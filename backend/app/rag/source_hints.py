from __future__ import annotations

import re
from typing import Any

_DOCUMENT_HINTS = [
    {
        "keys": [
            "sổ tay thuế điện tử dành cho hộ kinh doanh - cá nhân kinh doanh",
            "sổ tay thuế điện tử dành cho hộ kinh doanh cá nhân kinh doanh",
            "sổ tay thuế điện tử dành cho hộ kinh doanh & cá nhân kinh doanh",
            "hộ kinh doanh",
            "cá nhân kinh doanh",
        ],
        "source_url": "https://drive.google.com/file/d/1dehhovjyTTlrsVISO4wLbq8XVjPuxPUm/view",
        "display_name": "Sổ tay thuế điện tử dành cho hộ kinh doanh và cá nhân kinh doanh",
        "source_label": "Cục Thuế - Bộ Tài chính (Ban Pháp chế)",
        "lead": "Theo thông tin trong cuốn sổ tay thuế điện tử dành cho hộ kinh doanh và cá nhân kinh doanh do Cục Thuế - Bộ Tài chính (Ban Pháp chế) ban hành tháng 09/2025, tôi trả lời câu hỏi của bạn như sau:",
    },
    {
        "keys": [
            "sổ tay thuế điện tử dành cho kế toán trưởng",
            "kế toán trưởng doanh nghiệp",
        ],
        "source_url": "https://drive.google.com/file/d/1xW5fU_GxBcO5HOoKAUmp0v9ANN_-hlPi/view",
        "display_name": "Sổ tay thuế điện tử dành cho kế toán trưởng doanh nghiệp",
        "source_label": "Cục Thuế - Bộ Tài chính (Ban Pháp chế)",
        "lead": "Theo thông tin trong cuốn sổ tay thuế điện tử dành cho kế toán trưởng doanh nghiệp do Cục Thuế - Bộ Tài chính (Ban Pháp chế) ban hành tháng 09/2025, tôi trả lời câu hỏi của bạn như sau:",
    },
    {
        "keys": [
            "sổ tay thuế điện tử dành cho chủ doanh nghiệp",
            "chủ doanh nghiệp",
        ],
        "source_url": "https://drive.google.com/file/d/1AOuhpWI6JhbXjL4BMu_T8CR8FUewERSh/view",
        "display_name": "Sổ tay thuế điện tử dành cho chủ doanh nghiệp",
        "source_label": "Cục Thuế - Bộ Tài chính (Ban Pháp chế)",
        "lead": "Theo thông tin trong cuốn sổ tay thuế điện tử dành cho chủ doanh nghiệp do Cục Thuế - Bộ Tài chính (Ban Pháp chế) ban hành tháng 09/2025, tôi trả lời câu hỏi của bạn như sau:",
    },
    {
        "keys": [
            "sổ tay thuế điện tử hoàn thuế giá trị gia tăng",
            "hoàn thuế giá trị gia tăng",
        ],
        "source_url": "https://drive.google.com/file/d/1jDOV4b17bfguMJLD9d9y5pUdhAytC-uG/view",
        "display_name": "Sổ tay hoàn thuế giá trị gia tăng",
        "source_label": "Thuế tỉnh Gia Lai",
        "lead": "Theo thông tin trong sổ tay hoàn thuế giá trị gia tăng do Thuế tỉnh Gia Lai ban hành, tôi trả lời câu hỏi của bạn như sau:",
    },
]


def _normalize_name(name: str | None) -> str:
    raw = " ".join((name or "").strip().lower().replace("_", " ").split())
    return re.sub(r"\.[a-z0-9]{1,6}$", "", raw).strip()


def get_document_hint(document_name: str | None, document_meta: dict[str, Any] | None = None) -> dict[str, Any] | None:
    meta = document_meta or {}
    normalized = _normalize_name(document_name)
    for hint in _DOCUMENT_HINTS:
        if any(key in normalized for key in hint["keys"]):
            merged = dict(hint)
            if isinstance(meta.get("source_url"), str) and meta.get("source_url", "").strip():
                merged["source_url"] = meta["source_url"].strip()
            return merged
    if isinstance(meta.get("source_url"), str) and meta.get("source_url", "").strip():
        return {
            "source_url": meta["source_url"].strip(),
            "display_name": pretty_document_name(document_name),
        }
    return None


def pretty_document_name(document_name: str | None) -> str:
    hint = get_document_hint(document_name, {})
    if hint and hint.get("display_name"):
        return str(hint["display_name"])
    name = (document_name or "Tài liệu").strip()
    name = re.sub(r"\.[A-Za-z0-9]{1,6}$", "", name).strip()
    return name or "Tài liệu"


def resolve_document_source_url(document_name: str | None, document_meta: dict[str, Any] | None = None) -> str | None:
    meta = document_meta or {}
    hint = get_document_hint(document_name, meta)
    if hint and isinstance(hint.get("source_url"), str) and hint.get("source_url", "").strip():
        return str(hint["source_url"]).strip()
    direct = meta.get("source_url") or meta.get("canonical_url")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    return None
