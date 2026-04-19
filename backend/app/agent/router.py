"""
Agent Router — ưu tiên RAG trước, rồi mới fallback.

Logic mới:
  1. Nếu có tài liệu đã index thì luôn retrieve trước.
  2. Chấm độ mạnh của bằng chứng retrieve.
  3. Nếu chunk đủ mạnh → RAG.
  4. Nếu chunk yếu/không có → AI, nhưng vẫn mang theo support chunks để kiểm chứng mềm.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.evaluator import assess_retrieval, is_greeting_query
from app.config import settings
from app.rag.retriever import RetrievedChunk, retriever

logger = logging.getLogger(__name__)


class AnswerMode(str, Enum):
    RAG = "rag"
    AI = "ai"


class RouteDecision:
    def __init__(
        self,
        mode: AnswerMode,
        chunks: list[RetrievedChunk],
        reason: str = "",
        assessment: dict[str, Any] | None = None,
    ):
        self.mode = mode
        self.chunks = chunks
        self.reason = reason
        self.assessment = assessment or {}


class AgentRouter:
    async def route(
        self,
        query: str,
        db: AsyncSession,
        dataset_id: Optional[str] = None,
        force_mode: Optional[AnswerMode] = None,
    ) -> RouteDecision:
        if is_greeting_query(query):
            return RouteDecision(AnswerMode.AI, [], reason="greeting", assessment={"reason": "greeting", "confidence": "high"})

        has_docs = await self._has_indexed_documents(db)
        chunks: list[RetrievedChunk] = []

        if has_docs:
            chunks = await retriever.retrieve(query, db, dataset_id)

        assessment = assess_retrieval(query, chunks, mode_hint=force_mode or "auto")
        logger.debug("Route assessment: %s", assessment.to_dict())

        if force_mode == AnswerMode.RAG:
            if assessment.should_use_rag:
                return RouteDecision(AnswerMode.RAG, chunks, reason="forced_rag", assessment=assessment.to_dict())
            return RouteDecision(AnswerMode.AI, chunks, reason="forced_rag_but_weak_grounding", assessment=assessment.to_dict())

        if force_mode == AnswerMode.AI:
            return RouteDecision(AnswerMode.AI, chunks, reason="forced_ai", assessment=assessment.to_dict())

        if not has_docs:
            return RouteDecision(AnswerMode.AI, [], reason="no_documents", assessment=assessment.to_dict())

        if assessment.should_use_rag:
            return RouteDecision(AnswerMode.RAG, chunks, reason="rag_first_grounded", assessment=assessment.to_dict())

        return RouteDecision(AnswerMode.AI, chunks, reason=assessment.reason or "fallback_ai", assessment=assessment.to_dict())

    async def _has_indexed_documents(self, db: AsyncSession) -> bool:
        from sqlalchemy import func, select
        from app.models.db import Document

        result = await db.execute(
            select(func.count()).select_from(Document).where(Document.status == "ready")
        )
        return (result.scalar() or 0) > 0


agent_router = AgentRouter()
