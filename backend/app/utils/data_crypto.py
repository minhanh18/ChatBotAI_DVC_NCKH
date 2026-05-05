"""
data_crypto.py — Mã hóa / ẩn danh thông tin người dùng theo yêu cầu
an ninh mạng (Luật An ninh mạng 2018, Nghị định 13/2023/NĐ-CP về bảo vệ DLCN).

Chiến lược:
  1. session_key   → one-way HMAC-SHA256 (pseudonymisation) trước khi lưu DB.
  2. query_text    → lưu nguyên để admin giám sát chất lượng, nhưng
                     khi export/log ở cấp DEBUG thì mask PII.
  3. IP / user-agent → không lưu vào DB (không thu thập).
  4. Conversation.title → giữ nguyên (chỉ là tiêu đề hội thoại, không chứa DLCN).

Khoá HMAC lấy từ biến môi trường SESSION_HMAC_KEY (chuỗi bất kỳ, ≥ 32 ký tự).
Nếu không cấu hình thì fallback sang SECRET_KEY của ứng dụng.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

# Patterns PII đơn giản để mask khi log
_PII_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b\d{9,12}\b"), "[CCCD/CMND]"),                  # CCCD / CMND
    (re.compile(r"\b0\d{9}\b"), "[SĐT]"),                          # Số điện thoại VN
    (re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z.]+"), "[EMAIL]"),
    (re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"), "[THẺ]"),  # Số thẻ ngân hàng
]


@lru_cache(maxsize=1)
def _hmac_key() -> bytes:
    """Lấy khoá HMAC từ settings (lazy import để tránh circular)."""
    try:
        from app.config import settings
        raw = getattr(settings, "SESSION_HMAC_KEY", None) or getattr(settings, "SECRET_KEY", None) or "default-insecure-key"
    except Exception:
        raw = "default-insecure-key"
    if raw == "default-insecure-key":
        logger.warning(
            "SESSION_HMAC_KEY chưa cấu hình — session_key sẽ dùng khoá mặc định. "
            "Hãy đặt SESSION_HMAC_KEY trong .env để bảo mật đúng."
        )
    return raw.encode("utf-8")


def pseudonymise_session_key(raw_key: str) -> str:
    """
    Chuyển session_key thô (do client tự sinh) thành chuỗi HMAC-SHA256 hex.
    Đảm bảo không thể đảo ngược để suy ra danh tính người dùng,
    đồng thời vẫn dùng được làm khoá tra cứu (deterministic).

    Nếu raw_key rỗng → trả về rỗng (không xử lý).
    """
    if not raw_key:
        return raw_key
    digest = hmac.new(_hmac_key(), raw_key.encode("utf-8"), hashlib.sha256).hexdigest()
    # Giữ tiền tố "sk_" để dễ nhận biết loại khoá khi debug, không lộ giá trị thật
    return f"sk_{digest[:32]}"


def mask_pii(text: str) -> str:
    """
    Mask các thông tin nhận dạng cá nhân trong text trước khi ghi log.
    Không dùng cho nội dung lưu DB (query_text giữ nguyên để admin review).
    """
    if not text:
        return text
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def safe_log_query(query: str, max_len: int = 120) -> str:
    """
    Tạo phiên bản an toàn của query để ghi log:
    - Mask PII
    - Cắt bớt nếu dài
    """
    masked = mask_pii(query or "")
    if len(masked) > max_len:
        return masked[:max_len] + "…"
    return masked
