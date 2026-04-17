"""Documents API — upload, quản lý dataset và tài liệu."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import verify_admin
from app.config import settings
from app.models.db import Dataset, Document, DocumentSegment, get_db
from app.tasks.ingest import ingest_document_task

router = APIRouter(prefix="/documents", tags=["documents"])

Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)


# ══════════════════════════════════════════════════════════════════════════════
#  DATASET
# ══════════════════════════════════════════════════════════════════════════════

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
        # Đếm document
        doc_result = await db.execute(
            select(Document).where(Document.dataset_id == ds.id)
        )
        docs = doc_result.scalars().all()
        out.append({
            "id": str(ds.id),
            "name": ds.name,
            "description": ds.description,
            "document_count": len(docs),
            "ready_count": sum(1 for d in docs if d.status == "ready"),
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


# ══════════════════════════════════════════════════════════════════════════════
#  DOCUMENT UPLOAD & MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/datasets/{dataset_id}/upload")
async def upload_document(
    dataset_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_admin),
):
    """Upload tài liệu và bắt đầu indexing bất đồng bộ."""
    # Kiểm tra dataset tồn tại
    ds = (await db.execute(select(Dataset).where(Dataset.id == UUID(dataset_id)))).scalar_one_or_none()
    if not ds:
        raise HTTPException(404, "Dataset không tồn tại")

    # Kiểm tra định dạng
    ext = Path(file.filename or "").suffix.lower().lstrip(".")
    if ext not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Định dạng không hỗ trợ. Cho phép: {settings.ALLOWED_EXTENSIONS}")

    # Kiểm tra kích thước
    content = await file.read()
    size_mb = len(content) / (1024 * 1024)
    if size_mb > settings.MAX_UPLOAD_SIZE_MB:
        raise HTTPException(400, f"File quá lớn. Tối đa {settings.MAX_UPLOAD_SIZE_MB}MB")

    # Lưu file
    doc_id = str(uuid.uuid4())
    file_path = Path(settings.UPLOAD_DIR) / f"{doc_id}.{ext}"
    file_path.write_bytes(content)

    # Tạo Document record
    doc = Document(
        id=UUID(doc_id),
        dataset_id=UUID(dataset_id),
        name=file.filename or f"document_{doc_id}",
        file_path=str(file_path),
        file_type=ext,
        file_size=len(content),
        status="pending",
        meta={"uploaded_at": __import__("datetime").datetime.utcnow().isoformat(), "version": 1},
    )
    db.add(doc)
    await db.commit()

    # Đẩy vào Celery queue
    ingest_document_task.delay(doc_id)

    return {
        "id": doc_id,
        "name": doc.name,
        "status": "pending",
        "message": "Tài liệu đang được xử lý. Sẽ sẵn sàng trong vài giây.",
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
            "created_at": d.created_at.isoformat(),
        }
        for d in docs
    ]


@router.get("/documents/{document_id}")
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
    }




@router.patch("/documents/{document_id}")
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
    await db.commit()
    return {"message": "Đã cập nhật tên tài liệu", "id": str(doc.id), "name": doc.name}


@router.delete("/documents/{document_id}")
async def delete_document(document_id: str, db: AsyncSession = Depends(get_db), _=Depends(verify_admin)):
    doc = (await db.execute(select(Document).where(Document.id == UUID(document_id)))).scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document không tồn tại")

    # Xoá file vật lý
    if doc.file_path and Path(doc.file_path).exists():
        Path(doc.file_path).unlink()

    await db.delete(doc)
    await db.commit()
    return {"message": "Đã xoá tài liệu"}


@router.post("/documents/{document_id}/reindex")
async def reindex_document(document_id: str, db: AsyncSession = Depends(get_db), _=Depends(verify_admin)):
    """Re-index lại tài liệu (dùng khi thay đổi config chunking)."""
    doc = (await db.execute(select(Document).where(Document.id == UUID(document_id)))).scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document không tồn tại")

    doc.status = "pending"
    doc.error_message = None
    await db.commit()

    ingest_document_task.delay(document_id)
    return {"message": "Đã bắt đầu re-index"}


@router.get("/documents/{document_id}/segments")
async def list_segments(document_id: str, db: AsyncSession = Depends(get_db), _=Depends(verify_admin)):
    """Xem các chunk đã được tạo (để debug chunking)."""
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
        }
        for s in segs
    ]
