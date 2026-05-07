"""
Ingestor — pipeline đầy đủ: extract → chunk → embed → lưu DB.
Được gọi từ background task sau khi upload file.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.db import AsyncSessionLocal, Document, DocumentSegment
from app.rag.chunker import LegalAwareChunker
from app.rag.embedder import _is_rate_limited_error, embedding_service
from app.rag.extractor import ExtractorError, extract_text
from app.rag.legal_metadata import detect_legal_document_metadata
from app.rag.lifecycle import merge_meta

logger = logging.getLogger(__name__)

chunker = LegalAwareChunker(
    chunk_size=settings.CHUNK_SIZE,
    chunk_overlap=settings.CHUNK_OVERLAP,
)


INGEST_TIMEOUT_SECONDS = 600  # 10 phút — timeout cứng để tránh treo mãi mãi


async def ingest_document(document_id: str) -> None:
    try:
        await asyncio.wait_for(_ingest_document_inner(document_id), timeout=INGEST_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.error("  ✗ ingest_document %s timeout sau %ds", document_id, INGEST_TIMEOUT_SECONDS)
        async with AsyncSessionLocal() as db:
            doc = (await db.execute(select(Document).where(Document.id == UUID(document_id)))).scalar_one_or_none()
            if doc and doc.status in ("indexing", "pending"):
                await _set_status(db, doc, "error", f"Xử lý quá thời gian ({INGEST_TIMEOUT_SECONDS}s). Vui lòng thử reindex lại.")


async def _ingest_document_inner(document_id: str) -> None:
    # ── Bước 1: Đọc doc info, set status=indexing, rồi ĐÓNG connection ──────
    file_path: str | None = None
    doc_name: str = ""
    doc_dataset_id = None
    doc_meta: dict = {}

    async with AsyncSessionLocal() as db:
        stmt = select(Document).where(Document.id == UUID(document_id))
        doc = (await db.execute(stmt)).scalar_one_or_none()
        if not doc:
            logger.error("Document %s không tìm thấy", document_id)
            return

        # Nếu file_path không tồn tại trên disk, thử lấy từ file_content trong DB
        import os
        from pathlib import Path as _Path

        file_path = doc.file_path
        if not file_path or not _Path(file_path).exists():
            # Khôi phục file từ DB bytes (ephemeral filesystem đã mất file)
            if doc.file_content:
                ext = doc.file_type or "bin"
                restored_path = _Path(settings.UPLOAD_DIR) / f"{document_id}.{ext}"
                restored_path.parent.mkdir(parents=True, exist_ok=True)
                restored_path.write_bytes(doc.file_content)
                file_path = str(restored_path)
                doc.file_path = file_path
                logger.info("  ↻ Đã khôi phục file từ DB bytes: %s", restored_path)
            else:
                await _set_status(db, doc, "error", "File không còn trên server và không có backup trong DB. Vui lòng upload lại.")
                return

        doc_name = doc.name
        doc_dataset_id = doc.dataset_id
        doc_meta = dict(doc.meta or {})
        await _set_status(db, doc, "indexing")
        logger.info("Bắt đầu index document: %s (%s)", doc_name, document_id)
    # DB connection được giải phóng tại đây — trước khi extract + embed

    # ── Bước 2: Extract + chunk + embed (KHÔNG giữ DB connection) ────────────
    try:
        loop = asyncio.get_event_loop()
        raw_text = await loop.run_in_executor(None, extract_text, file_path)
        if not raw_text.strip():
            raise ValueError("File không có nội dung văn bản")

        chunks = chunker.split(raw_text)
        logger.info("  → %d chunks từ %d ký tự", len(chunks), len(raw_text))

        deduped_chunks = []
        seen_chunk_hashes: set[str] = set()
        for chunk in chunks:
            content_hash = (chunk.meta or {}).get("content_hash")
            if content_hash and content_hash in seen_chunk_hashes:
                continue
            if content_hash:
                seen_chunk_hashes.add(content_hash)
            deduped_chunks.append(chunk)
        chunks = deduped_chunks

        texts = [c.content for c in chunks]
        embeddings = await embedding_service.embed_texts(texts)
        # embed_texts có thể mất vài chục giây — KHÔNG giữ DB connection trong lúc này

    except (ExtractorError, ValueError) as e:
        async with AsyncSessionLocal() as db:
            doc = (await db.execute(select(Document).where(Document.id == UUID(document_id)))).scalar_one_or_none()
            if doc:
                await _set_status(db, doc, "error", str(e))
        logger.error("  ✗ Lỗi index %s: %s", document_id, e)
        return
    except Exception as e:
        if _is_rate_limited_error(e):
            async with AsyncSessionLocal() as db:
                doc = (await db.execute(select(Document).where(Document.id == UUID(document_id)))).scalar_one_or_none()
                if doc:
                    await _set_status(db, doc, "pending", "Hệ thống embedding đang bận, sẽ tự thử lại.")
            logger.warning("  ↻ Embedding bị giới hạn tần suất cho %s: %s", document_id, e)
            raise
        async with AsyncSessionLocal() as db:
            doc = (await db.execute(select(Document).where(Document.id == UUID(document_id)))).scalar_one_or_none()
            if doc:
                await _set_status(db, doc, "error", f"Lỗi không xác định: {e}")
        logger.exception("  ✗ Lỗi không xác định khi index %s", document_id)
        return

    # ── Bước 3: Lưu segments + cập nhật status → mở connection mới ──────────
    async with AsyncSessionLocal() as db:
        doc = (await db.execute(select(Document).where(Document.id == UUID(document_id)))).scalar_one_or_none()
        if not doc:
            logger.error("Document %s biến mất sau khi embed", document_id)
            return

        old_segs = await db.execute(select(DocumentSegment).where(DocumentSegment.document_id == doc.id))
        for seg in old_segs.scalars().all():
            await db.delete(seg)

        for chunk, emb in zip(chunks, embeddings):
            seg = DocumentSegment(
                document_id=doc.id,
                dataset_id=doc_dataset_id,
                position=chunk.position,
                content=chunk.content,
                word_count=chunk.word_count,
                embedding=emb,
                meta=chunk.meta,
            )
            db.add(seg)

        legal_meta = detect_legal_document_metadata(doc_name, raw_text)
        doc.meta = merge_meta(doc.meta, {
            **legal_meta,
            "page_count": max(((chunk.meta or {}).get("page_end") or 0) for chunk in chunks) if chunks else 0,
            "chunk_hash_count": len(seen_chunk_hashes),
            "is_active_for_retrieval": True,
            "lifecycle_status": doc_meta.get("lifecycle_status") or "active",
        })
        doc.chunk_count = len(chunks)
        doc.status = "ready"
        doc.error_message = None
        await db.commit()

    # Invalidate BM25 cache cho dataset này
    try:
        from app.rag.retriever import retriever as _retriever
        _retriever.invalidate_cache(str(doc_dataset_id) if doc_dataset_id else None)
    except Exception:
        pass

    logger.info("  ✓ Index xong: %d segments", len(chunks))


async def _set_status(
    db: AsyncSession,
    doc: Document,
    status: str,
    error: str | None = None,
) -> None:
    doc.status = status
    doc.error_message = error
    await db.commit()
