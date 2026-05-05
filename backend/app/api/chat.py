"""Chat API — endpoints hội thoại với SSE streaming."""

from __future__ import annotations

import io
import json
import re
from datetime import timezone, datetime
from typing import Optional
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from PIL import Image
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.router import AnswerMode, RouteDecision, agent_router
from app.chat.engine import chat_engine
from app.chat.evaluator import is_greeting_query, is_out_of_domain, out_of_domain_reply
from app.config import settings
from app.models.db import Conversation, Message, MessageFeedback, UsageLog, get_db, now_utc

router = APIRouter(prefix="/chat", tags=["chat"])
APP_TZ = ZoneInfo(settings.APP_TIMEZONE)


class ChatRequest(BaseModel):
    query: str
    conversation_id: Optional[str] = None
    dataset_id: Optional[str] = None
    mode: Optional[str] = None
    session_key: Optional[str] = None


class FeedbackRequest(BaseModel):
    rating: str
    issue_type: Optional[str] = None
    description: Optional[str] = None
    toggle: bool = False


@router.post("/stream")
async def chat_stream(req: ChatRequest, db: AsyncSession = Depends(get_db)):
    import time as _time
    _request_start_ms = _time.time()  # bắt đầu đo latency từ khi nhận request
    if not req.query.strip():
        raise HTTPException(400, "Query không được để trống")

    conversation = await _get_or_create_conversation(db, req.conversation_id, req.session_key)
    user_msg = await _save_user_message(db, conversation, req.query)
    history = await _load_history(db, conversation.id, exclude_message_id=user_msg.id)

    # Từ chối ngay nếu câu hỏi rõ ràng ngoài lĩnh vực hành chính/pháp lý
    if is_out_of_domain(req.query) and not is_greeting_query(req.query):
        ood_text = out_of_domain_reply()
        async def _ood_stream():
            yield f"data: {json.dumps({'type': 'conversation_id', 'data': str(conversation.id)})}\n\n"
            yield f"data: {json.dumps({'type': 'mode', 'data': 'ai'})}\n\n"
            for part in _split_static_stream_text(ood_text):
                yield f"data: {json.dumps({'type': 'token', 'data': part})}\n\n"
            from app.utils.data_crypto import mask_pii as _mask_pii
            assistant_msg = Message(
                conversation_id=conversation.id,
                role="assistant",
                content=ood_text,
                answer_mode="ai",
                citations=[],
                tokens_used=max(1, len(ood_text) // 4),
                latency_ms=0,
            )
            conversation.updated_at = datetime.utcnow()
            db.add(assistant_msg)
            await db.flush()
            db.add(UsageLog(
                conversation_id=conversation.id,
                message_id=assistant_msg.id,
                query_text=_mask_pii(req.query[:500]),
                answer_mode="ai",
                tokens_used=max(1, len(ood_text) // 4),
                latency_ms=0,
                is_rag=False,
                retrieved_chunks=0,
            ))
            await db.commit()
            yield f"data: {json.dumps({'type': 'done', 'data': {'tokens': max(1, len(ood_text) // 4), 'latency_ms': 0}})}\n\n"
        return _streaming_response(_ood_stream)

    clarification_text = _context_clarification_text(req.query, history)
    if clarification_text:
        async def event_stream():
            yield f"data: {json.dumps({'type': 'conversation_id', 'data': str(conversation.id)})}\n\n"
            yield f"data: {json.dumps({'type': 'mode', 'data': 'ai'})}\n\n"
            for part in _split_static_stream_text(clarification_text):
                yield f"data: {json.dumps({'type': 'token', 'data': part})}\n\n"
            assistant_msg = Message(
                conversation_id=conversation.id,
                role='assistant',
                content=clarification_text,
                answer_mode='ai',
                citations=[],
                tokens_used=max(1, len(clarification_text) // 4),
                latency_ms=0,
            )
            conversation.updated_at = now_utc()
            db.add(assistant_msg)
            await db.flush()
            from app.utils.data_crypto import mask_pii
            db.add(UsageLog(
                conversation_id=conversation.id,
                message_id=assistant_msg.id,
                query_text=mask_pii(req.query[:500]),
                answer_mode='ai',
                tokens_used=max(1, len(clarification_text) // 4),
                latency_ms=0,
                is_rag=False,
                retrieved_chunks=0,
            ))
            await db.commit()
            yield f"data: {json.dumps({'type': 'done', 'data': {'tokens': max(1, len(clarification_text) // 4), 'latency_ms': 0}})}\n\n"
        return _streaming_response(event_stream)

    effective_query = _resolve_effective_query(req.query, history)

    requested_mode = AnswerMode(req.mode) if req.mode in ("rag", "ai") else None
    force_web = await _should_auto_force_web(db, conversation.id, req.query)
    # Không ép AI/search trước khi RAG được kiểm tra. force_web chỉ được dùng ở tầng fallback
    # hoặc khi router đã quyết định AI vì tài liệu không đủ khớp.
    force_mode = requested_mode
    decision = await agent_router.route(
        query=effective_query,
        db=db,
        dataset_id=req.dataset_id,
        force_mode=force_mode,
    )
    # Chỉ bật web khi router đã kết luận AI sau bước retrieve, hoặc người dùng hỏi cần kiểm chứng mới nhất.
    # Nếu router chọn RAG (có chunks) thì KHÔNG ép force_web — engine sẽ tự fallback web khi RAG trả [[RAG_NO_ANSWER]].
    # force_web chỉ áp dụng khi router đã chọn AI (không tìm được chunk liên quan).
    rag_has_chunks = decision.mode == AnswerMode.RAG and bool(decision.chunks)
    force_web = bool(
        (not rag_has_chunks and force_web)
        or (decision.mode == AnswerMode.AI and decision.assessment.get("should_force_web"))
    )

    async def event_stream():
        yield f"data: {json.dumps({'type': 'conversation_id', 'data': str(conversation.id)})}\n\n"
        async for chunk in chat_engine.stream_response(
            query=effective_query,
            decision=decision,
            history=history,
            db=db,
            conversation=conversation,
            force_web=force_web,
            session_key=req.session_key,
            request_start_ms=_request_start_ms,
        ):
            yield chunk

    return _streaming_response(event_stream)


@router.post("/stream-image")
async def chat_stream_image(
    query: str = Form(""),
    conversation_id: Optional[str] = Form(None),
    session_key: Optional[str] = Form(None),
    image: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    clean_query = query.strip() or "Hãy phân tích hình ảnh này."

    if not image.filename:
        raise HTTPException(400, "Bạn chưa chọn hình ảnh")
    if not (image.content_type or "").startswith("image/"):
        raise HTTPException(400, "Chỉ hỗ trợ tệp hình ảnh")

    content = await image.read()
    size_mb = len(content) / (1024 * 1024)
    if size_mb > settings.MAX_CHAT_IMAGE_SIZE_MB:
        raise HTTPException(400, f"Hình ảnh quá lớn. Tối đa {settings.MAX_CHAT_IMAGE_SIZE_MB}MB")

    try:
        pil_image = Image.open(io.BytesIO(content))
        pil_image.load()
    except Exception as exc:
        raise HTTPException(400, f"Không thể đọc hình ảnh: {exc}") from exc

    conversation = await _get_or_create_conversation(db, conversation_id, session_key)
    user_text = clean_query + f"\n\n[Hình ảnh đính kèm: {image.filename}]"
    user_msg = await _save_user_message(db, conversation, user_text)
    history = await _load_history(db, conversation.id, exclude_message_id=user_msg.id)
    effective_query = _resolve_effective_query(clean_query, history)

    decision = RouteDecision(AnswerMode.AI, [], reason="image_upload", assessment={"reason": "image_upload"})

    async def event_stream():
        yield f"data: {json.dumps({'type': 'conversation_id', 'data': str(conversation.id)})}\n\n"
        async for chunk in chat_engine.stream_response(
            query=effective_query,
            decision=decision,
            history=history,
            db=db,
            conversation=conversation,
            image_part=pil_image,
        ):
            yield chunk

    return _streaming_response(event_stream)


@router.get("/conversations")
async def list_conversations(session_key: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    stmt = select(Conversation).order_by(Conversation.updated_at.desc()).limit(50)
    if session_key:
        from app.utils.data_crypto import pseudonymise_session_key
        stmt = stmt.where(Conversation.session_key == pseudonymise_session_key(session_key))
    result = await db.execute(stmt)
    convs = result.scalars().all()
    return [
        {
            "id": str(c.id),
            "title": c.title,
            "created_at": _to_app_iso(c.created_at),
            "updated_at": _to_app_iso(c.updated_at),
        }
        for c in convs
    ]


@router.get("/conversations/{conversation_id}/messages")
async def get_messages(conversation_id: str, db: AsyncSession = Depends(get_db)):
    stmt = select(Message).where(Message.conversation_id == UUID(conversation_id)).order_by(Message.created_at)
    result = await db.execute(stmt)
    msgs = result.scalars().all()

    feedback_result = await db.execute(
        select(MessageFeedback)
        .where(MessageFeedback.conversation_id == UUID(conversation_id))
        .order_by(MessageFeedback.created_at.desc())
    )
    latest_feedback_by_message: dict[str, str] = {}
    for feedback in feedback_result.scalars().all():
        key = str(feedback.message_id)
        if key not in latest_feedback_by_message:
            latest_feedback_by_message[key] = feedback.rating

    return [
        {
            "id": str(m.id),
            "role": m.role,
            "content": m.content,
            "answer_mode": m.answer_mode,
            "citations": m.citations or [],
            "created_at": _to_app_iso(m.created_at),
            "feedback": latest_feedback_by_message.get(str(m.id)),
        }
        for m in msgs
    ]


@router.post("/messages/{message_id}/feedback")
async def submit_feedback(message_id: str, payload: FeedbackRequest, db: AsyncSession = Depends(get_db)):
    rating = (payload.rating or "").strip().lower()
    if rating not in {"like", "dislike"}:
        raise HTTPException(400, "rating phải là like hoặc dislike")

    stmt = select(Message).where(Message.id == UUID(message_id))
    msg = (await db.execute(stmt)).scalar_one_or_none()
    if not msg:
        raise HTTPException(404, "Message không tồn tại")
    if msg.role != "assistant":
        raise HTTPException(400, "Chỉ ghi nhận phản hồi cho câu trả lời của trợ lý")

    existing_result = await db.execute(
        select(MessageFeedback)
        .where(MessageFeedback.message_id == msg.id)
        .order_by(MessageFeedback.created_at.desc())
    )
    existing_feedbacks = list(existing_result.scalars().all())
    current_feedback = existing_feedbacks[0] if existing_feedbacks else None

    if current_feedback and current_feedback.rating == rating and payload.toggle:
        await db.execute(delete(MessageFeedback).where(MessageFeedback.message_id == msg.id))
        await db.commit()
        return {"message": "Đã hủy đánh giá trước đó.", "feedback": None}

    await db.execute(delete(MessageFeedback).where(MessageFeedback.message_id == msg.id))

    feedback = MessageFeedback(
        message_id=msg.id,
        conversation_id=msg.conversation_id,
        rating=rating,
        issue_type=(payload.issue_type or None),
        description=(payload.description or None),
    )
    db.add(feedback)
    await db.commit()

    thanks = "Cảm ơn bạn đã đánh giá tích cực." if rating == "like" else "Cảm ơn bạn đã góp ý. Mình đã ghi nhận phản hồi này."
    return {"message": thanks, "feedback": rating}


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, db: AsyncSession = Depends(get_db)):
    stmt = select(Conversation).where(Conversation.id == UUID(conversation_id))
    conv = (await db.execute(stmt)).scalar_one_or_none()
    if not conv:
        raise HTTPException(404, "Conversation không tồn tại")

    # Xóa session memory ngay khi conversation bị xóa
    try:
        from app.chat.session_cache import delete_session
        if conv.session_key:
            delete_session(conv.session_key)
    except Exception:
        pass

    marker = f"deleted::{conv.session_key or 'anon'}::{uuid4().hex}"
    conv.session_key = marker
    conv.updated_at = now_utc()
    await db.commit()
    return {"message": "Đã xoá"}


async def _get_or_create_conversation(db: AsyncSession, conversation_id: Optional[str], session_key: Optional[str]) -> Conversation:
    if conversation_id:
        stmt = select(Conversation).where(Conversation.id == UUID(conversation_id))
        conv = (await db.execute(stmt)).scalar_one_or_none()
        if conv:
            return conv

    # Mã hoá session_key trước khi lưu DB (pseudonymisation per Luật ANM 2018 / NĐ 13/2023)
    from app.utils.data_crypto import pseudonymise_session_key
    safe_key = pseudonymise_session_key(session_key) if session_key else session_key
    conv = Conversation(session_key=safe_key)
    db.add(conv)
    await db.flush()
    return conv


def _build_conversation_title(content: str) -> str:
    first_line = (content or "").splitlines()[0].strip()
    first_line = first_line.replace("[Hình ảnh đính kèm:", "").strip()
    if not first_line:
        return "Hội thoại mới"
    title = " ".join(first_line.split())
    return title[:77] + "..." if len(title) > 80 else title


def _is_context_light_query(query: str) -> bool:
    normalized = " ".join((query or "").split()).lower()
    if not normalized:
        return True

    # Các câu follow-up thường ngắn, dùng đại từ/ẩn chủ ngữ hoặc viết tắt (đk = đăng ký).
    followup_patterns = [
        "nào", "những trường hợp", "trường hợp nào", "bắt buộc", "có bắt buộc",
        "phải không", "cần không", "được không", "làm sao", "như nào", "như thế nào",
        "bao nhiêu", "lệ phí", "mức phạt", "phạt bao nhiêu", "điều kiện sao",
        "thủ tục sao", "chi phí sao", "ở đâu", "khi nào", "ai phải", "ai được",
        "đk", "đăng kí", "đăng ký", "nó", "việc này", "cái này", "trường hợp đó",
        "còn hiệu lực không", "có miễn không", "mới nhất",
    ]
    if len(normalized) <= 56:
        return True
    return any(pattern in normalized for pattern in followup_patterns)


def _normalize_followup_query_text(text: str) -> str:
    text = " ".join((text or "").split())
    text = re.sub(r"\bđk\b", "đăng ký", text, flags=re.IGNORECASE)
    text = re.sub(r"\bđăng kí\b", "đăng ký", text, flags=re.IGNORECASE)
    return text


_TOPIC_PATTERNS: list[tuple[str, str]] = [
    (r"\b(tạm\s+trú|đăng\s+ký\s+tạm\s+trú|gia\s+hạn\s+tạm\s+trú)\b", "đăng ký tạm trú"),
    (r"\b(thường\s+trú|đăng\s+ký\s+thường\s+trú)\b", "đăng ký thường trú"),
    (r"\b(cư\s+trú)\b", "cư trú"),
    (r"\b(căn\s+cước|cccd|căn\s+cước\s+công\s+dân)\b", "căn cước công dân"),
    (r"\b(khai\s+sinh|hộ\s+tịch)\b", "hộ tịch/khai sinh"),
    (r"\b(hộ\s+kinh\s+doanh|đăng\s+ký\s+kinh\s+doanh|thành\s+lập\s+hộ\s+kinh\s+doanh)\b", "hộ kinh doanh"),
    (r"\b(tndn|thuế\s+thu\s+nhập\s+doanh\s+nghiệp|thuế\s+tndn)\b", "thuế thu nhập doanh nghiệp"),
    (r"\b(gtgt|vat|thuế\s+giá\s+trị\s+gia\s+tăng)\b", "thuế giá trị gia tăng"),
    (r"\b(bảo\s+hiểm\s+xã\s+hội|bhxh|bảo\s+hiểm\s+y\s+tế|bhyt)\b", "bảo hiểm"),
    (r"\b(đất\s+đai|sổ\s+đỏ|quyền\s+sử\s+dụng\s+đất)\b", "đất đai"),
]


def _extract_topics(text: str) -> list[str]:
    normalized = _normalize_followup_query_text(text).lower()
    topics: list[str] = []
    for pattern, label in _TOPIC_PATTERNS:
        if re.search(pattern, normalized, flags=re.IGNORECASE) and label not in topics:
            topics.append(label)
    return topics


def _is_self_contained_query(query: str) -> bool:
    q = _normalize_followup_query_text(query).lower()
    if _extract_topics(q):
        return True
    if len(q) > 80:
        return True
    return False


def _collect_conversation_topics(history: list[Message]) -> list[str]:
    topics: list[str] = []
    for msg in history:
        if msg.role != "user":
            continue
        for topic in _extract_topics(msg.content or ""):
            if topic not in topics:
                topics.append(topic)
    return topics


def _split_static_stream_text(text: str, chunk_size: int = 80):
    for i in range(0, len(text), chunk_size):
        yield text[i:i + chunk_size]


def _context_clarification_text(query: str, history: list[Message]) -> str | None:
    clean_query = _normalize_followup_query_text(query)
    if not clean_query or _is_self_contained_query(clean_query) or not _is_context_light_query(clean_query):
        return None
    topics = _collect_conversation_topics(history)
    if len(topics) <= 1:
        return None
    topics_text = ", ".join(topics[-4:])
    return (
        "Mình chưa chắc bạn đang hỏi tiếp theo chủ đề nào trong phiên này. "
        f"Trước đó có nhiều chủ đề khác nhau: {topics_text}.\n\n"
        "Bạn vui lòng nói rõ bạn muốn hỏi về chủ đề nào để mình tra cứu và trả lời chính xác hơn."
    )


def _resolve_effective_query(query: str, history: list[Message]) -> str:
    """Tạo truy vấn hiệu lực ngắn gọn cho RAG/search.

    Quan trọng: không nhét toàn bộ tóm tắt phản hồi vào query truy xuất/web search, vì sẽ làm
    Tavily/search nhận query quá dài và lẫn chủ đề cũ. Log hội thoại chỉ dùng để xác định chủ đề.
    """
    clean_query = _normalize_followup_query_text(query)
    if not clean_query:
        return query
    if _is_self_contained_query(clean_query) or not _is_context_light_query(clean_query):
        return clean_query

    topics = _collect_conversation_topics(history)
    if len(topics) == 1:
        return f"Chủ đề hiện tại: {topics[0]}. Câu hỏi: {clean_query}"

    # Không rõ chủ đề hoặc nhiều chủ đề: giữ nguyên để hệ thống hỏi làm rõ/không kéo nhiễu.
    return clean_query


_RECHECK_HINTS = [
    "bạn trả lời sai", "trả lời sai", "câu trước sai", "phản hồi trước sai",
    "sai rồi", "không đúng", "chưa đúng",
    "xem lại", "kiểm tra lại", "tra lại", "đối chiếu lại", "tra ngược lại",
]

_LEGAL_FRESHNESS_HINTS = [
    "mới nhất", "hiện hành", "hiện nay", "hiện tại",
    "còn hiệu lực", "đang có hiệu lực", "hiệu lực",
    "vừa sửa đổi", "vừa bổ sung", "sửa đổi", "bổ sung",
    "thay thế", "thay đổi", "quy định mới", "luật mới", "nghị định mới", "thông tư mới",
]

_LEGAL_OBJECT_HINTS = [
    "luật", "bộ luật", "nghị định", "thông tư", "quyết định", "quy định",
    "điều", "khoản", "điểm", "xử phạt", "mức phạt", "lệ phí", "thủ tục", "hành chính",
    "căn cứ pháp lý", "cư trú", "tạm trú", "thường trú", "cccd", "căn cước", "hộ tịch", "thuế",
]


def _contains_any(text: str, keywords: list[str]) -> bool:
    text = " ".join((text or "").split()).lower()
    return any(keyword in text for keyword in keywords)


def _is_recheck_query(query: str) -> bool:
    return _contains_any(query, _RECHECK_HINTS)


def _is_legal_freshness_query(query: str) -> bool:
    q = " ".join((query or "").split()).lower()
    return _contains_any(q, _LEGAL_OBJECT_HINTS) and _contains_any(q, _LEGAL_FRESHNESS_HINTS)


async def _latest_assistant_feedback_is_dislike(db: AsyncSession, conversation_id: UUID) -> bool:
    last_assistant_stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.role == "assistant")
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    last_assistant = (await db.execute(last_assistant_stmt)).scalars().first()
    if not last_assistant:
        return False

    feedback_stmt = (
        select(MessageFeedback)
        .where(MessageFeedback.message_id == last_assistant.id)
        .order_by(MessageFeedback.created_at.desc())
        .limit(1)
    )
    latest_feedback = (await db.execute(feedback_stmt)).scalars().first()
    return bool(latest_feedback and latest_feedback.rating == "dislike")


async def _should_auto_force_web(db: AsyncSession, conversation_id: UUID, query: str) -> bool:
    if _is_legal_freshness_query(query):
        return True
    if _is_recheck_query(query):
        return True
    if _is_context_light_query(query) and await _latest_assistant_feedback_is_dislike(db, conversation_id):
        return True
    return False


async def _save_user_message(db: AsyncSession, conversation: Conversation, content: str) -> Message:
    user_msg = Message(conversation_id=conversation.id, role="user", content=content)
    if not conversation.title or conversation.title == "Hội thoại mới":
        conversation.title = _build_conversation_title(content)
    conversation.updated_at = now_utc()
    db.add(user_msg)
    await db.commit()
    return user_msg


async def _load_history(db: AsyncSession, conversation_id: UUID, exclude_message_id: Optional[UUID] = None) -> list[Message]:
    stmt = select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at)
    if exclude_message_id:
        stmt = stmt.where(Message.id != exclude_message_id)
    history_result = await db.execute(stmt)
    return list(history_result.scalars().all())


def _streaming_response(event_stream):
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _to_app_iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(APP_TZ).isoformat()
