"""
FastAPI application entry point.
"""

from __future__ import annotations

import logging

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


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    """Tạo bảng và extension pgvector khi khởi động."""
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        # Migration: thêm cột file_content nếu chưa có (ephemeral fs workaround)
        await conn.execute(text("""
            ALTER TABLE documents
            ADD COLUMN IF NOT EXISTS file_content BYTEA
        """))
    logger.info("✓ Database ready")
    logger.info("✓ %s v%s started", settings.APP_NAME, settings.APP_VERSION)

    # Background GC cho session cache (chạy mỗi 30 phút)
    async def _session_gc_loop():
        import asyncio as _asyncio
        while True:
            await _asyncio.sleep(1800)
            try:
                from app.chat.session_cache import gc_expired_sessions
                removed = gc_expired_sessions()
                if removed:
                    logger.info("Session GC: removed %d expired sessions", removed)
            except Exception as _e:
                logger.debug("Session GC error (ignored): %s", _e)

    import asyncio as _asyncio
    _asyncio.create_task(_session_gc_loop())


@app.on_event("shutdown")
async def shutdown():
    await engine.dispose()


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.APP_VERSION}


@app.get("/")
async def root():
    return {"message": f"{settings.APP_NAME} API", "docs": "/api/docs"}
