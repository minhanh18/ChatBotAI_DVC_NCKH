"""
Ingestor — pipeline đầy đủ: extract → chunk → embed → lưu DB.
Được gọi từ Celery task sau khi upload file.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.db import AsyncSessionLocal, Document, DocumentSegment
from app.rag.chunker import RecursiveCharacterChunker
from app.rag.embedder import _is_rate_limited_error, embedding_service
from app.rag.extractor import ExtractorError, extract_text
from app.rag.legal_metadata import detect_legal_document_metadata
from app.rag.lifecycle import merge_meta

logger = logging.getLogger(__name__)

chunker = RecursiveCharacterChunker(
    chunk_size=settings.CHUNK_SIZE,
    chunk_overlap=settings.CHUNK_OVERLAP,
    separators=settings.CHUNK_SEPARATORS,
)


async def ingest_document(document_id: str) -> None:
    async with AsyncSessionLocal() as db:
        stmt = select(Document).where(Document.id == UUID(document_id))
        doc = (await db.execute(stmt)).scalar_one_or_none()
        if not doc:
            logger.error("Document %s không tìm thấy", document_id)
            return

        await _set_status(db, doc, "indexing")
        logger.info("Bắt đầu index document: %s (%s)", doc.name, document_id)

        try:
            raw_text = extract_text(doc.file_path)
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

            old_segs = await db.execute(select(DocumentSegment).where(DocumentSegment.document_id == doc.id))
            for seg in old_segs.scalars().all():
                await db.delete(seg)

            for chunk, emb in zip(chunks, embeddings):
                seg = DocumentSegment(
                    document_id=doc.id,
                    dataset_id=doc.dataset_id,
                    position=chunk.position,
                    content=chunk.content,
                    word_count=chunk.word_count,
                    embedding=emb,
                    meta=chunk.meta,
                )
                db.add(seg)

            legal_meta = detect_legal_document_metadata(doc.name, raw_text)
            doc.meta = merge_meta(doc.meta, {
                **legal_meta,
                "page_count": max(((chunk.meta or {}).get("page_end") or 0) for chunk in chunks) if chunks else 0,
                "chunk_hash_count": len(seen_chunk_hashes),
                "is_active_for_retrieval": True,
                "lifecycle_status": (doc.meta or {}).get("lifecycle_status") or "active",
            })
            doc.chunk_count = len(chunks)
            doc.status = "ready"
            doc.error_message = None
            await db.commit()

            logger.info("  ✓ Index xong: %d segments", len(chunks))

        except (ExtractorError, ValueError) as e:
            await _set_status(db, doc, "error", str(e))
            logger.error("  ✗ Lỗi index %s: %s", document_id, e)
        except Exception as e:
            if _is_rate_limited_error(e):
                await _set_status(db, doc, "pending", "Hệ thống embedding đang bận, sẽ tự thử lại.")
                logger.warning("  ↻ Embedding bị giới hạn tần suất cho %s, sẽ retry: %s", document_id, e)
                raise
            await _set_status(db, doc, "error", f"Lỗi không xác định: {e}")
            logger.exception("  ✗ Lỗi không xác định khi index %s", document_id)


async def _set_status(
    db: AsyncSession,
    doc: Document,
    status: str,
    error: str | None = None,
) -> None:
    doc.status = status
    doc.error_message = error
    await db.commit()
