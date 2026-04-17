"""
Agent Router — điều hướng thông minh giữa 2 chế độ:
  • RAG  : trả lời dựa trên tài liệu nội bộ (semantic search + Gemini)
  • AI   : trả lời trực tiếp bằng Gemini (web knowledge / general chat)

Logic định tuyến (theo thứ tự ưu tiên):
  1. Dùng Gemini để phân tích ý định query (nếu API available)
  2. Fallback: keyword matching với INTERNAL_DOC_KEYWORDS trong config.py
  3. Nếu RAG được chọn nhưng không tìm thấy chunk đủ score → tự động
     chuyển sang AI mode và thông báo.
"""

from __future__ import annotations

import asyncio
import logging
from enum import Enum
from typing import Optional

import google.generativeai as genai
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.rag.retriever import RetrievedChunk, retriever

logger = logging.getLogger(__name__)

genai.configure(api_key=settings.GEMINI_API_KEY)


class AnswerMode(str, Enum):
    RAG = "rag"           # Trả lời từ tài liệu nội bộ
    AI = "ai"             # Trả lời từ Gemini (general AI)


class RouteDecision:
    def __init__(
        self,
        mode: AnswerMode,
        chunks: list[RetrievedChunk],
        reason: str = "",
    ):
        self.mode = mode
        self.chunks = chunks
        self.reason = reason


_ROUTER_SYSTEM_PROMPT = """Bạn là agent điều hướng. Nhiệm vụ duy nhất của bạn là phân loại câu hỏi của người dùng.

Trả lời CHỈ bằng một từ:
- "RAG" nếu câu hỏi hỏi về thông tin nội bộ, tài liệu, quy định, quy trình, hướng dẫn, chính sách công ty.
- "AI" nếu câu hỏi là kiến thức chung, lập trình, toán học, hoặc không liên quan tài liệu nội bộ.

Câu hỏi: {query}"""


class AgentRouter:
    async def route(
        self,
        query: str,
        db: AsyncSession,
        dataset_id: Optional[str] = None,
        force_mode: Optional[AnswerMode] = None,
        prefer_web: bool = False,
    ) -> RouteDecision:
        """
        Phân tích query và quyết định chế độ trả lời.
        Nếu force_mode được đặt, bỏ qua logic routing.
        """
        if force_mode:
            chunks = []
            if force_mode == AnswerMode.RAG:
                chunks = await retriever.retrieve(query, db, dataset_id)
            elif prefer_web:
                has_docs = await self._has_indexed_documents(db)
                chunks = await retriever.retrieve(query, db, dataset_id) if has_docs else []
            return RouteDecision(force_mode, chunks, reason="forced")

        # ── Bước 1: Web realtime chỉ chạy khi người dùng chủ động bật ──
        has_docs = await self._has_indexed_documents(db)

        if prefer_web:
            support_chunks = await retriever.retrieve(query, db, dataset_id) if has_docs else []
            return RouteDecision(AnswerMode.AI, support_chunks, reason="prefer_web")

        # ── Bước 2: Kiểm tra tài liệu có trong DB không ──────────────────
        if not has_docs:
            return RouteDecision(AnswerMode.AI, [], reason="no_documents")

        # ── Bước 3: LLM routing ───────────────────────────────────────────
        intent = await self._classify_intent(query)
        logger.debug("Router intent: %s for query: %s", intent, query[:50])

        if intent == AnswerMode.AI:
            return RouteDecision(AnswerMode.AI, [], reason="llm_router")

        # ── Bước 4: Thử retrieve chunks ───────────────────────────────────
        chunks = await retriever.retrieve(query, db, dataset_id)

        if not chunks:
            # Không tìm thấy tài liệu liên quan → fallback sang AI
            logger.info("Không có chunk phù hợp, chuyển sang AI mode")
            return RouteDecision(
                AnswerMode.AI, [],
                reason="no_relevant_chunks"
            )

        return RouteDecision(AnswerMode.RAG, chunks, reason="rag_found")

    # ── Private ───────────────────────────────────────────────────────────────

    async def _classify_intent(self, query: str) -> AnswerMode:
        """Dùng Gemini Flash để phân loại ý định."""
        try:
            model = genai.GenerativeModel(settings.GEMINI_MODEL)
            prompt = _ROUTER_SYSTEM_PROMPT.format(query=query)
            response = model.generate_content(
                prompt,
                generation_config=genai.GenerationConfig(
                    temperature=0,
                    max_output_tokens=5,
                ),
            )
            answer = _extract_response_text(response).strip().upper()
            if "RAG" in answer:
                return AnswerMode.RAG
            return AnswerMode.AI
        except Exception as e:
            logger.warning("LLM router thất bại, dùng keyword fallback: %s", e)
            return self._keyword_route(query)

    def _keyword_route(self, query: str) -> AnswerMode:
        """Keyword fallback khi LLM không khả dụng."""
        q_lower = query.lower()
        for kw in settings.INTERNAL_DOC_KEYWORDS:
            if kw.lower() in q_lower:
                return AnswerMode.RAG
        return AnswerMode.AI

    async def _has_indexed_documents(self, db: AsyncSession) -> bool:
        from sqlalchemy import select, func
        from app.models.db import Document
        result = await db.execute(
            select(func.count()).select_from(Document).where(Document.status == "ready")
        )
        return (result.scalar() or 0) > 0


def _extract_response_text(response) -> str:
    try:
        text = getattr(response, "text")
        if text:
            return str(text)
    except Exception:
        pass

    pieces: list[str] = []
    try:
        candidates = getattr(response, "candidates", None) or []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", None) or []
            for part in parts:
                part_text = getattr(part, "text", None)
                if part_text:
                    pieces.append(str(part_text))
    except Exception:
        return ""

    return "".join(pieces)


agent_router = AgentRouter()
