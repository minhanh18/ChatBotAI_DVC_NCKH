"""Chat API — endpoints hội thoại với SSE streaming."""

from __future__ import annotations

import io
import json
from datetime import timezone
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
from app.config import settings
from app.models.db import Conversation, Message, MessageFeedback, get_db, now_utc

router = APIRouter(prefix="/chat", tags=["chat"])
APP_TZ = ZoneInfo(settings.APP_TIMEZONE)


class ChatRequest(BaseModel):
    query: str
    conversation_id: Optional[str] = None
    dataset_id: Optional[str] = None
    mode: Optional[str] = None
    session_key: Optional[str] = None
    use_web: bool = False


class ConversationOut(BaseModel):
    id: str
    title: str
    created_at: str

    class Config:
        from_attributes = True


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    answer_mode: Optional[str]
    citations: list
    created_at: str
    feedback: Optional[str] = None

    class Config:
        from_attributes = True


class FeedbackRequest(BaseModel):
    rating: str
    issue_type: Optional[str] = None
    description: Optional[str] = None
    toggle: bool = False


@router.post("/stream")
async def chat_stream(req: ChatRequest, db: AsyncSession = Depends(get_db)):
    if not req.query.strip():
        raise HTTPException(400, "Query không được để trống")

    conversation = await _get_or_create_conversation(db, req.conversation_id, req.session_key)
    user_msg = await _save_user_message(db, conversation, req.query)
    history = await _load_history(db, conversation.id, exclude_message_id=user_msg.id)
    effective_query = _resolve_effective_query(req.query, history)

    force_mode = AnswerMode(req.mode) if req.mode in ("rag", "ai") else None
    decision = await agent_router.route(
        query=effective_query,
        db=db,
        dataset_id=req.dataset_id,
        force_mode=force_mode,
        prefer_web=req.use_web,
    )

    async def event_stream():
        yield f"data: {json.dumps({'type': 'conversation_id', 'data': str(conversation.id)})}\n\n"
        async for chunk in chat_engine.stream_response(
            query=effective_query,
            decision=decision,
            history=history,
            db=db,
            conversation=conversation,
            use_web=req.use_web,
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

    decision = RouteDecision(AnswerMode.AI, [], reason="image_upload")

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
async def list_conversations(
    session_key: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Conversation).order_by(Conversation.updated_at.desc()).limit(50)
    if session_key:
        stmt = stmt.where(Conversation.session_key == session_key)
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
    stmt = (
        select(Message)
        .where(Message.conversation_id == UUID(conversation_id))
        .order_by(Message.created_at)
    )
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
    rating = (payload.rating or '').strip().lower()
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

    # Mỗi câu trả lời chỉ giữ đúng 1 bản ghi đánh giá hiện hành.
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

    # Ẩn hội thoại khỏi người dùng hiện tại nhưng vẫn giữ nguyên log/giám sát cho admin.
    marker = f"deleted::{conv.session_key or 'anon'}::{uuid4().hex}"
    conv.session_key = marker
    conv.updated_at = now_utc()
    await db.commit()
    return {"message": "Đã xoá"}


async def _get_or_create_conversation(
    db: AsyncSession,
    conversation_id: Optional[str],
    session_key: Optional[str],
) -> Conversation:
    if conversation_id:
        stmt = select(Conversation).where(Conversation.id == UUID(conversation_id))
        conv = (await db.execute(stmt)).scalar_one_or_none()
        if conv:
            return conv

    conv = Conversation(session_key=session_key)
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
    generic_patterns = [
        "bao nhiêu", "lệ phí", "mức phạt", "phạt bao nhiêu", "giá bao nhiêu", "như nào", "như thế nào",
        "còn hiệu lực không", "có miễn không", "mới nhất", "điều kiện sao", "thủ tục sao", "chi phí sao",
    ]
    if len(normalized) <= 24:
        return True
    return any(pattern in normalized for pattern in generic_patterns)


def _resolve_effective_query(query: str, history: list[Message]) -> str:
    clean_query = " ".join((query or "").split())
    if not clean_query:
        return query
    if not _is_context_light_query(clean_query):
        return clean_query

    previous_user_messages = [
        (msg.content or "").strip()
        for msg in reversed(history)
        if msg.role == "user" and (msg.content or "").strip()
    ]
    for previous in previous_user_messages:
        if previous != clean_query:
            return f"{previous}\n\nCâu hỏi tiếp theo cùng ngữ cảnh: {clean_query}"
    return clean_query


async def _save_user_message(db: AsyncSession, conversation: Conversation, content: str) -> Message:
    user_msg = Message(
        conversation_id=conversation.id,
        role="user",
        content=content,
    )
    if not conversation.title or conversation.title == "Hội thoại mới":
        conversation.title = _build_conversation_title(content)
    conversation.updated_at = now_utc()
    db.add(user_msg)
    await db.commit()
    return user_msg


async def _load_history(db: AsyncSession, conversation_id: UUID, exclude_message_id: Optional[UUID] = None) -> list[Message]:
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    )
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
