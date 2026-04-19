"""
Retriever — tìm kiếm semantic trong pgvector.
Trả về các chunk liên quan kèm metadata để tạo citations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.db import Document, DocumentSegment
from app.rag.embedder import embedding_service

logger = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    segment_id: str
    document_id: str
    document_name: str
    content: str
    score: float
    position: int
    document_meta: dict | None = None
    segment_meta: dict | None = None


class VectorRetriever:
    def __init__(
        self,
        top_k: int = settings.RETRIEVAL_TOP_K,
        score_threshold: float = settings.RETRIEVAL_SCORE_THRESHOLD,
    ):
        self.top_k = top_k
        self.score_threshold = score_threshold

    async def retrieve(
        self,
        query: str,
        db: AsyncSession,
        dataset_id: Optional[str] = None,
    ) -> list[RetrievedChunk]:
        """
        Tìm kiếm semantic cho câu truy vấn.
        Nếu dataset_id được cung cấp thì giới hạn trong dataset đó.
        """
        # Embed query
        query_vector = await embedding_service.embed_query(query)

        # Cosine similarity query với pgvector
        # pgvector dùng <=> cho cosine distance (1 - similarity)
        distance_expr = DocumentSegment.embedding.cosine_distance(query_vector)

        stmt = (
            select(
                DocumentSegment.id,
                DocumentSegment.document_id,
                DocumentSegment.content,
                DocumentSegment.position,
                DocumentSegment.dataset_id,
                Document.name.label("document_name"),
                Document.meta.label("document_meta"),
                DocumentSegment.meta.label("segment_meta"),
                (1 - distance_expr).label("score"),
            )
            .join(Document, DocumentSegment.document_id == Document.id)
            .where(DocumentSegment.embedding.is_not(None))
            .where(Document.status == "ready")
            .order_by(distance_expr)
            .limit(self.top_k * 8)  # Lấy dư nhiều hơn để loại bỏ bản deprecated/trùng lặp
        )

        if dataset_id:
            stmt = stmt.where(DocumentSegment.dataset_id == UUID(dataset_id))

        result = await db.execute(stmt)
        rows = result.fetchall()

        chunks: list[RetrievedChunk] = []
        seen_content_hashes: set[str] = set()
        for row in rows:
            score = float(row.score)
            if score < self.score_threshold:
                continue

            document_meta = row.document_meta or {}
            lifecycle_status = str(document_meta.get("lifecycle_status") or "active").lower()
            if lifecycle_status in {"deprecated", "archived"}:
                continue
            if document_meta.get("is_active_for_retrieval") is False:
                continue

            segment_meta = row.segment_meta or {}
            content_hash = str(segment_meta.get("content_hash") or "").strip()
            if content_hash and content_hash in seen_content_hashes:
                continue
            if content_hash:
                seen_content_hashes.add(content_hash)

            chunks.append(
                RetrievedChunk(
                    segment_id=str(row.id),
                    document_id=str(row.document_id),
                    document_name=row.document_name,
                    content=row.content,
                    score=score,
                    position=row.position,
                    document_meta=document_meta,
                    segment_meta=segment_meta,
                )
            )

            if len(chunks) >= self.top_k:
                break

        return chunks


retriever = VectorRetriever()
