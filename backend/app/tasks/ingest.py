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
        raise self.retry(exc=exc, countdown=countdown)
