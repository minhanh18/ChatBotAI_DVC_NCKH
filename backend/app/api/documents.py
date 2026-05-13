"""Documents API — upload, quản lý dataset và tài liệu."""

from __future__ import annotations

import asyncio
import mimetypes
import uuid
from datetime import datetime
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
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
from app.rag.ingestor import ingest_document
from app.utils.storage import delete_file, file_exists, load_file, save_file

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

    # Offload CPU-bound hash computation to thread pool to avoid blocking the event loop
    file_hash = await asyncio.to_thread(compute_file_hash, content)
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
    # Lưu file: Azure Blob Storage nếu USE_AZURE_STORAGE=True, ngược lại local disk
    await asyncio.to_thread(save_file, file_path, content)

    doc_meta = build_document_meta(
        file_hash=file_hash,
        normalized_name=normalized_name,
        version=next_version,
        source_url=source_url,
        previous_document_id=previous_doc_id,
    )
    doc_meta["uploaded_at"] = datetime.utcnow().isoformat()

    # Tìm đoạn này (khoảng dòng 132):
    # Khi USE_R2_STORAGE=True: file đã an toàn trên R2, KHÔNG lưu binary vào DB
    # để tránh double RAM usage và làm đầy PostgreSQL với file nhị phân lớn.
    # Khi USE_R2_STORAGE=False: vẫn lưu file_content làm backup (ephemeral disk).
    _file_content_for_db = None if getattr(settings, "USE_R2_STORAGE", False) else content

    doc = Document(
        id=UUID(doc_id),
        dataset_id=UUID(dataset_id),
        name=file.filename or f"document_{doc_id}",
        file_path=str(file_path),
        file_content=_file_content_for_db,
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

    # Commit metadata — nhanh, không giữ binary content trong transaction
    await db.commit()

    # Giải phóng RAM ngay: xóa reference đến bytes file trước khi spawn ingest task.
    # Quan trọng khi file lớn (10-50MB): tránh RAM spike gây OOM crash uvicorn worker.
    del content
    if "_file_content_for_db" in dir():
        del _file_content_for_db
    import gc as _gc
    _gc.collect()

    # Ingest chạy async background — không block response.
    # QUAN TRỌNG: asyncio.create_task chạy trong cùng event loop / process với uvicorn.
    # Nếu ingest nặng (PDF lớn, nhiều trang), worker sẽ bận và không nhận request mới.
    # Giải pháp lâu dài: dùng Celery worker riêng. Hiện tại: ingest được giới hạn RAM
    # qua streaming per-page (PyMuPDF) và INGEST_TIMEOUT_SECONDS=600.
    asyncio.create_task(ingest_document(doc_id))

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

    if doc.file_path and file_exists(doc.file_path):
        await asyncio.to_thread(delete_file, doc.file_path)

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


@router.api_route("/{document_id}/file", methods=["GET", "HEAD"])
async def serve_document_file(request: Request, document_id: str, db: AsyncSession = Depends(get_db)):
    doc = (await db.execute(
        select(Document).where(Document.id == UUID(document_id))
    )).scalar_one_or_none()
    
    if not doc:
        raise HTTPException(404, "Tài liệu không tồn tại")

    # --- BƯỚC 1: ƯU TIÊN LẤY DỮ LIỆU TỪ DATABASE (FILE_CONTENT) ---
    # Điều này giúp file luôn khả dụng kể cả khi Render xóa thư mục /tmp
    file_bytes = doc.file_content
    
    # --- BƯỚC 2: NẾU DB TRỐNG, MỚI TÌM TRÊN ĐĨA CỨNG (FALLBACK) ---
    if file_bytes is None:
        file_path = Path(doc.file_path) if doc.file_path else None
        # Logic tìm kiếm file trên disk (giữ lại để hỗ trợ tài liệu cũ)
        if not file_path or not file_exists(str(file_path)):
            for ext in settings.ALLOWED_EXTENSIONS:
                candidate = Path(settings.UPLOAD_DIR) / f"{document_id}.{ext}"
                if file_exists(str(candidate)):
                    file_path = candidate
                    break
        
        if file_path and file_exists(str(file_path)):
            # Đọc file từ disk thành bytes
            from app.utils.storage import load_file as _load_file
            file_bytes = await asyncio.to_thread(_load_file, str(file_path))

    # --- BƯỚC 3: TRẢ VỀ PHẢN HỒI ---
    if file_bytes:
        # Xác định định dạng file
        ft = (doc.file_type or '').lower().lstrip('.')
        media_type = {
            'pdf': 'application/pdf',
            'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'doc': 'application/msword',
            'txt': 'text/plain; charset=utf-8',
        }.get(ft) or 'application/octet-stream'

        # Xử lý HEAD request (chỉ lấy header)
        if request.method == "HEAD":
            from starlette.responses import Response
            return Response(
                status_code=200,
                headers={
                    "Content-Type": media_type,
                    "Content-Length": str(len(file_bytes)),
                    "Content-Disposition": "inline",
                    "Cache-Control": "private, max-age=3600",
                },
            )

        # Xử lý GET request (trả về nội dung file)
        from starlette.responses import Response as BytesResponse
        from urllib.parse import quote as _url_quote
        # RFC 5987: encode tên file UTF-8 để tránh UnicodeEncodeError với ký tự tiếng Việt
        _safe_filename = _url_quote(doc.name.encode("utf-8"), safe="")
        return BytesResponse(
            content=file_bytes,
            media_type=media_type,
            headers={
                "Content-Disposition": f"inline; filename*=UTF-8''{_safe_filename}",
                "Cache-Control": "private, max-age=3600",
                "X-Content-Type-Options": "nosniff",
            },
        )

    # Nếu cả DB và Disk đều không có file
    raise HTTPException(
        404,
        detail={
            "message": "Không tìm thấy nội dung file.",
            "hint": "Hãy upload lại tài liệu này để lưu trữ vào Database vĩnh viễn.",
        }
    )


@router.post("/backfill-file-content")
async def backfill_file_content(db: AsyncSession = Depends(get_db), _=Depends(verify_admin)):
    """
    Admin endpoint: đọc file từ disk và lưu vào cột file_content trong DB
    cho các tài liệu chưa có (upload trước khi có cột này).
    Gọi 1 lần sau khi deploy bản có cột file_content.
    """
    docs = (await db.execute(
        select(Document).where(Document.file_content.is_(None))
    )).scalars().all()

    updated = 0
    missing = 0
    for doc in docs:
        file_path = Path(doc.file_path) if doc.file_path else None
        # Thử tìm file trên disk
        if not file_path or not file_path.exists():
            for ext in settings.ALLOWED_EXTENSIONS:
                candidate = Path(settings.UPLOAD_DIR) / f"{doc.id}.{ext}"
                if candidate.exists():
                    file_path = candidate
                    break
        if file_path and file_path.exists():
            doc.file_content = file_path.read_bytes()
            updated += 1
        else:
            missing += 1

    await db.commit()
    return {
        "updated": updated,
        "missing_on_disk": missing,
        "message": f"Đã backfill {updated} tài liệu. {missing} tài liệu không còn file trên disk — cần upload lại."
    }


@router.post("/{document_id}/reindex")
async def reindex_document(document_id: str, db: AsyncSession = Depends(get_db), _=Depends(verify_admin)):
    doc = (await db.execute(select(Document).where(Document.id == UUID(document_id)))).scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document không tồn tại")

    doc.status = "pending"
    doc.error_message = None
    doc.meta = merge_meta(doc.meta, {"last_reindex_requested_at": datetime.utcnow().isoformat()})
    await db.commit()

    asyncio.create_task(ingest_document(document_id))
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