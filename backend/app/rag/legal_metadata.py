from __future__ import annotations

import hashlib
import re
from typing import Any

TITLE_NUMBER_PATTERNS = [
    (re.compile(r"\b(\d+/\d{4}/QH\d+)\b", re.IGNORECASE), "Luật"),
    (re.compile(r"\b(\d+/\d{4}/NĐ-CP)\b", re.IGNORECASE), "Nghị định"),
    (re.compile(r"\b(\d+/\d{4}/TT-[A-Z0-9]+)\b", re.IGNORECASE), "Thông tư"),
    (re.compile(r"\b(\d+/\d{4}/QĐ-[A-Z0-9]+)\b", re.IGNORECASE), "Quyết định"),
]

ARTICLE_RE = re.compile(r"\b(Điều\s+\d+[A-Za-z0-9-]*)\b", re.IGNORECASE)
CLAUSE_RE = re.compile(r"\b(Khoản\s+\d+[A-Za-z0-9-]*)\b", re.IGNORECASE)
POINT_RE = re.compile(r"\b(Điểm\s+[a-zđ])\b", re.IGNORECASE)
SECTION_RE = re.compile(r"^(Chương\s+[IVXLC0-9]+|Mục\s+\d+|Phần\s+[IVXLC0-9]+)[:\s-]*(.*)$", re.IGNORECASE | re.MULTILINE)

TOPIC_KEYWORDS = {
    "thuế": ["thuế", "người nộp thuế", "tờ khai", "khấu trừ", "kê khai"],
    "cư trú": ["cư trú", "tạm trú", "thường trú", "nơi cư trú"],
    "hộ tịch": ["khai sinh", "kết hôn", "hộ tịch", "giấy khai sinh"],
    "dịch vụ công": ["dịch vụ công", "nộp hồ sơ", "một cửa", "trực tuyến"],
}


def sha256_text(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def detect_legal_document_metadata(name: str, text: str | None) -> dict[str, Any]:
    title_source = f"{name}\n{(text or "")[:1500]}"
    document_number = None
    document_type = None
    for pattern, doc_type in TITLE_NUMBER_PATTERNS:
        match = pattern.search(title_source)
        if match:
            document_number = match.group(1)
            document_type = doc_type
            break

    issue_year = None
    if document_number:
        parts = document_number.split("/")
        if len(parts) >= 2 and parts[1].isdigit():
            issue_year = int(parts[1])

    return {
        "document_number": document_number,
        "document_type": document_type,
        "issue_year": issue_year,
    }


def extract_chunk_legal_metadata(content: str) -> dict[str, Any]:
    article = ARTICLE_RE.search(content or "")
    clause = CLAUSE_RE.search(content or "")
    point = POINT_RE.search(content or "")
    section = SECTION_RE.search(content or "")

    topic = None
    lowered = (content or "").lower()
    for topic_name, keywords in TOPIC_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            topic = topic_name
            break

    meta: dict[str, Any] = {
        "content_hash": sha256_text(content or ""),
        "article_ref": article.group(1) if article else None,
        "clause_ref": clause.group(1) if clause else None,
        "point_ref": point.group(1) if point else None,
        "section_heading": (" ".join(part for part in section.groups() if part).strip() if section else None),
        "legal_topic": topic,
    }
    return {key: value for key, value in meta.items() if value is not None}
