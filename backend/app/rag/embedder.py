"""
Embedding service — ưu tiên lại luồng embedding gần v8 để giữ chất lượng retrieve,
nhưng vẫn có fallback sang model mới nếu model cũ không còn khả dụng.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import google.generativeai as genai
import httpx

from app.config import settings

logger = logging.getLogger(__name__)

genai.configure(api_key=settings.GEMINI_API_KEY)

_BATCH_SIZE = 5
_RETRY_MAX = 6
_RETRY_DELAY = 3.0




def _is_rate_limited_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in ("429", "resource_exhausted", "quota", "too many requests", "rate limit"))


class EmbeddingService:
    def __init__(self, model: str = settings.EMBEDDING_MODEL):
        self.model = model
        self.fallback_model = settings.EMBEDDING_FALLBACK_MODEL

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i : i + _BATCH_SIZE]
            embeddings = await self._embed_batch_with_retry(batch, task_type="RETRIEVAL_DOCUMENT")
            all_embeddings.extend(embeddings)
        return all_embeddings

    async def embed_query(self, text: str) -> list[float]:
        [embedding] = await self._embed_batch_with_retry([text], task_type="RETRIEVAL_QUERY")
        return embedding

    async def _embed_batch_with_retry(self, texts: list[str], task_type: str) -> list[list[float]]:
        last_error: Exception | None = None
        for attempt in range(_RETRY_MAX):
            try:
                return await self._embed_batch_prefer_legacy(texts, task_type)
            except Exception as exc:
                last_error = exc
                if attempt < _RETRY_MAX - 1:
                    wait = (_RETRY_DELAY * (2 ** attempt)) + random.uniform(0.0, 1.0)
                    if _is_rate_limited_error(exc):
                        wait = max(wait, 8.0 + (attempt * 6.0))
                    logger.warning(
                        "Embedding batch thất bại (lần %d/%d): %s. Retry sau %.1fs",
                        attempt + 1,
                        _RETRY_MAX,
                        exc,
                        wait,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error("Embedding batch thất bại sau %d lần thử: %s", _RETRY_MAX, exc)
        assert last_error is not None
        raise last_error

    async def _embed_batch_prefer_legacy(self, texts: list[str], task_type: str) -> list[list[float]]:
        try:
            return await asyncio.to_thread(self._embed_batch_legacy_sync, texts, task_type)
        except Exception as exc:
            if not self._should_fallback(exc):
                raise
            logger.warning(
                "Legacy embedding model '%s' không dùng được, fallback sang '%s': %s",
                self.model,
                self.fallback_model,
                exc,
            )
            return await self._embed_batch_http(texts, task_type, self.fallback_model)

    def _embed_batch_legacy_sync(self, texts: list[str], task_type: str) -> list[list[float]]:
        result = genai.embed_content(
            model=f"models/{self.model}",
            content=texts,
            task_type=task_type,
            output_dimensionality=settings.EMBEDDING_DIMENSION,
        )
        payload = result.get("embedding") if isinstance(result, dict) else None
        if isinstance(payload, list) and payload and isinstance(payload[0], list):
            return payload
        if isinstance(payload, list):
            return [payload]
        raise RuntimeError("Legacy embedding response không hợp lệ")

    async def _embed_batch_http(self, texts: list[str], task_type: str, model: str) -> list[list[float]]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": settings.GEMINI_API_KEY,
        }

        async def _request_one(client: httpx.AsyncClient, text: str) -> list[float]:
            payload: dict[str, Any] = {
                "model": f"models/{model}",
                "content": {"parts": [{"text": text}]},
                "taskType": task_type,
                "outputDimensionality": settings.EMBEDDING_DIMENSION,
            }
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json() or {}
            single = data.get("embedding")
            if isinstance(single, dict) and single.get("values"):
                return single["values"]
            if isinstance(single, list):
                return single
            raise RuntimeError("Phản hồi embedding không hợp lệ")

        async with httpx.AsyncClient(timeout=60.0) as client:
            outputs: list[list[float]] = []
            for text in texts:
                outputs.append(await _request_one(client, text))
                await asyncio.sleep(0.35)
            return outputs

    @staticmethod
    def _should_fallback(exc: Exception) -> bool:
        message = str(exc).lower()
        fallback_markers = (
            "404",
            "not found",
            "not supported",
            "unsupported",
            "embedcontent",
            "listmodels",
            "deprecated",
        )
        return any(marker in message for marker in fallback_markers)


embedding_service = EmbeddingService()
