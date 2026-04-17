"""
Chat Engine — lõi sinh câu trả lời với streaming.
Hỗ trợ 2 chế độ: RAG (tài liệu nội bộ) và AI (Gemini direct / multimodal).
Trả về SSE stream để frontend render real-time.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import re
import threading
import time
from collections.abc import AsyncGenerator
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import google.generativeai as genai
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.router import AnswerMode, RouteDecision
from app.config import settings
from app.models.db import Conversation, Message, UsageLog
from app.rag.retriever import RetrievedChunk
from app.web.live_search import is_time_sensitive_query, maybe_fetch_web_context

logger = logging.getLogger(__name__)
genai.configure(api_key=settings.GEMINI_API_KEY)

# ── Prompt templates ──────────────────────────────────────────────────────────

_RAG_SYSTEM_PROMPT = """Bạn là trợ lý AI chuyên trả lời câu hỏi dựa trên tài liệu nội bộ.

## Thời gian hệ thống nội bộ
{current_datetime}

## Hướng dẫn chung
- Chỉ trả lời dựa trên ngữ cảnh được cung cấp bên dưới.
- Dùng mốc thời gian hệ thống ở trên để hiểu các cụm như hôm nay, hiện nay, hiện tại, ngày này, thời điểm này.
- Không được tự in nguyên văn dòng thời gian hệ thống ở trên ra câu trả lời, trừ khi người dùng hỏi trực tiếp về ngày giờ hiện tại.
- Nếu không tìm thấy thông tin trong ngữ cảnh, hãy nói thẳng rằng tài liệu không đề cập đến vấn đề này.
- Trích dẫn nguồn tài liệu cụ thể bằng cú pháp [Tài liệu: <tên>] sau mỗi thông tin quan trọng.
- Trả lời bằng Markdown với tiêu đề, danh sách khi phù hợp.
- Không bịa đặt thông tin ngoài ngữ cảnh.

## Yêu cầu mở rộng theo lĩnh vực
{domain_instructions}

## Ngữ cảnh từ tài liệu nội bộ
{context}
"""

_AI_SYSTEM_PROMPT = """Bạn là trợ lý AI thông minh, trả lời bằng tiếng Việt (hoặc ngôn ngữ người dùng dùng).

## Thời gian hệ thống nội bộ
{current_datetime}

## Hướng dẫn chung
- Trả lời chính xác, hữu ích và đầy đủ vừa phải; không vì ngắn gọn mà làm mất ý quan trọng.
- Với câu hỏi cần thông tin mới, ưu tiên nguồn web có ngày gần nhất và nêu rõ thời điểm cập nhật.
- Nếu câu hỏi chứa các ý như mới nhất, hiện nay, hiệu lực, sửa đổi, bổ sung, xử phạt, lệ phí, giá cả hôm nay, thời gian thực, phải ưu tiên dữ liệu mới hơn dữ liệu cũ.
- Nếu có nhiều nguồn và một nguồn mới hơn làm thay đổi nội dung nguồn cũ, dùng nguồn mới hơn và nói rõ điểm thay đổi, không được trộn lẫn hai mốc thời gian.
- Luôn bám đúng đối tượng người dùng hỏi; không lấy nhầm số liệu của mục lân cận hoặc chủ thể gần nghĩa.
- Dùng mốc thời gian hệ thống ở trên để hiểu các cụm như hôm nay, hiện nay, hiện tại, ngày này, thời điểm này.
- Không được tự in nguyên văn dòng thời gian hệ thống ở trên ra câu trả lời, trừ khi người dùng hỏi trực tiếp về ngày giờ hiện tại.
- Nếu người dùng gửi hình ảnh, hãy phân tích đúng nội dung nhìn thấy trong hình rồi mới trả lời.
- Dùng Markdown khi phù hợp.
- Nếu là câu hỏi lập trình, luôn có code example.
- Nếu không biết, nói thẳng thay vì đoán mò.

## Yêu cầu mở rộng theo lĩnh vực
{domain_instructions}
"""


@dataclass
class Citation:
    document_name: str
    content: str
    score: float
    segment_id: str
    url: str | None = None
    source_type: str = "document"
    domain: str | None = None
    page_date: str | None = None
    fetched_at: str | None = None
    reliability_score: float | None = None


@dataclass
class StreamEvent:
    type: str   # "token" | "citations" | "done" | "error" | "mode"
    data: object


def _current_datetime_context() -> str:
    tz = ZoneInfo(settings.APP_TIMEZONE)
    now = datetime.now(tz)
    return now.strftime("%H:%M:%S ngày %d/%m/%Y (%Z)")


_LEGAL_QUERY_PATTERNS = [
    r"\btạm trú\b",
    r"\bthường trú\b",
    r"\bcư trú\b",
    r"\bkhai sinh\b",
    r"\bđăng ký\b",
    r"\blệ phí\b",
    r"\bthuế\b",
    r"\bhộ khẩu\b",
    r"\bnghị định\b",
    r"\bthông tư\b",
    r"\bluật\b",
    r"\bđiều\s*\d+",
    r"\bkhoản\s*\d+",
    r"\bpháp luật\b",
    r"\bthủ tục\b",
    r"\bhành chính\b",
    r"\bcăn cứ pháp lý\b",
]


def _is_legal_query(query: str) -> bool:
    text = (query or '').lower()
    return any(re.search(pattern, text) for pattern in _LEGAL_QUERY_PATTERNS)


def _domain_instructions(query: str, *, rag: bool) -> str:
    if _is_legal_query(query):
        source_scope = (
            '- Nếu có nhiều nguồn, ưu tiên văn bản pháp luật, cổng thông tin cơ quan nhà nước, hoặc tài liệu nội bộ có trích dẫn văn bản.\n'
            if not rag
            else '- Ưu tiên các đoạn ngữ cảnh có nêu rõ Điều, Khoản, điểm, tên luật/nghị định/thông tư hoặc hiệu lực văn bản.\n'
        )
        return (
            '### Khi câu hỏi thuộc lĩnh vực pháp lý / thủ tục hành chính\n'
            '- Mở đầu bằng kết luận ngắn gọn, trực diện: có / không / trong trường hợp nào.\n'
            '- Ngay sau kết luận, phải nêu căn cứ pháp lý ngay trong thân câu trả lời nếu ngữ cảnh hoặc nguồn có chứa căn cứ đó.\n'
            '- Ưu tiên format: **Căn cứ pháp lý:** Khoản..., Điều..., tên văn bản..., tình trạng hiệu lực hoặc mốc áp dụng nếu có.\n'
            '- Có thể trích ngắn một câu then chốt, nhưng không sao chép dài; sau đó giải thích lại bằng ngôn ngữ dễ hiểu.\n'
            '- Không trả lời kiểu chung chung nếu đã có căn cứ cụ thể trong nguồn.\n'
            '- Nếu đang trả lời về bảng lệ phí, mức phạt, biểu mức thu hoặc các mục đứng sát nhau, chỉ được dùng con số xuất hiện cùng đúng đối tượng được hỏi trong cùng dòng, cùng mục hoặc cùng đoạn; không được ghép số từ mục lân cận.\n'
            '- Nếu nguồn có nhiều mục gần giống nhau như tạm trú và thường trú, phải kiểm tra lại tên thủ tục ngay trước mỗi mức tiền rồi mới kết luận.\n'
            '- Nếu chưa đủ căn cứ để khẳng định, phải nói rõ là chưa xác minh đủ nguồn pháp lý mới nhất.\n'
            f'{source_scope}'
        )

    return (
        '### Khi câu hỏi không thuộc lĩnh vực pháp lý\n'
        '- Trả lời tự nhiên, rõ ý, có thể ngắn hoặc dài tùy độ phức tạp của câu hỏi.\n'
        '- Không cần cố rút quá ngắn nếu điều đó làm mất thông tin quan trọng.\n'
    )


def _build_rag_context(chunks: list[RetrievedChunk]) -> tuple[str, list[Citation]]:
    """Tạo context string và danh sách citations từ các chunk."""
    parts: list[str] = []
    citations: list[Citation] = []

    for i, chunk in enumerate(chunks, 1):
        parts.append(
            f"[{i}] **{chunk.document_name}** (độ liên quan: {chunk.score:.0%})\n"
            f"{chunk.content}"
        )
        citations.append(
            Citation(
                document_name=chunk.document_name,
                content=chunk.content[:300] + ("…" if len(chunk.content) > 300 else ""),
                score=chunk.score,
                segment_id=chunk.segment_id,
            )
        )

    return "\n\n---\n\n".join(parts), citations


def _build_history(messages: list[Message]) -> list[dict]:
    """Chuyển lịch sử hội thoại sang format Gemini."""
    history: list[dict] = []
    limit = settings.CONVERSATION_HISTORY_LIMIT
    recent = messages[-limit * 2 :] if len(messages) > limit * 2 else messages
    for msg in recent:
        role = "user" if msg.role == "user" else "model"
        history.append({"role": role, "parts": [msg.content]})
    return history


class ChatEngine:
    def __init__(self):
        self._model = genai.GenerativeModel(
            settings.GEMINI_MODEL,
            generation_config=genai.GenerationConfig(
                temperature=settings.GEMINI_TEMPERATURE,
                max_output_tokens=settings.GEMINI_MAX_OUTPUT_TOKENS,
                top_p=settings.GEMINI_TOP_P,
            ),
        )

    async def stream_response(
        self,
        query: str,
        decision: RouteDecision,
        history: list[Message],
        db: AsyncSession,
        conversation: Conversation,
        image_part: Optional[Any] = None,
        use_web: bool = False,
    ) -> AsyncGenerator[str, None]:
        """
        Tạo SSE stream. Mỗi event là JSON line:
          data: {"type": "token", "data": "..."}
          data: {"type": "citations", "data": [...]} 
          data: {"type": "mode", "data": "rag"|"ai"}
          data: {"type": "done", "data": {"tokens": N, "latency_ms": N}}
        """
        start_ms = time.time()
        full_text = ""
        citations: list[Citation] = []
        tokens_used = 0

        # ── Gửi mode trước để frontend hiển thị badge ─────────────────────
        yield _sse(StreamEvent("mode", decision.mode.value))

        try:
            if decision.mode == AnswerMode.RAG:
                async for event in self._stream_rag(query, decision.chunks, history):
                    if event.type == "token":
                        full_text += event.data
                        yield _sse(event)
                    elif event.type == "citations":
                        citations = event.data
                        yield _sse(StreamEvent("citations", [asdict(c) for c in citations]))
            else:
                async for event in self._stream_ai(query, history, support_chunks=decision.chunks, image_part=image_part, use_web=use_web):
                    if event.type == "token":
                        full_text += event.data
                        yield _sse(event)
                    elif event.type == "citations":
                        citations = [_coerce_citation(c) for c in event.data]
                        yield _sse(StreamEvent("citations", [asdict(c) for c in citations]))

            latency_ms = int((time.time() - start_ms) * 1000)
            msg = Message(
                conversation_id=conversation.id,
                role="assistant",
                content=full_text,
                answer_mode=decision.mode.value,
                citations=[asdict(c) for c in citations],
                tokens_used=tokens_used,
                latency_ms=latency_ms,
            )
            conversation.updated_at = datetime.utcnow()
            db.add(msg)

            usage = UsageLog(
                conversation_id=conversation.id,
                message_id=msg.id,
                query_text=query[:500],
                answer_mode=decision.mode.value,
                tokens_used=tokens_used,
                latency_ms=latency_ms,
                is_rag=(decision.mode == AnswerMode.RAG),
                retrieved_chunks=len(decision.chunks),
            )
            db.add(usage)
            await db.commit()

            yield _sse(StreamEvent("done", {"tokens": tokens_used, "latency_ms": latency_ms}))

        except Exception as e:
            logger.exception("Chat engine error: %s", e)
            yield _sse(StreamEvent("error", str(e)))

    async def _stream_rag(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        history: list[Message],
    ) -> AsyncGenerator[StreamEvent, None]:
        context, citations = _build_rag_context(chunks)
        system = _RAG_SYSTEM_PROMPT.format(
            context=context,
            current_datetime=_current_datetime_context(),
            domain_instructions=_domain_instructions(query, rag=True),
        )
        full_prompt = f"{system}\n\n## Câu hỏi của người dùng\n{query}"

        yield StreamEvent("citations", citations)

        chat = self._model.start_chat(history=_build_history(history))
        emitted = False
        async for text in _gemini_stream_in_thread(chat, full_prompt):
            emitted = True
            yield StreamEvent("token", text)
        if not emitted:
            yield StreamEvent("token", "Mình chưa nhận được nội dung phản hồi hợp lệ từ mô hình. Bạn vui lòng thử lại giúp mình.")

    async def _stream_ai(
        self,
        query: str,
        history: list[Message],
        support_chunks: Optional[list[RetrievedChunk]] = None,
        image_part: Optional[Any] = None,
        use_web: bool = False,
    ) -> AsyncGenerator[StreamEvent, None]:
        system_prompt = _AI_SYSTEM_PROMPT.format(
            current_datetime=_current_datetime_context(),
            domain_instructions=_domain_instructions(query, rag=False),
        )
        support_chunks = support_chunks or []
        if support_chunks:
            rag_context, rag_citations = _build_rag_context(support_chunks)
            system_prompt += (
                "\n\n## Ngữ cảnh hỗ trợ từ dataset nội bộ\n"
                f"{rag_context}\n\n"
                "## Quy tắc dùng dataset nội bộ\n"
                "- Dùng ngữ cảnh nội bộ ở trên như lớp kiểm chứng bổ sung, đặc biệt khi câu hỏi nối tiếp ngữ cảnh trước đó.\n"
                "- Nếu dữ liệu web mới hơn và làm thay đổi thông tin nội bộ cũ, phải ưu tiên nguồn mới hơn nhưng nêu rõ phần thay đổi.\n"
                "- Nếu bảng, biểu hoặc mục lệ phí có nhiều dòng gần nhau, chỉ lấy dòng khớp đúng thủ tục đang được hỏi.\n"
            )
            yield StreamEvent("citations", rag_citations)

        web_context, web_citations = await maybe_fetch_web_context(query, force=use_web)

        if web_context:
            system_prompt += (
                "\n\n## Dữ liệu web thời gian thực\n"
                f"{web_context}\n\n"
                "## Quy tắc dùng dữ liệu web\n"
                "- Chỉ dùng dữ liệu web ở trên khi người dùng đã chủ động bật chế độ realtime.\n"
                "- Ưu tiên nguồn có ngày trên trang gần nhất với thời gian hệ thống hiện tại.\n"
                "- Nếu dữ liệu web chưa đủ mới hoặc chưa chắc chắn, nói rõ giới hạn thay vì khẳng định chắc chắn.\n"
                "- Khi phù hợp, nêu tên nguồn hoặc URL trong câu trả lời.\n"
                "- Với câu hỏi pháp lý hoặc lệ phí, phải đối chiếu đúng tên thủ tục trong câu hỏi và trong lịch sử gần nhất trước khi kết luận mức tiền.\n"
            )
            yield StreamEvent("citations", web_citations)
        elif use_web:
            system_prompt += (
                "\n\n## Lưu ý về dữ liệu hiện tại\n"
                "- Người dùng đã bật chế độ thời gian thực.\n"
                "- Nếu không có đủ nguồn web mới, hãy nói rõ rằng bạn chưa lấy được dữ liệu cập nhật thay vì suy đoán.\n"
            )

        gemini_history = _build_history(history)

        if gemini_history and gemini_history[0]["role"] == "user":
            gemini_history[0]["parts"][0] = system_prompt + "\n\n" + gemini_history[0]["parts"][0]

        chat = self._model.start_chat(history=gemini_history)

        prompt_parts: Any
        if image_part is not None:
            prompt_parts = [system_prompt, query or "Hãy phân tích hình ảnh này và trả lời bằng tiếng Việt.", image_part]
        elif not gemini_history:
            prompt_parts = system_prompt + "\n\n" + query
        else:
            prompt_parts = query

        emitted = False
        async for text in _gemini_stream_in_thread(chat, prompt_parts):
            emitted = True
            yield StreamEvent("token", text)
        if not emitted:
            yield StreamEvent("token", "Mình chưa nhận được nội dung phản hồi hợp lệ từ mô hình. Bạn vui lòng thử lại giúp mình.")




def _coerce_citation(raw: Any) -> Citation:
    if isinstance(raw, Citation):
        return raw
    if not isinstance(raw, dict):
        raise TypeError("Invalid citation payload")
    allowed = {
        "document_name", "content", "score", "segment_id", "url",
        "source_type", "domain", "page_date", "fetched_at", "reliability_score",
    }
    data = {key: value for key, value in raw.items() if key in allowed}
    data.setdefault("document_name", "Nguồn")
    data.setdefault("content", "")
    data.setdefault("score", 0.0)
    data.setdefault("segment_id", data.get("url") or data["document_name"])
    return Citation(**data)


def _safe_chunk_text(response_chunk: Any) -> str:
    try:
        text = getattr(response_chunk, "text")
        if text:
            return str(text)
    except Exception:
        pass

    parts_text: list[str] = []
    try:
        candidates = getattr(response_chunk, "candidates", None) or []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", None) or []
            for part in parts:
                part_text = getattr(part, "text", None)
                if part_text:
                    parts_text.append(str(part_text))
    except Exception:
        return ""

    return "".join(parts_text)


async def _gemini_stream_in_thread(chat, prompt: Any) -> AsyncGenerator[str, None]:
    """
    Chạy Gemini streaming call trong một thread riêng,
    dùng queue để bridge sang async generator — tránh block event loop.
    """
    _SENTINEL = object()
    token_queue: queue.Queue = queue.Queue()

    def _worker():
        try:
            response = chat.send_message(prompt, stream=True)
            for chunk in response:
                chunk_text = _safe_chunk_text(chunk)
                if chunk_text:
                    token_queue.put(chunk_text)
        except Exception as e:
            token_queue.put(e)
        finally:
            token_queue.put(_SENTINEL)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()

    loop = asyncio.get_event_loop()
    while True:
        item = await loop.run_in_executor(None, token_queue.get)
        if item is _SENTINEL:
            break
        if isinstance(item, Exception):
            raise item
        yield item


def _sse(event: StreamEvent) -> str:
    """Format một SSE line."""
    payload = json.dumps({"type": event.type, "data": event.data}, ensure_ascii=False)
    return f"data: {payload}\n\n"


chat_engine = ChatEngine()
