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


def _run_async(coro):
    """
    Chạy coroutine an toàn trong môi trường Celery worker.

    asyncio.run() sẽ raise RuntimeError nếu đã có event loop đang chạy
    (ví dụ khi dùng gevent/eventlet pool hoặc một số môi trường đặc biệt).
    Hàm này xử lý cả hai trường hợp:
      - Không có event loop → tạo mới và chạy (asyncio.run thông thường)
      - Đã có event loop → chạy trong thread riêng biệt để tránh xung đột
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Event loop đang chạy (gevent/eventlet/nested) → chạy trong thread mới
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        # Không có event loop trong thread này → tạo mới
        return asyncio.run(coro)


@celery_app.task(bind=True, max_retries=8, default_retry_delay=15)
def ingest_document_task(self, document_id: str):
    """Task index tài liệu — chạy trong Celery worker."""
    try:
        from app.rag.ingestor import ingest_document
        _run_async(ingest_document(document_id))
        logger.info("Task ingest xong: %s", document_id)
    except Exception as exc:
        # Nếu là lỗi rate-limit → retry với backoff, KHÔNG đánh dấu error ngay
        from app.rag.embedder import _is_rate_limited_error
        if _is_rate_limited_error(exc):
            countdown = min(300, 15 * (2 ** self.request.retries))
            logger.warning(
                "Task ingest bị rate-limit, retry sau %ss: %s — %s",
                countdown, document_id, exc,
            )
            try:
                raise self.retry(exc=exc, countdown=countdown)
            except self.MaxRetriesExceededError:
                _mark_document_failed(
                    document_id,
                    f"Embedding bị giới hạn tần suất sau {self.max_retries} lần thử: {exc}",
                )
                logger.error("Task ingest hết retry (rate-limit) cho %s: %s", document_id, exc)
            return

        # Các lỗi khác (kể cả RuntimeError từ asyncio, lỗi DB, …)
        # → đánh dấu error ngay, KHÔNG retry vô ích để tránh treo pending
        logger.error("Task ingest thất bại (không retry): %s — %s", document_id, exc, exc_info=True)
        _mark_document_failed(document_id, f"Lỗi xử lý: {exc}")


def _mark_document_failed(document_id: str, message: str) -> None:
    """Đánh dấu document là error. Dùng _run_async để tránh xung đột event loop."""
    try:
        from app.models.db import AsyncSessionLocal, Document
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

        _run_async(_set_failed())
    except Exception as e:
        logger.error("Không thể đánh dấu document failed: %s — %s", document_id, e)
