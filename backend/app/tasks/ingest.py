"""Celery tasks — chạy pipeline index tài liệu bất đồng bộ."""

import asyncio
import logging

from celery import Celery

from app.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    "chatbot",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Ho_Chi_Minh",
    enable_utc=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)


@celery_app.task(bind=True, max_retries=8, default_retry_delay=15)
def ingest_document_task(self, document_id: str):
    """Task index tài liệu — chạy trong Celery worker."""
    try:
        from app.rag.ingestor import ingest_document
        asyncio.run(ingest_document(document_id))
        logger.info("Task ingest xong: %s", document_id)
    except Exception as exc:
        countdown = min(300, 15 * (2 ** self.request.retries))
        logger.warning("Task ingest sẽ retry: %s — %s (sau %ss)", document_id, exc, countdown)
        try:
            raise self.retry(exc=exc, countdown=countdown)
        except self.MaxRetriesExceededError:
            # Hết lần retry → đánh dấu document là error
            _mark_document_failed(document_id, f"Đã thử {self.max_retries} lần nhưng không thành công: {exc}")
            logger.error("Task ingest hết retry cho %s: %s", document_id, exc)


def _mark_document_failed(document_id: str, message: str) -> None:
    """Đánh dấu document là error khi ingest task hết retry."""
    try:
        from app.rag.ingestor import AsyncSessionLocal
        from app.models.db import Document
        from sqlalchemy import select
        from uuid import UUID

        async def _set_failed():
            async with AsyncSessionLocal() as db:
                stmt = select(Document).where(Document.id == UUID(document_id))
                doc = (await db.execute(stmt)).scalar_one_or_none()
                if doc:
                    doc.status = "error"
                    doc.error_message = message[:500]
                    await db.commit()

        asyncio.run(_set_failed())
    except Exception as e:
        logger.error("Không thể đánh dấu document failed: %s — %s", document_id, e)
