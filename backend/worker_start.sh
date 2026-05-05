#!/bin/sh
# Render Free plan: web service bắt buộc phải respond health check trên $PORT
# Chạy python -m http.server (built-in, không cần cài thêm) song song với Celery
python -m http.server "${PORT:-10000}" --bind 0.0.0.0 &

echo "==> Health server started on port ${PORT:-10000}"
echo "==> Starting Celery worker..."

exec celery -A app.tasks.ingest.celery_app worker \
    --loglevel=info \
    --concurrency=2 \
    -Q celery
