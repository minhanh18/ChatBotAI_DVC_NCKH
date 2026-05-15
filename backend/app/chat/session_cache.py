"""
Session Memory Cache — ghi nhớ ngữ cảnh trong phiên trò chuyện.

Kiến trúc (dựa trên research conversation context management 2024-2025):
  ┌─────────────────────────────────────────────────────────────────┐
  │  Mỗi phiên (session_key = conversation_id) lưu:                │
  │    • recent_turns     — N cặp (query, answer) gần nhất          │
  │    • cached_chunk_ids — segment_id đã dùng trong phiên          │
  │    • cached_chunks    — nội dung chunk đã retrieve               │
  │    • rolling_summary  — tóm tắt lịch sử (Gemini nén mỗi 3 turn) │
  │    • entities         — thực thể đã nhắc đến (người, luật, thủ tục)│
  │    • ttl              — xóa khi user đóng tab (mặc định 2 giờ)  │
  └─────────────────────────────────────────────────────────────────┘

Lợi ích:
  1. Câu hỏi follow-up → lookup cached_chunks trước, skip RAG nếu đủ.
  2. LLM nhận rolling_summary → không cần toàn bộ history.
  3. Entities extraction → gợi ý hỏi thêm liên quan.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Cấu hình ─────────────────────────────────────────────────────────────────
_SESSION_TTL_SEC = 7200        # 2 giờ
_MAX_RECENT_TURNS = 6          # số lượt (query+answer) giữ lại
_SUMMARY_EVERY_N_TURNS = 3     # nén summary sau mỗi N lượt
_MAX_CACHED_CHUNKS = 30        # tối đa số chunk lưu trong session
_MAX_SUMMARY_CHARS = 1200      # giới hạn ký tự rolling summary


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class CachedChunk:
    segment_id: str
    document_name: str
    content: str
    score: float
    document_id: str = ""
    position: int = 0
    segment_meta: dict = field(default_factory=dict)
    document_meta: dict = field(default_factory=dict)
    added_at: float = field(default_factory=time.time)


@dataclass
class SessionState:
    session_key: str
    recent_turns: list[tuple[str, str]] = field(default_factory=list)   # [(query, answer)]
    cached_chunks: list[CachedChunk] = field(default_factory=list)
    rolling_summary: str = ""
    entities: list[str] = field(default_factory=list)
    turn_count: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # Cache kết quả web search theo topic — tránh gọi lại Tavily cho follow-up cùng chủ đề
    web_results: dict = field(default_factory=dict)  # topic_key → {context, citations, cached_at}


# ── In-memory store ───────────────────────────────────────────────────────────
# Không dùng Redis để tránh phụ thuộc thêm; TTL được enforce khi đọc.
# Với production nhiều worker: nên thay bằng Redis với JSON serialization.

_store: dict[str, dict] = {}   # session_key → serialised SessionState


def _now() -> float:
    return time.time()


def _session_key_hash(conversation_id: str) -> str:
    return hashlib.md5(conversation_id.encode()).hexdigest()[:16]


def _get_state(session_key: str) -> SessionState | None:
    raw = _store.get(session_key)
    if raw is None:
        return None
    # TTL check
    if _now() - raw.get("updated_at", 0) > _SESSION_TTL_SEC:
        del _store[session_key]
        return None
    chunks = [CachedChunk(**c) for c in raw.get("cached_chunks", [])]
    return SessionState(
        session_key=session_key,
        recent_turns=raw.get("recent_turns", []),
        cached_chunks=chunks,
        rolling_summary=raw.get("rolling_summary", ""),
        entities=raw.get("entities", []),
        turn_count=raw.get("turn_count", 0),
        created_at=raw.get("created_at", _now()),
        updated_at=raw.get("updated_at", _now()),
        web_results=raw.get("web_results", {}),
    )


def _save_state(state: SessionState) -> None:
    state.updated_at = _now()
    _store[state.session_key] = {
        "recent_turns": state.recent_turns[-_MAX_RECENT_TURNS * 2:],
        "cached_chunks": [asdict(c) for c in state.cached_chunks[-_MAX_CACHED_CHUNKS:]],
        "rolling_summary": state.rolling_summary,
        "entities": state.entities[-50:],
        "turn_count": state.turn_count,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
        "web_results": state.web_results,
    }


def _init_state(session_key: str) -> SessionState:
    state = SessionState(session_key=session_key)
    _save_state(state)
    return state


# ── Public API ────────────────────────────────────────────────────────────────

def get_session_summary(session_key: str) -> str:
    """Trả về rolling summary của phiên (rỗng nếu chưa có)."""
    state = _get_state(session_key)
    return state.rolling_summary if state else ""


def get_cached_chunks(session_key: str, query: str) -> list[Any]:
    """
    Tìm các chunk đã cache trong phiên có liên quan đến query.
    Dùng simple keyword overlap để quyết định độ liên quan.
    Trả về list RetrievedChunk-compatible dicts.
    """
    state = _get_state(session_key)
    if not state or not state.cached_chunks:
        return []

    normalized_query = (query or "").lower().replace("đk", "đăng ký").replace("đăng kí", "đăng ký")
    query_words = set(normalized_query.split())
    relevant: list[tuple[float, CachedChunk]] = []

    for chunk in state.cached_chunks:
        content_words = set((chunk.document_name + " " + chunk.content).lower().split())
        overlap = len(query_words & content_words) / max(len(query_words), 1)
        if overlap > 0.25:   # ≥ 25% từ query xuất hiện trong chunk
            # Tăng score theo độ overlap
            adjusted_score = min(chunk.score * (1 + overlap), 0.99)
            relevant.append((adjusted_score, chunk))

    if not relevant:
        return []

    relevant.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in relevant[:5]]


def cache_chunks(session_key: str, chunks: list[Any]) -> None:
    """
    Lưu các chunk vừa retrieve vào cache phiên.
    Dedup theo segment_id.
    """
    state = _get_state(session_key) or _init_state(session_key)
    existing_ids = {c.segment_id for c in state.cached_chunks}

    for chunk in chunks:
        sid = getattr(chunk, "segment_id", None) or str(chunk.get("segment_id", ""))
        if sid and sid not in existing_ids:
            state.cached_chunks.append(CachedChunk(
                segment_id=sid,
                document_name=getattr(chunk, "document_name", ""),
                content=getattr(chunk, "content", ""),
                score=getattr(chunk, "score", 0.0),
                document_id=str(getattr(chunk, "document_id", "") or ""),
                position=int(getattr(chunk, "position", 0) or 0),
                segment_meta=getattr(chunk, "segment_meta", {}) or {},
                document_meta=getattr(chunk, "document_meta", {}) or {},
            ))
            existing_ids.add(sid)

    _save_state(state)


def reconstruct_chunks(session_key: str) -> list[Any]:
    """Trả về tất cả cached chunks cho phiên (để fallback retrieval)."""
    state = _get_state(session_key)
    if not state:
        return []
    return list(state.cached_chunks)


async def maybe_update_summary(
    session_key: str,
    query: str,
    answer: str,
    history_pairs: list[tuple[str, str]],
    gemini_model: Any,
) -> None:
    """
    Cập nhật turn count + recent_turns.
    Mỗi SUMMARY_EVERY_N_TURNS lượt → dùng Gemini nén thành rolling_summary.
    Chạy async, không block main flow.
    """
    state = _get_state(session_key) or _init_state(session_key)
    state.turn_count += 1
    state.recent_turns.append((query, answer[:800]))   # giới hạn answer được cache

    # Trích entities đơn giản (tên luật, số điều)
    import re
    new_entities = re.findall(
        r"(?:Điều|Khoản|Luật|Nghị định|Thông tư|Quyết định)\s+[\w/\-]+",
        query + " " + answer,
        re.IGNORECASE,
    )
    for e in new_entities:
        if e not in state.entities:
            state.entities.append(e)

    # Nén summary định kỳ
    if state.turn_count % _SUMMARY_EVERY_N_TURNS == 0 and gemini_model and state.recent_turns:
        try:
            pairs_text = "\n".join(
                f"Q: {q}\nA: {a[:400]}"
                for q, a in state.recent_turns[-_SUMMARY_EVERY_N_TURNS * 2:]
            )
            prev = state.rolling_summary
            prompt = (
                f"Tóm tắt ngắn gọn (tối đa 200 từ) cuộc hội thoại dưới đây, "
                f"tập trung vào chủ đề pháp lý/thủ tục đang thảo luận, "
                f"những điều khoản đã nhắc đến, và điều người dùng cần làm tiếp theo.\n\n"
                f"Tóm tắt trước đó (nếu có): {prev}\n\n"
                f"Các lượt hội thoại gần đây:\n{pairs_text}\n\n"
                f"Tóm tắt cập nhật:"
            )
            chat = gemini_model.start_chat(history=[])
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: chat.send_message(prompt).text,
            )
            state.rolling_summary = response.strip()[:_MAX_SUMMARY_CHARS]
            logger.debug("Session %s summary updated (%d chars)", session_key[:8], len(state.rolling_summary))
        except Exception as e:
            logger.debug("Summary update failed (ignored): %s", e)

    _save_state(state)


def increment_turn(session_key: str) -> None:
    """Tăng turn count mà không cập nhật gì thêm (dùng cho greeting)."""
    state = _get_state(session_key) or _init_state(session_key)
    state.turn_count += 1
    _save_state(state)


def delete_session(session_key: str) -> None:
    """Xóa session khi user đóng conversation."""
    _store.pop(session_key, None)


def gc_expired_sessions() -> int:
    """Dọn dẹp session hết TTL. Gọi định kỳ từ background task."""
    cutoff = _now() - _SESSION_TTL_SEC
    expired = [k for k, v in _store.items() if v.get("updated_at", 0) < cutoff]
    for k in expired:
        del _store[k]
    if expired:
        logger.info("GC: removed %d expired sessions", len(expired))
    return len(expired)


_WEB_CACHE_TTL_SEC = 600  # Web results cache tồn tại 10 phút trong session


def _topic_key(query: str) -> str:
    """Trích topic key từ query — dùng để lookup web cache theo chủ đề."""
    import re
    # Nếu query có prefix "Chủ đề hiện tại: X. Câu hỏi: Y" → dùng X làm key
    m = re.match(r'^Chủ đề hiện tại:\s*([^.]+)\.', query.strip(), re.IGNORECASE)
    if m:
        return m.group(1).strip().lower()
    # Fallback: dùng 4 từ đầu của query
    words = query.strip().lower().split()
    return " ".join(words[:4])


def cache_web_results(session_key: str, query: str, web_context: str, web_citations: list) -> None:
    """Cache kết quả web search cho topic của query. Dùng lại cho follow-up cùng chủ đề."""
    if not session_key or not web_context:
        return
    state = _get_state(session_key) or _init_state(session_key)
    key = _topic_key(query)
    state.web_results[key] = {
        "context": web_context,
        "citations": web_citations,
        "cached_at": _now(),
    }
    # Giữ tối đa 5 topic khác nhau trong session
    if len(state.web_results) > 5:
        oldest = min(state.web_results, key=lambda k: state.web_results[k].get("cached_at", 0))
        del state.web_results[oldest]
    _save_state(state)


def get_cached_web_results(session_key: str, query: str) -> tuple[str, list] | None:
    """Lấy web results đã cache cho topic. Trả về (context, citations) hoặc None nếu miss/hết hạn."""
    if not session_key:
        return None
    state = _get_state(session_key)
    if not state:
        return None
    key = _topic_key(query)
    cached = state.web_results.get(key)
    if not cached:
        return None
    if _now() - cached.get("cached_at", 0) > _WEB_CACHE_TTL_SEC:
        del state.web_results[key]
        _save_state(state)
        return None
    return cached["context"], cached["citations"]