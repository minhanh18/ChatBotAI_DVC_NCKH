"""Database setup with SQLAlchemy async + pgvector."""

from datetime import datetime
from typing import Any
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Float, ForeignKey,
    Integer, LargeBinary, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, relationship

from app.config import settings

# ── Engine ────────────────────────────────────────────────────────────────────
# Render Postgres free tier đóng idle connection sau ~240s (4 phút).
# pool_recycle=180 đảm bảo connection bị recycle trước khi server đóng.
# connect_args với ssl="require" + statement_timeout tránh connection treo.
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=5,             
    max_overflow=2,          
    pool_recycle=180,
    pool_pre_ping=True,
    pool_timeout=30,      
    echo=settings.DEBUG,
    # DI CHUYỂN RA ĐÂY:
    prepared_statement_cache_size=0, 
    # connect_args chỉ giữ lại các tham số truyền trực tiếp cho driver asyncpg
    connect_args={
        "ssl": "require",
        "server_settings": {
            "statement_timeout": "30000",
            "idle_in_transaction_session_timeout": "60000",
        },
    },
)

AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


# ── Base ──────────────────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    pass


def now_utc() -> datetime:
    return datetime.utcnow()


# ══════════════════════════════════════════════════════════════════════════════
#  DATASET / DOCUMENT MODELS
# ══════════════════════════════════════════════════════════════════════════════

class Dataset(Base):
    """Bộ tài liệu nội bộ — mỗi Dataset là 1 nhóm tài liệu."""
    __tablename__ = "datasets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=now_utc, nullable=False)
    updated_at = Column(DateTime, default=now_utc, onupdate=now_utc, nullable=False)

    documents = relationship("Document", back_populates="dataset", cascade="all, delete-orphan")


class Document(Base):
    """Tài liệu được upload vào hệ thống."""
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dataset_id = Column(UUID(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(500), nullable=False)
    file_path = Column(String(1000), nullable=True)
    file_content = Column(LargeBinary, nullable=True)   # backup bytes — serve khi ephemeral fs mất file
    file_type = Column(String(50), nullable=True)
    file_size = Column(BigInteger, default=0)
    status = Column(String(20), default="pending", nullable=False)
    error_message = Column(Text, nullable=True)
    chunk_count = Column(Integer, default=0)
    meta = Column(JSONB, default=dict)
    created_at = Column(DateTime, default=now_utc, nullable=False)
    updated_at = Column(DateTime, default=now_utc, onupdate=now_utc, nullable=False)

    dataset = relationship("Dataset", back_populates="documents")
    segments = relationship("DocumentSegment", back_populates="document", cascade="all, delete-orphan")


class DocumentSegment(Base):
    """Chunk của tài liệu sau khi chunking + embedding."""
    __tablename__ = "document_segments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    dataset_id = Column(UUID(as_uuid=True), nullable=False)
    position = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    word_count = Column(Integer, default=0)
    embedding = Column(Vector(settings.EMBEDDING_DIMENSION), nullable=True)
    meta = Column(JSONB, default=dict)
    created_at = Column(DateTime, default=now_utc, nullable=False)

    document = relationship("Document", back_populates="segments")


# ══════════════════════════════════════════════════════════════════════════════
#  CONVERSATION / CHAT MODELS
# ══════════════════════════════════════════════════════════════════════════════

class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String(500), default="Hội thoại mới")
    session_key = Column(String(255), nullable=True, index=True)
    created_at = Column(DateTime, default=now_utc, nullable=False)
    updated_at = Column(DateTime, default=now_utc, onupdate=now_utc, nullable=False)

    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan",
                            order_by="Message.created_at")


class Message(Base):
    __tablename__ = "messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    answer_mode = Column(String(20), nullable=True)
    citations = Column(JSONB, default=list)
    tokens_used = Column(Integer, default=0)
    latency_ms = Column(Integer, default=0)
    created_at = Column(DateTime, default=now_utc, nullable=False)

    conversation = relationship("Conversation", back_populates="messages")


# ══════════════════════════════════════════════════════════════════════════════
#  ADMIN / MONITORING MODELS
# ══════════════════════════════════════════════════════════════════════════════

class MessageFeedback(Base):
    """Phản hồi like / dislike cho câu trả lời của bot."""
    __tablename__ = "message_feedback"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    message_id = Column(UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True, index=True)
    rating = Column(String(20), nullable=False)
    issue_type = Column(String(100), nullable=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=now_utc, nullable=False)


class UsageLog(Base):
    """Log mỗi lượt chat để admin giám sát."""
    __tablename__ = "usage_logs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id = Column(UUID(as_uuid=True), nullable=True)
    message_id = Column(UUID(as_uuid=True), nullable=True)
    query_text = Column(Text, nullable=True)
    answer_mode = Column(String(20), nullable=True)
    tokens_used = Column(Integer, default=0)
    latency_ms = Column(Integer, default=0)
    is_rag = Column(Boolean, default=False)
    retrieved_chunks = Column(Integer, default=0)
    created_at = Column(DateTime, default=now_utc, nullable=False)
