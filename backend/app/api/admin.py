"""
Admin API — giám sát hệ thống, thống kê sử dụng.
Bảo vệ bằng Basic Auth (config trong config.py).
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from zoneinfo import ZoneInfo

from app.config import settings
from app.models.db import Conversation, Dataset, Document, Message, MessageFeedback, UsageLog, get_db
from app.rag.lifecycle import lifecycle_status, version_of

router = APIRouter(prefix="/admin", tags=["admin"])
security = HTTPBasic()
APP_TZ = ZoneInfo(settings.APP_TIMEZONE)


def _to_app_iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(APP_TZ).isoformat()


def _user_conversation_clause():
    return or_(Conversation.session_key.is_(None), ~Conversation.session_key.like("admin::%"))



def verify_admin(credentials: HTTPBasicCredentials = Depends(security)):
    ok_user = secrets.compare_digest(credentials.username, settings.ADMIN_USERNAME)
    ok_pass = secrets.compare_digest(credentials.password, settings.ADMIN_PASSWORD)
    if not (ok_user and ok_pass):
        raise HTTPException(
            401,
            "Sai thông tin xác thực",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


@router.post("/reset-monitoring")
async def reset_monitoring(db: AsyncSession = Depends(get_db), _=Depends(verify_admin)):
    # Xóa toàn bộ dữ liệu giám sát: logs, feedback, hội thoại, tin nhắn
    await db.execute(text("TRUNCATE TABLE usage_logs RESTART IDENTITY CASCADE"))
    await db.execute(text("TRUNCATE TABLE message_feedback RESTART IDENTITY CASCADE"))
    await db.execute(text("TRUNCATE TABLE messages RESTART IDENTITY CASCADE"))
    await db.execute(text("TRUNCATE TABLE conversations RESTART IDENTITY CASCADE"))
    await db.commit()
    # Đảm bảo transaction commit hoàn toàn trước khi frontend re-fetch
    await db.close()
    return {"message": "Đã đặt lại toàn bộ giám sát: hội thoại, tin nhắn, log và đánh giá."}


# ── Dashboard tổng quan ───────────────────────────────────────────────────────

@router.get("/dashboard")
async def dashboard(db: AsyncSession = Depends(get_db), _=Depends(verify_admin)):
    """Tổng quan hệ thống."""
    now = datetime.utcnow()
    since_24h = now - timedelta(hours=24)
    since_7d = now - timedelta(days=7)

    total_convs = (await db.execute(
        select(func.count()).select_from(Conversation).where(_user_conversation_clause())
    )).scalar()
    convs_24h = (await db.execute(
        select(func.count()).select_from(Conversation).where(_user_conversation_clause(), Conversation.created_at >= since_24h)
    )).scalar()

    total_msgs = (await db.execute(
        select(func.count())
        .select_from(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(_user_conversation_clause())
    )).scalar()
    msgs_24h = (await db.execute(
        select(func.count())
        .select_from(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(_user_conversation_clause(), Message.created_at >= since_24h)
    )).scalar()

    mode_stats = (await db.execute(
        select(UsageLog.answer_mode, func.count().label("cnt"))
        .join(Conversation, Conversation.id == UsageLog.conversation_id, isouter=True)
        .where(_user_conversation_clause(), UsageLog.created_at >= since_7d)
        .group_by(UsageLog.answer_mode)
    )).fetchall()
    mode_breakdown = {row.answer_mode: row.cnt for row in mode_stats}

    avg_latency = (await db.execute(
        select(func.avg(UsageLog.latency_ms))
        .join(Conversation, Conversation.id == UsageLog.conversation_id, isouter=True)
        .where(_user_conversation_clause(), UsageLog.created_at >= since_7d)
    )).scalar()

    total_tokens = (await db.execute(
        select(func.sum(UsageLog.tokens_used))
        .join(Conversation, Conversation.id == UsageLog.conversation_id, isouter=True)
        .where(_user_conversation_clause(), UsageLog.created_at >= since_7d)
    )).scalar()

    doc_stats = (await db.execute(
        select(Document.status, func.count().label("cnt"))
        .group_by(Document.status)
    )).fetchall()

    feedback_7d = (await db.execute(
        select(MessageFeedback.rating, func.count().label("cnt"))
        .join(Conversation, Conversation.id == MessageFeedback.conversation_id, isouter=True)
        .where(_user_conversation_clause(), MessageFeedback.created_at >= since_7d)
        .group_by(MessageFeedback.rating)
    )).fetchall()

    data = {
        "conversations": {"total": total_convs, "last_24h": convs_24h},
        "messages": {"total": total_msgs, "last_24h": msgs_24h},
        "answer_modes_7d": mode_breakdown,
        "avg_latency_ms_7d": round(float(avg_latency or 0), 1),
        "total_tokens_7d": int(total_tokens or 0),
        "documents": {row.status: row.cnt for row in doc_stats},
        "feedback_7d": {row.rating: row.cnt for row in feedback_7d},
    }
    return JSONResponse(content=data, headers={"Cache-Control": "no-store, no-cache, must-revalidate"})


# ── Usage logs ────────────────────────────────────────────────────────────────

@router.get("/logs")
async def usage_logs(
    limit: int = 50,
    offset: int = 0,
    mode: str | None = None,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_admin),
):
    stmt = (
        select(UsageLog)
        .join(Conversation, Conversation.id == UsageLog.conversation_id, isouter=True)
        .where(_user_conversation_clause())
        .order_by(UsageLog.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if mode:
        stmt = stmt.where(UsageLog.answer_mode == mode)

    result = await db.execute(stmt)
    logs = result.scalars().all()
    return [
        {
            "id": log.id,
            "conversation_id": str(log.conversation_id) if log.conversation_id else None,
            "query": log.query_text,
            "mode": log.answer_mode,
            "tokens": log.tokens_used,
            "latency_ms": log.latency_ms,
            "is_rag": log.is_rag,
            "retrieved_chunks": log.retrieved_chunks,
            "created_at": _to_app_iso(log.created_at),
        }
        for log in logs
    ]


# ── Daily chart data ──────────────────────────────────────────────────────────

@router.get("/stats/daily")
async def daily_stats(days: int = 14, db: AsyncSession = Depends(get_db), _=Depends(verify_admin)):
    """Số messages mỗi ngày trong N ngày gần nhất."""
    since = datetime.utcnow() - timedelta(days=days)
    result = await db.execute(
        text("""
            SELECT DATE(u.created_at) as day,
                   COUNT(*) as total,
                   SUM(CASE WHEN u.answer_mode = 'rag' THEN 1 ELSE 0 END) as rag_count,
                   SUM(CASE WHEN u.answer_mode = 'ai' THEN 1 ELSE 0 END) as ai_count,
                   SUM(CASE WHEN u.answer_mode IN ('ai_rag', 'ai+rag') THEN 1 ELSE 0 END) as ai_rag_count,
                   AVG(u.latency_ms) as avg_latency
            FROM usage_logs u
            LEFT JOIN conversations c ON c.id = u.conversation_id
            WHERE u.created_at >= :since
              AND (c.session_key IS NULL OR c.session_key NOT LIKE 'admin::%')
            GROUP BY DATE(u.created_at)
            ORDER BY day
        """),
        {"since": since},
    )
    rows = result.fetchall()
    return [
        {
            "day": str(row.day),
            "total": row.total,
            "rag": row.rag_count,
            "ai": row.ai_count,
            "ai_rag": getattr(row, "ai_rag_count", 0),
            "avg_latency_ms": round(float(row.avg_latency or 0), 1),
        }
        for row in rows
    ]


# ── Document management ───────────────────────────────────────────────────────

@router.get("/documents")
async def admin_list_documents(
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_admin),
):
    stmt = select(Document).order_by(Document.created_at.desc()).limit(200)
    if status:
        stmt = stmt.where(Document.status == status)
    result = await db.execute(stmt)
    docs = result.scalars().all()
    return [
        {
            "id": str(d.id),
            "dataset_id": str(d.dataset_id),
            "name": d.name,
            "status": d.status,
            "chunk_count": d.chunk_count,
            "file_size": d.file_size,
            "error": d.error_message,
            "version": version_of(d.meta),
            "lifecycle_status": lifecycle_status(d.meta),
            "created_at": _to_app_iso(d.created_at),
        }
        for d in docs
    ]


# ── Conversations ─────────────────────────────────────────────────────────────

@router.get("/conversations")
async def admin_conversations(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_admin),
):
    result = await db.execute(
        select(Conversation)
        .where(_user_conversation_clause())
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
    )
    convs = result.scalars().all()
    return [
        {
            "id": str(c.id),
            "title": c.title,
            "session_key": c.session_key,
            "created_at": _to_app_iso(c.created_at),
            "updated_at": _to_app_iso(c.updated_at),
        }
        for c in convs
    ]


@router.delete("/conversations/{conversation_id}")
async def admin_delete_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_admin),
):
    from uuid import UUID
    conv = (await db.execute(
        select(Conversation).where(Conversation.id == UUID(conversation_id))
    )).scalar_one_or_none()
    if not conv:
        raise HTTPException(404, "Không tìm thấy")
    await db.delete(conv)
    await db.commit()
    return {"message": "Đã xoá"}


@router.get("/feedback-logs")
async def feedback_logs(
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_admin),
):
    result = await db.execute(
        select(MessageFeedback, Message.content, Conversation.title)
        .join(Message, Message.id == MessageFeedback.message_id)
        .join(Conversation, Conversation.id == MessageFeedback.conversation_id, isouter=True)
        .where(_user_conversation_clause())
        .order_by(MessageFeedback.created_at.desc())
        .limit(limit)
        .offset(offset)
    )

    rows = result.all()
    return [
        {
            "id": feedback.id,
            "conversation_id": str(feedback.conversation_id) if feedback.conversation_id else None,
            "conversation_title": title or "Hội thoại",
            "message_id": str(feedback.message_id),
            "rating": feedback.rating,
            "issue_type": feedback.issue_type,
            "description": feedback.description,
            "answer_excerpt": (message_content[:180] + "…") if message_content and len(message_content) > 180 else (message_content or ""),
            "created_at": _to_app_iso(feedback.created_at),
        }
        for feedback, message_content, title in rows
    ]
