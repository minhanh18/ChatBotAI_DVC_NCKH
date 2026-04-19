from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable

from app.models.db import Document


def compute_file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def normalize_document_name(name: str | None) -> str:
    raw = (name or "").strip().lower().replace("_", " ")
    raw = re.sub(r"\.[a-z0-9]{1,6}$", "", raw)
    return " ".join(raw.split())


def lifecycle_status(meta: dict[str, Any] | None) -> str:
    value = str((meta or {}).get("lifecycle_status") or "active").strip().lower()
    return value or "active"


def is_active_for_retrieval(meta: dict[str, Any] | None) -> bool:
    info = meta or {}
    if info.get("is_active_for_retrieval") is False:
        return False
    return lifecycle_status(info) == "active"


def version_of(meta: dict[str, Any] | None) -> int:
    value = (meta or {}).get("version")
    try:
        return int(value)
    except Exception:
        return 1


def build_document_meta(
    *,
    file_hash: str,
    normalized_name: str,
    version: int,
    source_url: str | None,
    previous_document_id: str | None = None,
) -> dict[str, Any]:
    meta = {
        "version": version,
        "normalized_name": normalized_name,
        "source_hash": file_hash,
        "lifecycle_status": "active",
        "is_active_for_retrieval": True,
    }
    if source_url:
        meta["source_url"] = source_url
    if previous_document_id:
        meta["supersedes_document_id"] = previous_document_id
    return meta


def merge_meta(existing: dict[str, Any] | None, updates: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing or {})
    merged.update({k: v for k, v in updates.items() if v is not None})
    return merged


def find_duplicate_document(documents: Iterable[Document], *, file_hash: str) -> Document | None:
    for doc in documents:
        meta = doc.meta or {}
        if meta.get("source_hash") == file_hash:
            return doc
    return None


def find_latest_same_name_document(documents: Iterable[Document], *, normalized_name: str) -> Document | None:
    candidates = [doc for doc in documents if (doc.meta or {}).get("normalized_name") == normalized_name]
    if not candidates:
        return None
    candidates.sort(key=lambda item: version_of(item.meta), reverse=True)
    return candidates[0]
