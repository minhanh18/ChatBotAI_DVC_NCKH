"""
Agent Router — RAG-first bắt buộc.

Nguyên tắc:
  1. Với mọi câu hỏi không phải lời chào, nếu hệ thống có tài liệu đã index thì luôn truy xuất RAG trước.
  2. Nếu retriever tìm được chunk, engine sẽ sinh câu trả lời bằng RAG trước.
  3. Nếu RAG không trả lời được / không đủ căn cứ, engine mới fallback sang web search.
  4. Không ép thẳng sang AI/search chỉ vì câu hỏi có vẻ cần cập nhật; việc search là tầng fallback sau RAG.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.evaluator import assess_retrieval, is_greeting_query, is_chitchat_query
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
        """Định tuyến phản hồi theo chính sách RAG-first.

        `force_mode=AI` không được dùng để bỏ qua RAG đối với câu hỏi thường,
        vì yêu cầu của hệ thống là luôn kiểm tra nguồn nội bộ trước. Ngoại lệ:
        endpoint ảnh đã tạo decision AI riêng, không đi qua router này.
        """
        if is_greeting_query(query) or is_chitchat_query(query):
            reason = "chitchat" if is_chitchat_query(query) else "greeting"
            return RouteDecision(
                AnswerMode.AI,
                [],
                reason=reason,
                assessment={"reason": reason, "confidence": "high", "rag_first_checked": False},
            )

        # Expand viết tắt trước khi retrieve để RAG tìm đúng chunk
        import re as _re
        _ABBR_MAP = {
            r'\bđk\b':   'đăng ký',
            r'\bhk\b':   'hộ khẩu',
            r'\bcccd\b': 'căn cước công dân',
            r'\bcmnd\b': 'chứng minh nhân dân',
            r'\bcmt\b':  'chứng minh thư',
            r'\bubnd\b': 'uỷ ban nhân dân',
            r'\bqđ\b':   'quyết định',
        }
        _q = query
        for _pat, _rep in _ABBR_MAP.items():
            _q = _re.sub(_pat, _rep, _q, flags=_re.IGNORECASE)
        if _q != query:
            logger.info("Router query normalized: %r → %r", query, _q)
            query = _q

        has_docs = await self._has_indexed_documents(db, dataset_id=dataset_id)
        chunks: list[RetrievedChunk] = []

        if has_docs:
            # Luôn retrieve trước, kể cả khi UI/backend có gợi ý mode AI.
            chunks = await retriever.retrieve(query, db, dataset_id)

        assessment_obj = assess_retrieval(query, chunks, mode_hint=force_mode or "auto")
        assessment = assessment_obj.to_dict()
        assessment["rag_first_checked"] = bool(has_docs)
        assessment["force_mode_requested"] = getattr(force_mode, "value", force_mode)
        logger.info("Route assessment: %s", assessment)

        if not has_docs:
            return RouteDecision(
                AnswerMode.AI,
                [],
                reason="no_indexed_documents_after_rag_check",
                assessment=assessment,
            )

        if chunks:
            # Quan trọng: mọi chunk được retriever trả về đều phải được thử RAG trước.
            # Nếu model không trả lời được từ context, engine sẽ fallback web.
            assessment["should_use_rag"] = True
            assessment["should_force_web"] = False
            assessment["should_refuse_precise"] = False
            return RouteDecision(
                AnswerMode.RAG,
                chunks,
                reason="rag_first_always_try",
                assessment=assessment,
            )

        # Có tài liệu nhưng không truy xuất được chunk nào: đã kiểm tra RAG, mới cho search/AI.
        assessment["should_use_rag"] = False
        assessment["should_force_web"] = True
        return RouteDecision(
            AnswerMode.AI,
            [],
            reason="rag_checked_no_chunks_then_web",
            assessment=assessment,
        )

    async def _has_indexed_documents(self, db: AsyncSession, dataset_id: Optional[str] = None) -> bool:
        from sqlalchemy import func, select
        from uuid import UUID
        from app.models.db import Document

        stmt = select(func.count()).select_from(Document).where(Document.status == "ready")
        if dataset_id:
            try:
                stmt = stmt.where(Document.dataset_id == UUID(dataset_id))
            except Exception:
                return False
        result = await db.execute(stmt)
        return (result.scalar() or 0) > 0


agent_router = AgentRouter()