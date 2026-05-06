"""
Query Rewriter — chuẩn hóa câu hỏi người dùng trước khi đưa vào RAG.

Xử lý:
- Tiếng Việt không dấu / thiếu dấu (gõ nhanh, bàn phím không hỗ trợ)
- Viết tắt hành chính (đk, bhyt, cccd, tthc, ...)
- Sai chính tả phổ biến
- Tiếng địa phương / khẩu ngữ
- Câu thiếu chủ ngữ / cộc lốc

Cơ chế: dùng Gemini (generate, non-streaming, max 80 token) để rewrite.
- Cache kết quả trong session để tránh gọi API lặp.
- Timeout 3s: nếu quá thời gian hoặc lỗi → trả về query gốc (fail-safe).
- Chỉ rewrite nếu query có dấu hiệu cần chuẩn hóa.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from functools import lru_cache

logger = logging.getLogger(__name__)

# Cache đơn giản in-process: hash(query) → rewritten
_REWRITE_CACHE: dict[str, str] = {}
_CACHE_MAX = 512

_REWRITE_PROMPT = """\
Bạn là công cụ chuẩn hóa văn bản tiếng Việt. Nhiệm vụ DUY NHẤT của bạn là viết lại câu đầu vào thành tiếng Việt chuẩn, đúng dấu, đúng chính tả, mở rộng viết tắt hành chính thông dụng.

QUY TẮC:
- Giữ nguyên ý nghĩa, KHÔNG trả lời câu hỏi, KHÔNG giải thích, KHÔNG thêm thông tin.
- Chỉ trả về DUY NHẤT câu đã chuẩn hóa, không có gì khác.
- Nếu câu đã chuẩn rồi, trả về nguyên câu đó.
- Mở rộng viết tắt: đk→đăng ký, bhyt→bảo hiểm y tế, cccd→căn cước công dân, tthc→thủ tục hành chính, dvc→dịch vụ công, ubnd→ủy ban nhân dân, hkd→hộ kinh doanh, cmnd→chứng minh nhân dân, gplx→giấy phép lái xe, bhxh→bảo hiểm xã hội.

VÍ DỤ:
- "muon dang ky tam tru thi lam sao" → "Muốn đăng ký tạm trú thì làm sao?"
- "toi can dk bhyt o dau" → "Tôi cần đăng ký bảo hiểm y tế ở đâu?"
- "xin chao" → "Xin chào"
- "ho so cccd can gi" → "Hồ sơ căn cước công dân cần gì?"
- "dk tam tru cho con chua thanh nien" → "Đăng ký tạm trú cho con chưa thành niên"
- "thu tuc ket hon mat bao lau" → "Thủ tục kết hôn mất bao lâu?"

Câu cần chuẩn hóa:
"""

# Regex phát hiện query cần rewrite
_NEEDS_REWRITE_PATTERNS = [
    re.compile(r'\b(muon|lam|nhu|the nao|lam sao|o dau|bao nhieu|can|duoc|khong|co the|phai)\b', re.IGNORECASE),
    re.compile(r'\b(tam tru|thuong tru|cu tru|dang ky|ho so|thu tuc|giay to|khai sinh|ket hon)\b', re.IGNORECASE),
    re.compile(r'\b(dk|bhyt|bhxh|cccd|cmnd|tthc|dvc|ubnd|hkd|gplx|hkk)\b', re.IGNORECASE),
]

def _needs_rewrite(query: str) -> bool:
    """Kiểm tra nhanh xem query có cần rewrite không, tránh gọi API không cần thiết."""
    q = query.lower()
    # Không rewrite nếu đã có đủ dấu tiếng Việt (heuristic: nhiều ký tự unicode Vietnamese)
    viet_chars = len(re.findall(r'[àáạảãăắằẳẵặâấầẩẫậèéẹẻẽêếềểễệìíịỉĩòóọỏõôốồổỗộơớờởỡợùúụủũưứừửữựỳýỵỷỹđ]', q))
    words = len(q.split())
    if words > 0 and viet_chars / words >= 0.6:
        return False  # Đã đủ dấu
    # Kiểm tra pattern không dấu / viết tắt
    for pattern in _NEEDS_REWRITE_PATTERNS:
        if pattern.search(q):
            return True
    # Nếu tỉ lệ ký tự ascii cao bất thường cho tiếng Việt → likely không dấu
    ascii_ratio = sum(1 for c in q if c.isalpha() and ord(c) < 128) / max(len(q), 1)
    return ascii_ratio > 0.75


async def rewrite_query(query: str) -> str:
    """
    Rewrite query tiếng Việt không dấu/viết tắt → tiếng Việt chuẩn.
    Fail-safe: luôn trả về query hợp lệ (gốc nếu có lỗi).
    """
    if not query or not query.strip():
        return query

    if not _needs_rewrite(query):
        return query  # Không cần rewrite → trả về ngay, không tốn API call

    cache_key = hashlib.md5(query.encode()).hexdigest()
    if cache_key in _REWRITE_CACHE:
        return _REWRITE_CACHE[cache_key]

    try:
        rewritten = await asyncio.wait_for(_call_gemini_rewrite(query), timeout=3.0)
        rewritten = rewritten.strip().strip('"').strip("'")
        if rewritten and len(rewritten) < len(query) * 5:  # sanity check
            # Lưu cache
            if len(_REWRITE_CACHE) >= _CACHE_MAX:
                # Xóa 1/4 cache cũ (FIFO rough)
                keys = list(_REWRITE_CACHE.keys())
                for k in keys[:_CACHE_MAX // 4]:
                    del _REWRITE_CACHE[k]
            _REWRITE_CACHE[cache_key] = rewritten
            logger.info("query_rewriter: '%s' → '%s'", query, rewritten)
            return rewritten
    except asyncio.TimeoutError:
        logger.debug("query_rewriter: timeout cho query '%s', dùng query gốc", query)
    except Exception as e:
        logger.debug("query_rewriter: lỗi '%s', dùng query gốc. Error: %s", query, e)

    return query  # Fail-safe


async def _call_gemini_rewrite(query: str) -> str:
    """Gọi Gemini non-streaming để rewrite query. Chạy trong thread."""
    import google.generativeai as genai
    from app.config import settings
    from app.chat.engine import _key_pool

    def _sync_call() -> str:
        key = _key_pool.get_available_key() or settings.GEMINI_API_KEY
        genai.configure(api_key=key)
        model = genai.GenerativeModel(
            settings.GEMINI_MODEL,
            generation_config={"max_output_tokens": 80, "temperature": 0.0},
        )
        response = model.generate_content(_REWRITE_PROMPT + query)
        return response.text or query

    return await asyncio.to_thread(_sync_call)
