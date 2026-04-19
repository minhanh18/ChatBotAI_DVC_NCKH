"""
Chat Engine — lõi sinh câu trả lời với streaming.
Ưu tiên RAG trước, có gate đánh giá bằng chứng trước khi phản hồi,
và chỉ hiển thị nguồn đã thực sự dùng.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict, deque
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
from app.chat.evaluator import (
    build_safe_fallback_answer,
    is_greeting_query,
    is_legal_query,
    is_procedure_query,
)
from app.config import settings
from app.models.db import Conversation, Message, UsageLog
from app.rag.retriever import RetrievedChunk
from app.rag.source_hints import (
    get_document_hint,
    pretty_document_name,
    resolve_document_source_url,
)
from app.web.live_search import maybe_fetch_web_context

logger = logging.getLogger(__name__)
genai.configure(api_key=settings.GEMINI_API_KEY)

_RAG_SYSTEM_PROMPT = """Bạn là trợ lý AI chuyên trả lời câu hỏi dựa trên tài liệu nội bộ.

## Thời gian hệ thống nội bộ
{current_datetime}

## Hướng dẫn chung
- Chỉ trả lời dựa trên ngữ cảnh được cung cấp bên dưới.
- Dùng mốc thời gian hệ thống ở trên để hiểu các cụm như hôm nay, hiện nay, hiện tại.
- Không được tự in nguyên văn dòng thời gian hệ thống ở trên ra câu trả lời, trừ khi người dùng hỏi trực tiếp về ngày giờ hiện tại.
- Nếu không tìm thấy thông tin trong ngữ cảnh, hãy trả về đúng chuỗi [[RAG_NO_ANSWER]] và không viết gì thêm.
- Trích dẫn nguồn tài liệu cụ thể bằng cú pháp [Nguồn: <tên tài liệu>, trang <số trang>] nếu xác định được trang.
- Không dùng đuôi .pdf trong tên tài liệu khi trả lời.
- Nếu đang dùng một tài liệu nội bộ cụ thể để trả lời, nên dẫn nhập tự nhiên bằng cụm như "Theo thông tin trong ..." thay vì lặp lại tên file thô.
- Không bịa đặt thông tin ngoài ngữ cảnh.
- Nếu đây là câu hỏi thủ tục, ưu tiên trình bày theo các mục: Kết luận ngắn, Hồ sơ cần chuẩn bị, Nơi nộp, Trình tự thực hiện, Lưu ý.

## Yêu cầu mở rộng theo lĩnh vực
{domain_instructions}

## Ngữ cảnh từ tài liệu nội bộ
{context}
"""

_AI_SYSTEM_PROMPT = """Bạn là trợ lý AI thông minh, trả lời bằng tiếng Việt.

## Thời gian hệ thống nội bộ
{current_datetime}

## Hướng dẫn chung
- Trả lời chính xác, hữu ích và đầy đủ vừa phải.
- Với câu hỏi pháp lý/hành chính cần kiểm tra bản mới nhất, hiệu lực, sửa đổi, bổ sung hoặc đối chiếu lại, ưu tiên nguồn web đang được cung cấp trong ngữ cảnh.
- Không dùng dữ liệu web cho câu hỏi pháp lý thông thường nếu chưa thật sự cần kiểm chứng tính cập nhật.
- Nếu có nhiều nguồn và một nguồn mới hơn làm thay đổi nội dung nguồn cũ, dùng nguồn mới hơn và nói rõ điểm thay đổi.
- Nếu nguồn có nêu rõ Điều, Khoản, điểm hoặc câu chữ pháp lý then chốt thì phải đưa căn cứ đó vào câu trả lời.
- Nếu nguồn có câu chữ then chốt đủ rõ, phải trích ngắn 1 đoạn trong blockquote Markdown.
- Không tách riêng một dòng mở đầu kiểu "Có, ..." rồi lại thêm mục "Kết luận ngắn:" ở ngay bên dưới. Phải gộp thành 1 câu duy nhất.
- Không tách riêng mục "Căn cứ pháp lý" rồi lại thêm một khối "Điều ..." độc lập bên dưới nếu cả hai đang nói cùng một căn cứ.
- Với câu hỏi pháp lý/thủ tục, ưu tiên đúng format sau:

  1. Một câu trả lời trực diện, gộp luôn kết luận. Ví dụ: "Có, công dân phải ..."
  2. Một đoạn mở đầu bằng: "Theo quy định tại Khoản ... Điều ... [Tên văn bản]:"
  3. Ngay bên dưới là blockquote trích nguyên văn phần điều khoản liên quan nhất
  4. Sau đó mới giải thích ngắn gọn, dễ hiểu nếu cần

- Nếu chưa đủ căn cứ đáng tin, phải nói rõ là chưa đủ cơ sở để khẳng định chính xác hoàn toàn.
- Nếu người dùng gửi hình ảnh, hãy phân tích đúng nội dung nhìn thấy trong hình rồi mới trả lời.
- Dùng Markdown khi phù hợp.
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
    type: str
    data: object


def _current_datetime_context() -> str:
    tz = ZoneInfo(settings.APP_TIMEZONE)
    now = datetime.now(tz)
    return now.strftime("%H:%M:%S ngày %d/%m/%Y (%Z)")


def _domain_instructions(query: str, *, rag: bool) -> str:
    if is_legal_query(query):
        source_scope = (
            "- Nếu có nhiều nguồn, ưu tiên văn bản pháp luật, cổng thông tin cơ quan nhà nước, hoặc tài liệu nội bộ có trích dẫn văn bản.\n"
            if not rag
            else "- Ưu tiên các đoạn ngữ cảnh có nêu rõ Điều, Khoản, điểm, tên luật/nghị định/thông tư hoặc hiệu lực văn bản.\n"
        )
        guidance = (
            "- Nếu đây là câu hỏi thủ tục, chỉ nêu Hồ sơ/Nơi nộp/Bước thực hiện khi nguồn thật sự hỗ trợ; không tự suy diễn thêm.\n"
            if is_procedure_query(query)
            else ""
        )
        return (
            "### Khi câu hỏi thuộc lĩnh vực pháp lý / thủ tục hành chính\n"
            "- Mở đầu bằng 1 câu trả lời trực diện, ngắn gọn, gộp luôn kết luận; không lặp lại thêm mục 'Kết luận ngắn' ngay bên dưới.\n"
            "- Sau câu mở đầu, nếu có căn cứ cụ thể thì viết: 'Theo quy định tại Khoản..., Điều..., tên văn bản:'.\n"
            "- Ngay sau câu đó, trích 1 blockquote ngắn là đúng phần câu chữ pháp lý liên quan nhất.\n"
            "- Không tách riêng một mục 'Căn cứ pháp lý' rồi lại tách tiếp một mục 'Điều ...' nếu cùng nói về một căn cứ.\n"
            "- Nếu chưa đủ căn cứ để khẳng định, phải nói rõ là chưa đủ cơ sở để khẳng định chính xác hoàn toàn.\n"
            "- Không trả lời kiểu chung chung nếu đã có căn cứ cụ thể trong nguồn.\n"
            "- Nếu nguồn có nhiều mục gần giống nhau như tạm trú và thường trú, phải kiểm tra lại tên thủ tục ngay trước mỗi kết luận.\n"
            f"{guidance}"
            f"{source_scope}"
        )

    return (
        "### Khi câu hỏi không thuộc lĩnh vực pháp lý\n"
        "- Trả lời tự nhiên, rõ ý, có thể ngắn hoặc dài tùy độ phức tạp của câu hỏi.\n"
    )


def _page_label_from_meta(meta: dict[str, Any] | None) -> str | None:
    info = meta or {}
    page_start = info.get("page_start")
    page_end = info.get("page_end")
    if isinstance(page_start, int):
        if isinstance(page_end, int) and page_end != page_start:
            return f"trang {page_start}-{page_end}"
        return f"trang {page_start}"
    location = info.get("location_label")
    if isinstance(location, str) and location.strip() and location.strip().lower().startswith("trang"):
        return location.strip()
    return None


def _chunk_location_label(chunk: RetrievedChunk) -> str | None:
    meta = chunk.segment_meta or {}
    parts: list[str] = []
    page_label = _page_label_from_meta(meta)
    if page_label:
        parts.append(page_label)
    for key in ("article_ref", "clause_ref", "point_ref"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return " • ".join(parts) if parts else None


def _build_rag_context(chunks: list[RetrievedChunk]) -> tuple[str, list[Citation]]:
    parts: list[str] = []
    citations: list[Citation] = []

    for i, chunk in enumerate(chunks, 1):
        hint = get_document_hint(chunk.document_name, chunk.document_meta) or {}
        display_name = pretty_document_name(chunk.document_name)
        source_url = resolve_document_source_url(chunk.document_name, chunk.document_meta)
        source_label = hint.get("source_label")
        page_label = _page_label_from_meta(chunk.segment_meta or {})
        location = _chunk_location_label(chunk)

        header_lines = [f"[{i}] Tài liệu: {display_name}"]
        if source_label:
            header_lines.append(f"Cơ quan/tổ chức ban hành: {source_label}")
        if page_label:
            header_lines.append(f"Trang tham chiếu: {page_label}")
        if source_url:
            header_lines.append(f"URL nguồn: {source_url}")
        if hint.get("lead"):
            header_lines.append(f"Câu dẫn gợi ý: {hint['lead']}")

        parts.append("\n".join(header_lines) + f"\nNội dung trích: {chunk.content}")
        citations.append(
            Citation(
                document_name=display_name,
                content=page_label or (location or ""),
                score=chunk.score,
                segment_id=chunk.segment_id,
                url=source_url,
                source_type="document",
            )
        )

    return "\n\n---\n\n".join(parts), citations


def _merge_citations(existing: list[Citation], incoming: list[Citation]) -> list[Citation]:
    merged: list[Citation] = []
    seen: set[str] = set()

    for citation in [*existing, *incoming]:
        if citation.source_type == "document":
            key = f"doc::{citation.document_name.strip().lower()}::{citation.segment_id}"
        else:
            key = (
                citation.url
                or f"web::{(citation.document_name or '').strip().lower()}::"
                   f"{(citation.domain or '').strip().lower()}::"
                   f"{(citation.page_date or '').strip().lower()}::"
                   f"{citation.segment_id}"
            ).strip().lower()

        if not key or key in seen:
            continue

        seen.add(key)
        merged.append(citation)

    return merged


def _dedupe_legal_quotes(text: str) -> str:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line
        if raw_line.lstrip().startswith(">"):
            prefix, _, rest = raw_line.partition(">")
            quote_body = rest.lstrip()
            quote_body = re.sub(r'^[“”"]{2,}', '"', quote_body)
            quote_body = re.sub(r'^[“”](?=[“"])', '"', quote_body)
            quote_body = re.sub(r'(?<=[^\s])[”"]{2,}\s*$', '"', quote_body)
            line = f"{prefix}> {quote_body}".rstrip()
        lines.append(line)
    return "\n".join(lines)


def _remove_redundant_legal_basis_section(text: str) -> str:
    if not re.search(r"(?m)^>\s+", text):
        return text
    pattern = re.compile(
        r"(?:^|\n)(?:\*\*)?Căn cứ pháp lý:?\*\*?\s*\n(?:\s*[-*]\s.*(?:\n|$)){1,8}",
        re.IGNORECASE,
    )
    return pattern.sub("\n", text, count=1)


def _replace_pdf_suffixes(text: str) -> str:
    return re.sub(r"(?i)\.pdf\b", "", text or "").strip()


def _linkify_inline_doc_references(text: str, citations: list[Citation]) -> str:
    if not text or not citations:
        return text

    page_queues: dict[str, deque[str]] = defaultdict(deque)
    url_by_name: dict[str, str] = {}
    display_by_name: dict[str, str] = {}

    for citation in citations:
        if citation.source_type not in {"document", "web"}:
            continue
        display = pretty_document_name(citation.document_name)
        norm = re.sub(r"\s+", " ", display.lower()).strip()
        display_by_name[norm] = display
        if citation.url:
            url_by_name[norm] = citation.url
        page_label = (citation.content or "").strip()
        if page_label and page_label not in page_queues[norm]:
            page_queues[norm].append(page_label)

    def _pick_name(raw_name: str) -> tuple[str | None, str | None, str | None]:
        candidate = re.sub(r"(?i)\.pdf\b", "", raw_name or "").strip()
        norm = re.sub(r"\s+", " ", candidate.lower()).strip()
        for known_norm, display in display_by_name.items():
            if norm == known_norm or norm in known_norm or known_norm in norm:
                page = page_queues[known_norm].popleft() if page_queues.get(known_norm) else None
                return display, url_by_name.get(known_norm), page
        return pretty_document_name(candidate), None, None

    pattern = re.compile(
        r"\[(?:Tài liệu|Nguồn)\s*:\s*([^,\]]+?)(?:\.pdf)?(?:,\s*(?:đoạn\s*\d+|trang\s*[0-9-]+))?\]"
    )

    def repl(match: re.Match[str]) -> str:
        display, url, page = _pick_name(match.group(1))
        label = display
        if page:
            label = f"{display}, {page}"
        if url:
            return f"[{label}]({url})"
        return f"[{label}]"

    return pattern.sub(repl, text)


def _clean_response_text(text: str, citations: list[Citation] | None = None) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return cleaned
    cleaned = _dedupe_legal_quotes(cleaned)
    cleaned = _remove_redundant_legal_basis_section(cleaned)
    cleaned = _replace_pdf_suffixes(cleaned)
    cleaned = _linkify_inline_doc_references(cleaned, citations or [])
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def _normalize_legal_answer_structure(text: str) -> str:
    content = (text or "").strip()
    if not content:
        return content

    content = re.sub(
        r'(?is)^(Có[,:\s].*?)\n+\s*(?:\*\*)?Kết luận ngắn:?\*\*?\s*(.+?)(?=\n{2,}|\n(?:Căn cứ pháp lý|Theo quy định tại)|$)',
        lambda m: m.group(2).strip()
        if m.group(2).strip().lower().startswith(("có,", "không,", "được,", "phải,", "không phải"))
        else f"{m.group(1).strip()} {m.group(2).strip()}",
        content,
        count=1,
    )

    content = re.sub(
        r"(?im)^(?:\*\*)?Căn cứ pháp lý:?\*\*?\s*(Theo quy định tại .+)$",
        r"\1",
        content,
    )

    content = re.sub(
        r'(?im)^\s*[“"\']?(Điều\s+\d+[^"\n]*)[”"\']?\s*$\n?',
        "",
        content,
    )

    content = re.sub(r"\n{3,}", "\n\n", content).strip()
    return content


def _ensure_rag_source_lead(text: str, citations: list[Citation]) -> str:
    if not text.strip():
        return text
    first_doc = next((citation for citation in citations if citation.source_type == "document"), None)
    if not first_doc:
        return text

    display_name = pretty_document_name(first_doc.document_name)
    hint = get_document_hint(display_name, {}) or {}
    lowered_head = text[:260].lower()
    if display_name.lower() in lowered_head or "theo thông tin trong" in lowered_head:
        return text

    lead = str(
        hint.get("lead")
        or f"Theo thông tin trong tài liệu {display_name}, tôi trả lời câu hỏi của bạn như sau:"
    ).strip()
    return lead + "\n\n" + text.lstrip()


_RAG_NO_ANSWER_MARKERS = (
    "tài liệu không đề cập rõ",
    "tài liệu chưa đề cập rõ",
    "không tìm thấy thông tin trong ngữ cảnh",
    "không tìm thấy thông tin trong tài liệu",
    "ngữ cảnh không đề cập",
    "không có thông tin trong tài liệu",
    "không đủ thông tin trong tài liệu",
    "chưa có thông tin trong tài liệu",
    "không có trong tài liệu",
    "không được đề cập trong tài liệu",
    "không có nội dung nào đề cập",
    "không có nội dung đề cập",
    "không đề cập đến",
    "chưa đề cập đến",
    "không nói đến",
    "chưa nói đến",
    "không có thông tin nào về",
    "các tài liệu này tập trung vào",
    "tài liệu hiện có không chứa",
)


def _should_fallback_to_web_after_rag(query: str, text: str) -> bool:
    normalized = " ".join((text or "").lower().split())
    if not normalized:
        return True

    if "[[rag_no_answer]]" in normalized:
        return True

    if any(marker in normalized for marker in _RAG_NO_ANSWER_MARKERS):
        return True

    if is_legal_query(query) or is_procedure_query(query):
        fallback_patterns = (
            ("không" in normalized and "đề cập" in normalized),
            ("không" in normalized and "thông tin" in normalized and "tài liệu" in normalized),
            ("chưa đủ cơ sở" in normalized and "tài liệu" in normalized),
            ("không có" in normalized and "tài liệu" in normalized and "về" in normalized),
        )
        if any(fallback_patterns):
            return True

    return False


def _build_history(messages: list[Message]) -> list[dict]:
    history: list[dict] = []
    limit = settings.CONVERSATION_HISTORY_LIMIT
    recent = messages[-limit * 2:] if len(messages) > limit * 2 else messages
    for msg in recent:
        role = "user" if msg.role == "user" else "model"
        history.append({"role": role, "parts": [msg.content]})
    return history


def _coerce_citation(raw: Any) -> Citation:
    if isinstance(raw, Citation):
        return raw
    if not isinstance(raw, dict):
        raise TypeError("Invalid citation payload")

    url = raw.get("url") or raw.get("source_url") or raw.get("link") or raw.get("href")

    document_name = (
        raw.get("document_name")
        or raw.get("title")
        or raw.get("name")
        or raw.get("domain")
        or "Nguồn web"
    )

    content = raw.get("content") or raw.get("snippet") or raw.get("excerpt") or ""

    source_type = raw.get("source_type") or ("web" if url else "document")

    segment_id = (
        raw.get("segment_id")
        or raw.get("id")
        or url
        or f"{document_name}::{raw.get('page_date') or raw.get('fetched_at') or ''}"
    )

    return Citation(
        document_name=document_name,
        content=content,
        score=float(raw.get("score") or 0.0),
        segment_id=str(segment_id),
        url=url,
        source_type=source_type,
        domain=raw.get("domain"),
        page_date=raw.get("page_date"),
        fetched_at=raw.get("fetched_at"),
        reliability_score=raw.get("reliability_score"),
    )


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
    payload = json.dumps({"type": event.type, "data": event.data}, ensure_ascii=False)
    return f"data: {payload}\n\n"


async def _stream_static_text(text: str) -> AsyncGenerator[StreamEvent, None]:
    remaining = text or ""
    while remaining:
        chunk = remaining[:120]
        remaining = remaining[120:]
        yield StreamEvent("token", chunk)
        await asyncio.sleep(0)


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
        force_web: bool = False,
    ) -> AsyncGenerator[str, None]:
        start_ms = time.time()
        full_text = ""
        citations: list[Citation] = []
        tokens_used = 0
        response_mode = decision.mode

        try:
            assessment = decision.assessment or {}
            is_greeting = decision.reason == "greeting" or is_greeting_query(query)

            if is_greeting and image_part is None:
                response_mode = AnswerMode.AI
                yield _sse(StreamEvent("mode", response_mode.value))

                greeting_text = "Xin chào! Tôi có thể giúp gì cho bạn hôm nay?"
                async for event in _stream_static_text(greeting_text):
                    full_text += str(event.data)
                    yield _sse(event)

            elif (
                assessment.get("should_refuse_precise")
                and decision.mode == AnswerMode.AI
                and image_part is None
                and not force_web
            ):
                response_mode = AnswerMode.AI
                yield _sse(StreamEvent("mode", response_mode.value))

                fallback_text = build_safe_fallback_answer(query, _AssessmentAdapter(assessment))
                async for event in _stream_static_text(fallback_text):
                    full_text += str(event.data)
                    yield _sse(event)

            else:
                if decision.mode == AnswerMode.RAG:
                    rag_text, rag_citations = await self._generate_rag_answer(
                        query=query,
                        chunks=decision.chunks,
                        history=history,
                    )
                    rag_text = _clean_response_text(rag_text, rag_citations)

                    if _should_fallback_to_web_after_rag(query, rag_text):
                        logger.info(
                            "RAG did not answer sufficiently; fallback to web search for query=%s",
                            query,
                        )

                        response_mode = AnswerMode.AI
                        yield _sse(StreamEvent("mode", response_mode.value))

                        async for event in self._stream_ai(
                            query=query,
                            history=history,
                            support_chunks=decision.chunks,
                            image_part=image_part,
                            force_web=True,
                        ):
                            if event.type == "token":
                                full_text += str(event.data)
                                yield _sse(event)
                            elif event.type == "citations":
                                citations = _merge_citations(
                                    citations,
                                    [_coerce_citation(c) for c in event.data],
                                )
                    else:
                        response_mode = AnswerMode.RAG
                        citations = _merge_citations(citations, rag_citations)
                        yield _sse(StreamEvent("mode", response_mode.value))

                        async for event in _stream_static_text(rag_text):
                            full_text += str(event.data)
                            yield _sse(event)

                else:
                    response_mode = AnswerMode.AI
                    yield _sse(StreamEvent("mode", response_mode.value))

                    support_chunks: list[RetrievedChunk] = []
                    best_score = float(assessment.get("best_score") or 0.0)

                    allow_internal_support = (
                        decision.chunks
                        and best_score >= 0.45
                        and not (
                            force_web
                            and assessment.get("should_force_web")
                            and not assessment.get("should_use_rag")
                        )
                    )

                    if allow_internal_support:
                        support_chunks = decision.chunks

                    async for event in self._stream_ai(
                        query=query,
                        history=history,
                        support_chunks=support_chunks,
                        image_part=image_part,
                        force_web=force_web,
                    ):
                        if event.type == "token":
                            full_text += str(event.data)
                            yield _sse(event)
                        elif event.type == "citations":
                            citations = _merge_citations(
                                citations,
                                [_coerce_citation(c) for c in event.data],
                            )

            full_text = _clean_response_text(full_text, citations)
            if is_legal_query(query) or is_procedure_query(query):
                full_text = _normalize_legal_answer_structure(full_text)

            if response_mode == AnswerMode.RAG and full_text and not is_greeting:
                full_text = _ensure_rag_source_lead(full_text, citations)

            latency_ms = int((time.time() - start_ms) * 1000)
            tokens_used = max(0, len(full_text) // 4)

            msg = Message(
                conversation_id=conversation.id,
                role="assistant",
                content=full_text,
                answer_mode=response_mode.value,
                citations=[asdict(c) for c in citations],
                tokens_used=tokens_used,
                latency_ms=latency_ms,
            )
            conversation.updated_at = datetime.utcnow()
            db.add(msg)
            await db.flush()

            usage = UsageLog(
                conversation_id=conversation.id,
                message_id=msg.id,
                query_text=query[:500],
                answer_mode=response_mode.value,
                tokens_used=tokens_used,
                latency_ms=latency_ms,
                is_rag=(response_mode == AnswerMode.RAG),
                retrieved_chunks=len(decision.chunks),
            )
            db.add(usage)
            await db.commit()

            if citations and not is_greeting and not assessment.get("should_refuse_precise"):
                yield _sse(StreamEvent("citations", [asdict(c) for c in citations]))

            yield _sse(
                StreamEvent(
                    "done",
                    {"tokens": tokens_used, "latency_ms": latency_ms},
                )
            )

        except Exception as e:
            logger.exception("Chat engine error: %s", e)
            yield _sse(StreamEvent("error", str(e)))

    async def _generate_rag_answer(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        history: list[Message],
    ) -> tuple[str, list[Citation]]:
        context, citations = _build_rag_context(chunks)
        system = _RAG_SYSTEM_PROMPT.format(
            context=context,
            current_datetime=_current_datetime_context(),
            domain_instructions=_domain_instructions(query, rag=True),
        )
        full_prompt = f"{system}\n\n## Câu hỏi của người dùng\n{query}"

        chat = self._model.start_chat(history=_build_history(history))
        parts: list[str] = []

        async for text in _gemini_stream_in_thread(chat, full_prompt):
            if text:
                parts.append(text)

        full_text = "".join(parts).strip()
        if not full_text:
            full_text = "Mình chưa nhận được nội dung phản hồi hợp lệ từ mô hình. Bạn vui lòng thử lại giúp mình."

        return full_text, citations

    async def _stream_ai(
        self,
        query: str,
        history: list[Message],
        support_chunks: Optional[list[RetrievedChunk]] = None,
        image_part: Optional[Any] = None,
        force_web: bool = False,
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
                "- Chỉ dùng dữ liệu nội bộ khi nội dung thật sự khớp; nếu chỉ khớp một phần thì phải nói rõ giới hạn.\n"
            )
            yield StreamEvent("citations", rag_citations)

        web_context, web_citations = await maybe_fetch_web_context(query, force=force_web)
        if web_context:
            system_prompt += (
                "\n\n## Dữ liệu web kiểm chứng cập nhật\n"
                f"{web_context}\n\n"
                "## Quy tắc dùng dữ liệu web\n"
                "- Chỉ dùng dữ liệu web ở trên khi truy vấn cần kiểm tra tính cập nhật, hiệu lực, sửa đổi, thay thế của văn bản pháp lý/hành chính.\n"
                "- Chỉ coi các URL trong ngữ cảnh web ở trên là nguồn tham khảo có thể hiển thị; không được tự bịa thêm link khác.\n"
                "- Nếu dữ liệu web chưa đủ mới hoặc chưa chắc chắn, nói rõ giới hạn thay vì khẳng định chắc chắn.\n"
                "- Với câu hỏi pháp lý hoặc lệ phí, phải đối chiếu đúng tên thủ tục trong câu hỏi trước khi kết luận mức tiền.\n"
            )
            yield StreamEvent("citations", [_coerce_citation(c) for c in web_citations])

        elif force_web:
            system_prompt += (
                "\n\n## Lưu ý về dữ liệu hiện tại\n"
                "- Đây là truy vấn cần kiểm tra nguồn web cập nhật, nhưng hiện chưa lấy được đủ nguồn phù hợp.\n"
                "- Nếu chưa đủ căn cứ mới, hãy nói rõ giới hạn này thay vì suy đoán.\n"
            )

        gemini_history = _build_history(history)
        if gemini_history and gemini_history[0]["role"] == "user":
            gemini_history[0]["parts"][0] = system_prompt + "\n\n" + gemini_history[0]["parts"][0]

        chat = self._model.start_chat(history=gemini_history)

        if image_part is not None:
            prompt_parts: Any = [
                system_prompt,
                query or "Hãy phân tích hình ảnh này và trả lời bằng tiếng Việt.",
                image_part,
            ]
        elif not gemini_history:
            prompt_parts = system_prompt + "\n\n" + query
        else:
            prompt_parts = query

        emitted = False
        async for text in _gemini_stream_in_thread(chat, prompt_parts):
            emitted = True
            yield StreamEvent("token", text)

        if not emitted:
            yield StreamEvent(
                "token",
                "Tôi chưa nhận được nội dung phản hồi hợp lệ từ mô hình. Bạn vui lòng thử lại giúp tôi.",
            )


class _AssessmentAdapter:
    def __init__(self, payload: dict[str, Any]):
        self.__dict__.update(payload or {})


chat_engine = ChatEngine()