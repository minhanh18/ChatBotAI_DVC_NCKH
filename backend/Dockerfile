FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY worker_start.sh ./worker_start.sh
RUN chmod +x ./worker_start.sh
# cache-bust: force rebuild v3.9.3

RUN mkdir -p /tmp/uploads

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PORT=8000
ENV UVICORN_WORKERS=2
# 2 workers: 1 xử lý ingest nặng, 1 vẫn nhận chat request bình thường.
# Render free tier RAM ~512MB: 2 workers × ~150MB idle = ~300MB, còn buffer cho ingest.
# Nếu upgrade plan (1GB+), có thể tăng lên 3-4.

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${UVICORN_WORKERS:-2}"]
