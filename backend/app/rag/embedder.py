"""
Embedding service với key rotation và retry thông minh.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import random
from typing import Any

import google.generativeai as genai
import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_BATCH_SIZE = 5
_RETRY_MAX = 6
_RETRY_DELAY = 3.0


def _is_rate_limited_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in ("429", "resource_exhausted", "quota", "too many requests", "rate limit"))


def _build_key_pool() -> list[str]:
    """Gom tất cả API keys thành 1 pool, loại trùng, bỏ rỗng."""
    seen: set[str] = set()
    pool: list[str] = []
    for key in [settings.GEMINI_API_KEY] + list(settings.GEMINI_API_KEYS):
        k = (key or "").strip()
        if k and k not in seen:
            seen.add(k)
            pool.append(k)
    return pool


class EmbeddingService:
    def __init__(self, model: str = settings.EMBEDDING_MODEL):
        self.model = model
        self.fallback_model = settings.EMBEDDING_FALLBACK_MODEL
        self._key_pool = _build_key_pool()
        # cycle qua keys để phân tải đều
        self._key_cycle = itertools.cycle(self._key_pool) if self._key_pool else iter([])
        self._current_key_idx = 0

    def _next_key(self) -> str:
        """Lấy key tiếp theo trong pool (round-robin)."""
        if not self._key_pool:
            return settings.GEMINI_API_KEY
        self._current_key_idx = (self._current_key_idx + 1) % len(self._key_pool)
        return self._key_pool[self._current_key_idx]

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
                # Xoay key mỗi lần retry để tránh hit cùng 1 quota
                api_key = self._key_pool[attempt % len(self._key_pool)] if self._key_pool else settings.GEMINI_API_KEY
                return await self._embed_batch_prefer_legacy(texts, task_type, api_key)
            except Exception as exc:
                last_error = exc
                if attempt < _RETRY_MAX - 1:
                    wait = (_RETRY_DELAY * (2 ** attempt)) + random.uniform(0.0, 1.0)
                    if _is_rate_limited_error(exc):
                        wait = max(wait, 8.0 + (attempt * 6.0))
                    logger.warning(
                        "Embedding batch thất bại (lần %d/%d, key idx %d): %s. Retry sau %.1fs",
                        attempt + 1, _RETRY_MAX, attempt % max(len(self._key_pool), 1), exc, wait,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error("Embedding batch thất bại sau %d lần thử: %s", _RETRY_MAX, exc)
        assert last_error is not None
        raise last_error

    async def _embed_batch_prefer_legacy(self, texts: list[str], task_type: str, api_key: str) -> list[list[float]]:
        try:
            return await asyncio.to_thread(self._embed_batch_legacy_sync, texts, task_type, api_key)
        except Exception as exc:
            if not self._should_fallback(exc):
                raise
            logger.warning(
                "Legacy embedding model '%s' không dùng được, fallback sang '%s': %s",
                self.model, self.fallback_model, exc,
            )
            return await self._embed_batch_http(texts, task_type, self.fallback_model, api_key)

    def _embed_batch_legacy_sync(self, texts: list[str], task_type: str, api_key: str) -> list[list[float]]:
        genai.configure(api_key=api_key)
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

    async def _embed_batch_http(self, texts: list[str], task_type: str, model: str, api_key: str) -> list[list[float]]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
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
        fallback_markers = ("404", "not found", "not supported", "unsupported", "embedcontent", "listmodels", "deprecated")
        return any(marker in message for marker in fallback_markers)


embedding_service = EmbeddingService()
