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
                (1 - distance_expr).label("score"),
            )
            .join(Document, DocumentSegment.document_id == Document.id)
            .where(DocumentSegment.embedding.is_not(None))
            .where(Document.status == "ready")
            .order_by(distance_expr)
            .limit(self.top_k * 2)  # Lấy dư, rồi lọc theo score
        )

        if dataset_id:
            stmt = stmt.where(DocumentSegment.dataset_id == UUID(dataset_id))

        result = await db.execute(stmt)
        rows = result.fetchall()

        chunks: list[RetrievedChunk] = []
        for row in rows:
            score = float(row.score)
            if score >= self.score_threshold:
                chunks.append(
                    RetrievedChunk(
                        segment_id=str(row.id),
                        document_id=str(row.document_id),
                        document_name=row.document_name,
                        content=row.content,
                        score=score,
                        position=row.position,
                    )
                )

        # Trả về top_k chunk tốt nhất
        return chunks[: self.top_k]


retriever = VectorRetriever()
