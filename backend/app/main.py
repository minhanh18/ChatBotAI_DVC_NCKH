"""
FastAPI application entry point.
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.admin import router as admin_router
from app.api.chat import router as chat_router
from app.api.documents import router as docs_router
from app.config import settings
from app.models.db import Base, engine

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(chat_router, prefix="/api")
app.include_router(docs_router, prefix="/api")
app.include_router(admin_router, prefix="/api")


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
@app.get("/api/health")
async def health_check():
    """Health check endpoint cho Render — phải trả 200 để service không bị restart."""
    return {"status": "ok", "version": settings.APP_VERSION}


@app.get("/")
async def root():
    return {"message": f"{settings.APP_NAME} API", "docs": "/api/docs"}


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    """Tạo bảng và extension pgvector khi khởi động."""
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)

            # ALTER TABLE file_content BYTEA: chỉ chạy khi cột chưa tồn tại.
            # Dùng statement_timeout ngắn để không block startup quá lâu.
            # Nếu R2 đã bật (USE_R2_STORAGE=True), cột này không cần thiết —
            # bỏ qua hoàn toàn để tránh timeout trên DB lớn.
            from app.config import settings as _settings
            if not getattr(_settings, "USE_R2_STORAGE", False):
                try:
                    # Kiểm tra cột đã tồn tại chưa trước khi ALTER (tránh lock bảng)
                    col_exists = await conn.execute(text("""
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'documents' AND column_name = 'file_content'
                    """))
                    if not col_exists.fetchone():
                        await conn.execute(text("SET LOCAL statement_timeout = '10s'"))
                        await conn.execute(text("""
                            ALTER TABLE documents
                            ADD COLUMN IF NOT EXISTS file_content BYTEA
                        """))
                        logger.info("✓ Column file_content added to documents")
                    else:
                        logger.info("✓ Column file_content already exists, skipping ALTER")
                except Exception as _alter_err:
                    logger.warning("ALTER TABLE file_content skipped (non-fatal): %s", _alter_err)
            else:
                logger.info("✓ USE_R2_STORAGE=True — skipping file_content column migration")

        logger.info("✓ Database ready")
    except Exception as e:
        logger.error("✗ Database startup error (non-fatal): %s", e)

    logger.info("✓ %s v%s started", settings.APP_NAME, settings.APP_VERSION)

    # Background GC cho session cache (mỗi 30 phút)
    async def _session_gc_loop():
        while True:
            await asyncio.sleep(1800)
            try:
                from app.chat.session_cache import gc_expired_sessions
                removed = gc_expired_sessions()
                if removed:
                    logger.info("Session GC: removed %d expired sessions", removed)
            except Exception as _e:
                logger.debug("Session GC error (ignored): %s", _e)

    asyncio.create_task(_session_gc_loop())

    # ── Self-ping keep-alive: tránh Render free tier ngủ đông ─────────────────
    # Render sẽ sleep service sau 15 phút không có traffic từ bên ngoài.
    # Self-ping mỗi 10 phút giữ service luôn thức.
    # Chỉ chạy khi có RENDER_EXTERNAL_URL (tức là đang chạy trên Render).
    _self_url = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not _self_url:
        # Fallback: tự build URL từ PORT
        _port = os.environ.get("PORT", "8000")
        _self_url = f"http://0.0.0.0:{_port}"

    async def _keep_alive_loop():
        await asyncio.sleep(60)  # chờ 1 phút sau startup trước khi bắt đầu
        while True:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    r = await client.get(f"{_self_url}/health")
                    logger.debug("Keep-alive ping: %s", r.status_code)
            except Exception as _e:
                logger.debug("Keep-alive ping failed (ignored): %s", _e)
            await asyncio.sleep(600)  # mỗi 10 phút

    asyncio.create_task(_keep_alive_loop())


@app.on_event("shutdown")
async def shutdown():
    await engine.dispose()
