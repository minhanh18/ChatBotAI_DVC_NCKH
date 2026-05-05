"""Documents API — upload, quản lý dataset và tài liệu."""

from __future__ import annotations

import mimetypes
import uuid
from datetime import datetime
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import verify_admin
from app.config import settings
from app.models.db import Dataset, Document, DocumentSegment, get_db
from app.rag.lifecycle import (
    build_document_meta,
    compute_file_hash,
    find_duplicate_document,
    find_latest_same_name_document,
    lifecycle_status,
    merge_meta,
    normalize_document_name,
    version_of,
)
from app.rag.source_hints import resolve_document_source_url
from app.tasks.ingest import ingest_document_task

router = APIRouter(prefix="/documents", tags=["documents"])
Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)


@router.post("/datasets")
async def create_dataset(name: str = Form(...), description: str = Form(""), db: AsyncSession = Depends(get_db), _=Depends(verify_admin)):
    ds = Dataset(name=name, description=description)
    db.add(ds)
    await db.commit()
    await db.refresh(ds)
    return {"id": str(ds.id), "name": ds.name, "description": ds.description}


@router.get("/datasets")
async def list_datasets(db: AsyncSession = Depends(get_db), _=Depends(verify_admin)):
    result = await db.execute(select(Dataset).order_by(Dataset.created_at.desc()))
    datasets = result.scalars().all()
    out = []
    for ds in datasets:
        doc_result = await db.execute(select(Document).where(Document.dataset_id == ds.id))
        docs = doc_result.scalars().all()
        out.append({
            "id": str(ds.id),
            "name": ds.name,
            "description": ds.description,
            "document_count": len(docs),
            "ready_count": sum(1 for d in docs if d.status == "ready"),
            "active_count": sum(1 for d in docs if lifecycle_status(d.meta) == "active"),
            "created_at": ds.created_at.isoformat(),
        })
    return out


@router.delete("/datasets/{dataset_id}")
async def delete_dataset(dataset_id: str, db: AsyncSession = Depends(get_db), _=Depends(verify_admin)):
    ds = (await db.execute(select(Dataset).where(Dataset.id == UUID(dataset_id)))).scalar_one_or_none()
    if not ds:
        raise HTTPException(404, "Dataset không tồn tại")
    await db.delete(ds)
    await db.commit()
    return {"message": "Đã xoá dataset"}


@router.post("/datasets/{dataset_id}/upload")
async def upload_document(
    dataset_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_admin),
):
    ds = (await db.execute(select(Dataset).where(Dataset.id == UUID(dataset_id)))).scalar_one_or_none()
    if not ds:
        raise HTTPException(404, "Dataset không tồn tại")

    ext = Path(file.filename or "").suffix.lower().lstrip(".")
    if ext not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Định dạng không hỗ trợ. Cho phép: {settings.ALLOWED_EXTENSIONS}")

    content = await file.read()
    size_mb = len(content) / (1024 * 1024)
    if size_mb > settings.MAX_UPLOAD_SIZE_MB:
        raise HTTPException(400, f"File quá lớn. Tối đa {settings.MAX_UPLOAD_SIZE_MB}MB")

    file_hash = compute_file_hash(content)
    normalized_name = normalize_document_name(file.filename or "")
    source_url = resolve_document_source_url(file.filename or "", {})

    existing_docs = (
        await db.execute(
            select(Document)
            .where(Document.dataset_id == UUID(dataset_id))
            .order_by(Document.created_at.desc())
        )
    ).scalars().all()

    duplicate_doc = find_duplicate_document(existing_docs, file_hash=file_hash)
    if duplicate_doc:
        return {
            "id": str(duplicate_doc.id),
            "name": duplicate_doc.name,
            "status": duplicate_doc.status,
            "duplicate_of": str(duplicate_doc.id),
            "message": "Tài liệu trùng nội dung đã tồn tại, hệ thống không index lặp lại.",
        }

    previous_doc = find_latest_same_name_document(existing_docs, normalized_name=normalized_name)
    previous_doc_id = str(previous_doc.id) if previous_doc else None
    next_version = (version_of(previous_doc.meta) + 1) if previous_doc else 1

    doc_id = str(uuid.uuid4())
    file_path = Path(settings.UPLOAD_DIR) / f"{doc_id}.{ext}"
    file_path.write_bytes(content)

    doc_meta = build_document_meta(
        file_hash=file_hash,
        normalized_name=normalized_name,
        version=next_version,
        source_url=source_url,
        previous_document_id=previous_doc_id,
    )
    doc_meta["uploaded_at"] = datetime.utcnow().isoformat()

    doc = Document(
        id=UUID(doc_id),
        dataset_id=UUID(dataset_id),
        name=file.filename or f"document_{doc_id}",
        file_path=str(file_path),
        file_type=ext,
        file_size=len(content),
        status="pending",
        meta=doc_meta,
    )
    db.add(doc)

    if previous_doc and lifecycle_status(previous_doc.meta) == "active":
        prev_meta = merge_meta(previous_doc.meta, {
            "lifecycle_status": "deprecated",
            "is_active_for_retrieval": False,
            "superseded_by_document_id": doc_id,
        })
        previous_doc.meta = prev_meta

    await db.commit()
    ingest_document_task.delay(doc_id)

    return {
        "id": doc_id,
        "name": doc.name,
        "status": "pending",
        "version": next_version,
        "supersedes_document_id": previous_doc_id,
        "message": "Tài liệu đang được xử lý. Phiên bản mới sẽ được ưu tiên cho RAG khi index xong.",
    }


@router.get("/datasets/{dataset_id}/documents")
async def list_documents(dataset_id: str, db: AsyncSession = Depends(get_db), _=Depends(verify_admin)):
    result = await db.execute(
        select(Document)
        .where(Document.dataset_id == UUID(dataset_id))
        .order_by(Document.created_at.desc())
    )
    docs = result.scalars().all()
    return [
        {
            "id": str(d.id),
            "name": d.name,
            "file_type": d.file_type,
            "file_size": d.file_size,
            "status": d.status,
            "chunk_count": d.chunk_count,
            "error_message": d.error_message,
            "version": version_of(d.meta),
            "lifecycle_status": lifecycle_status(d.meta),
            "is_active_for_retrieval": bool((d.meta or {}).get("is_active_for_retrieval", True)),
            "source_url": (d.meta or {}).get("source_url"),
            "document_number": (d.meta or {}).get("document_number"),
            "document_type": (d.meta or {}).get("document_type"),
            "created_at": d.created_at.isoformat(),
        }
        for d in docs
    ]


@router.get("/{document_id}")
async def get_document(document_id: str, db: AsyncSession = Depends(get_db), _=Depends(verify_admin)):
    doc = (await db.execute(select(Document).where(Document.id == UUID(document_id)))).scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document không tồn tại")
    return {
        "id": str(doc.id),
        "name": doc.name,
        "status": doc.status,
        "chunk_count": doc.chunk_count,
        "error_message": doc.error_message,
        "meta": doc.meta or {},
    }


@router.patch("/{document_id}")
async def rename_document(
    document_id: str,
    name: str = Form(...),
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_admin),
):
    doc = (await db.execute(select(Document).where(Document.id == UUID(document_id)))).scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document không tồn tại")
    clean_name = (name or "").strip()
    if not clean_name:
        raise HTTPException(400, "Tên tài liệu không được để trống")
    doc.name = clean_name
    doc.meta = merge_meta(doc.meta, {"normalized_name": normalize_document_name(clean_name)})
    await db.commit()
    return {"message": "Đã cập nhật tên tài liệu", "id": str(doc.id), "name": doc.name}


@router.post("/{document_id}/activate")
async def activate_document(document_id: str, db: AsyncSession = Depends(get_db), _=Depends(verify_admin)):
    doc = (await db.execute(select(Document).where(Document.id == UUID(document_id)))).scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document không tồn tại")

    normalized_name = (doc.meta or {}).get("normalized_name") or normalize_document_name(doc.name)
    siblings = (
        await db.execute(select(Document).where(Document.dataset_id == doc.dataset_id))
    ).scalars().all()
    for sibling in siblings:
        meta = sibling.meta or {}
        if (meta.get("normalized_name") or normalize_document_name(sibling.name)) != normalized_name:
            continue
        if sibling.id == doc.id:
            sibling.meta = merge_meta(meta, {"lifecycle_status": "active", "is_active_for_retrieval": True})
        else:
            sibling.meta = merge_meta(meta, {"lifecycle_status": "deprecated", "is_active_for_retrieval": False})
    await db.commit()
    return {"message": "Đã kích hoạt phiên bản tài liệu này cho RAG."}


@router.post("/{document_id}/deprecate")
async def deprecate_document(document_id: str, db: AsyncSession = Depends(get_db), _=Depends(verify_admin)):
    doc = (await db.execute(select(Document).where(Document.id == UUID(document_id)))).scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document không tồn tại")
    doc.meta = merge_meta(doc.meta, {"lifecycle_status": "deprecated", "is_active_for_retrieval": False})
    await db.commit()
    return {"message": "Đã đánh dấu tài liệu là deprecated."}


@router.delete("/{document_id}")
async def delete_document(document_id: str, db: AsyncSession = Depends(get_db), _=Depends(verify_admin)):
    doc = (await db.execute(select(Document).where(Document.id == UUID(document_id)))).scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document không tồn tại")

    was_active = lifecycle_status(doc.meta) == "active"
    normalized_name = (doc.meta or {}).get("normalized_name") or normalize_document_name(doc.name)
    dataset_id = doc.dataset_id

    if doc.file_path and Path(doc.file_path).exists():
        Path(doc.file_path).unlink()

    await db.delete(doc)
    await db.flush()

    if was_active:
        siblings = (
            await db.execute(select(Document).where(Document.dataset_id == dataset_id))
        ).scalars().all()
        candidates = [s for s in siblings if ((s.meta or {}).get("normalized_name") or normalize_document_name(s.name)) == normalized_name]
        if candidates:
            candidates.sort(key=lambda item: version_of(item.meta), reverse=True)
            latest = candidates[0]
            latest.meta = merge_meta(latest.meta, {"lifecycle_status": "active", "is_active_for_retrieval": True})

    await db.commit()
    return {"message": "Đã xoá tài liệu"}


@router.get("/{document_id}/file")
async def serve_document_file(
    document_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Serve file tài liệu gốc để xem trong trình duyệt.
    Hỗ trợ PDF fragment #page=N để mở đúng trang.
    Không yêu cầu xác thực admin để iframe trong chatbot có thể load được.
    """
    doc = (await db.execute(
        select(Document).where(Document.id == UUID(document_id))
    )).scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Tài liệu không tồn tại")

    file_path = Path(doc.file_path) if doc.file_path else None
    if not file_path or not file_path.exists():
        # Fallback: thử tìm theo UPLOAD_DIR + doc_id
        for ext in settings.ALLOWED_EXTENSIONS:
            candidate = Path(settings.UPLOAD_DIR) / f"{document_id}.{ext}"
            if candidate.exists():
                file_path = candidate
                break

    if not file_path or not file_path.exists():
        raise HTTPException(404, "File không tồn tại trên server")

    media_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=doc.name,
        headers={
            "Content-Disposition": "inline",                          # render trong browser
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/{document_id}/reindex")
async def reindex_document(document_id: str, db: AsyncSession = Depends(get_db), _=Depends(verify_admin)):
    doc = (await db.execute(select(Document).where(Document.id == UUID(document_id)))).scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document không tồn tại")

    doc.status = "pending"
    doc.error_message = None
    doc.meta = merge_meta(doc.meta, {"last_reindex_requested_at": datetime.utcnow().isoformat()})
    await db.commit()

    ingest_document_task.delay(document_id)
    return {"message": "Đã bắt đầu re-index có kiểm soát."}


@router.get("/{document_id}/segments")
async def list_segments(document_id: str, db: AsyncSession = Depends(get_db), _=Depends(verify_admin)):
    result = await db.execute(
        select(DocumentSegment)
        .where(DocumentSegment.document_id == UUID(document_id))
        .order_by(DocumentSegment.position)
    )
    segs = result.scalars().all()
    return [
        {
            "id": str(s.id),
            "position": s.position,
            "content": s.content[:200] + ("…" if len(s.content) > 200 else ""),
            "word_count": s.word_count,
            "has_embedding": s.embedding is not None,
            "page_start": (s.meta or {}).get("page_start"),
            "page_end": (s.meta or {}).get("page_end"),
            "location_label": (s.meta or {}).get("location_label"),
            "article_ref": (s.meta or {}).get("article_ref"),
            "clause_ref": (s.meta or {}).get("clause_ref"),
            "legal_topic": (s.meta or {}).get("legal_topic"),
        }
        for s in segs
    ]
