"""
Query Rewriter — chuẩn hóa câu hỏi người dùng trước khi đưa vào RAG.

Xử lý:
- Tiếng Việt không dấu / thiếu dấu (gõ nhanh, bàn phím không hỗ trợ)
- Viết tắt hành chính (đk, bhyt, cccd, tthc, ...)
- Sai chính tả phổ biến
- Tiếng địa phương / khẩu ngữ
- Câu thiếu chủ ngữ / cộc lốc

Cơ chế 2 lớp:
1. Offline: expand viết tắt bằng dictionary → nhanh, không cần API.
2. LLM (Gemini): dùng GEMINI_MODEL từ settings (mặc định gemini-2.5-flash) để restore dấu + sửa chính tả.
   - Timeout 10s (tăng từ 3s để tránh false-timeout với cold start).
   - KHÔNG import _key_pool từ engine (tránh circular import + deadlock).
   - Dùng GEMINI_API_KEY trực tiếp từ settings, không xoay vòng key.
   - Cache kết quả in-process (512 entries).
   - Fail-safe: nếu lỗi/timeout → trả về query đã expand viết tắt (không phải query gốc).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ── Cache in-process ─────────────────────────────────────────────────────────
_REWRITE_CACHE: dict[str, str] = {}
_CACHE_MAX = 512

# ── Viết tắt hành chính → tiếng Việt đầy đủ ─────────────────────────────────
# Expand offline TRƯỚC khi gọi LLM, vừa giảm tải API vừa làm hint cho model
_ABBREVIATIONS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bdk\b", re.IGNORECASE), "đăng ký"),
    (re.compile(r"\bbhyt\b", re.IGNORECASE), "bảo hiểm y tế"),
    (re.compile(r"\bbhxh\b", re.IGNORECASE), "bảo hiểm xã hội"),
    (re.compile(r"\bcccd\b", re.IGNORECASE), "căn cước công dân"),
    (re.compile(r"\bcmnd\b", re.IGNORECASE), "chứng minh nhân dân"),
    (re.compile(r"\btthc\b", re.IGNORECASE), "thủ tục hành chính"),
    (re.compile(r"\bdvc\b", re.IGNORECASE), "dịch vụ công"),
    (re.compile(r"\bubnd\b", re.IGNORECASE), "ủy ban nhân dân"),
    (re.compile(r"\bhkd\b", re.IGNORECASE), "hộ kinh doanh"),
    (re.compile(r"\bgplx\b", re.IGNORECASE), "giấy phép lái xe"),
    (re.compile(r"\bhkk\b", re.IGNORECASE), "hộ khẩu"),
    (re.compile(r"\bgt\b", re.IGNORECASE), "giấy tờ"),
    (re.compile(r"\bhs\b", re.IGNORECASE), "hồ sơ"),
    (re.compile(r"\btt\b", re.IGNORECASE), "thủ tục"),
    (re.compile(r"\bqd\b", re.IGNORECASE), "quyết định"),
    (re.compile(r"\bnd\b", re.IGNORECASE), "nghị định"),
    (re.compile(r"\bvp\b", re.IGNORECASE), "vi phạm"),
    (re.compile(r"\btk\b", re.IGNORECASE), "tài khoản"),
    (re.compile(r"\bphuong\b", re.IGNORECASE), "phường"),
    (re.compile(r"\bxa\b", re.IGNORECASE), "xã"),
    (re.compile(r"\bthi tran\b", re.IGNORECASE), "thị trấn"),
    (re.compile(r"\bhuyen\b", re.IGNORECASE), "huyện"),
    (re.compile(r"\btinh\b", re.IGNORECASE), "tỉnh"),
]

_REWRITE_PROMPT = """\
Bạn là công cụ chuẩn hóa văn bản tiếng Việt. Nhiệm vụ DUY NHẤT của bạn là viết lại câu đầu vào thành tiếng Việt chuẩn, đúng dấu, đúng chính tả, mở rộng viết tắt hành chính thông dụng.

QUY TẮC BẮT BUỘC:
- Giữ nguyên ý nghĩa, KHÔNG trả lời câu hỏi, KHÔNG giải thích, KHÔNG thêm thông tin.
- Chỉ trả về DUY NHẤT câu đã chuẩn hóa — KHÔNG có gì khác, KHÔNG giải thích.
- Nếu câu đã chuẩn rồi, trả về nguyên câu đó.
- Mở rộng viết tắt: đk→đăng ký, bhyt→bảo hiểm y tế, cccd→căn cước công dân, tthc→thủ tục hành chính, dvc→dịch vụ công, ubnd→ủy ban nhân dân, hkd→hộ kinh doanh, cmnd→chứng minh nhân dân, gplx→giấy phép lái xe, bhxh→bảo hiểm xã hội, gt→giấy tờ, hs→hồ sơ, tt→thủ tục.
- Khôi phục dấu tiếng Việt cho từ không dấu: "tam tru"→"tạm trú", "thuong tru"→"thường trú", "khai sinh"→"khai sinh", "ket hon"→"kết hôn", "ho so"→"hồ sơ", "thu tuc"→"thủ tục", "dang ky"→"đăng ký", "can cu"→"căn cứ".

VÍ DỤ (input → output, KHÔNG giải thích):
- "muon dang ky tam tru thi lam sao" → "Muốn đăng ký tạm trú thì làm sao?"
- "toi can dk bhyt o dau" → "Tôi cần đăng ký bảo hiểm y tế ở đâu?"
- "ho so cccd can gi" → "Hồ sơ căn cước công dân cần gì?"
- "dk tam tru cho con chua thanh nien" → "Đăng ký tạm trú cho con chưa thành niên"
- "thu tuc ket hon mat bao lau" → "Thủ tục kết hôn mất bao lâu?"
- "toi muon dk tam tru thi lam sao" → "Tôi muốn đăng ký tạm trú thì làm sao?"
- "xin chao" → "Xin chào"

Câu cần chuẩn hóa (chỉ trả về câu đã chuẩn, không thêm gì):
"""

# Regex phát hiện query cần rewrite
_NEEDS_REWRITE_PATTERNS = [
    re.compile(r"\b(muon|lam|nhu|the nao|lam sao|o dau|bao nhieu|can|duoc|khong|co the|phai|nop|xin|cap|chuyen)\b", re.IGNORECASE),
    re.compile(r"\b(tam tru|thuong tru|cu tru|dang ky|ho so|thu tuc|giay to|khai sinh|ket hon|nha dat|dat dai)\b", re.IGNORECASE),
    re.compile(r"\b(dk|bhyt|bhxh|cccd|cmnd|tthc|dvc|ubnd|hkd|gplx|hkk|gt|hs|tt|qd|nd)\b", re.IGNORECASE),
]


def _expand_abbreviations(query: str) -> str:
    """Expand viết tắt hành chính offline, không cần API."""
    result = query
    for pattern, replacement in _ABBREVIATIONS:
        result = pattern.sub(replacement, result)
    return result


def _needs_rewrite(query: str) -> bool:
    """
    Kiểm tra xem query (đã qua expand viết tắt) còn cần LLM restore dấu không.

    Logic:
    - Đếm số từ "ascii-only" (không có dấu tiếng Việt) trong query SAU khi đã expand viết tắt.
    - Nếu > 30% số từ vẫn là ascii-only → khả năng cao là query không dấu → cần LLM.
    - Fallback: check pattern từ thông dụng không dấu (muon, lam, o dau...).
    """
    q = query.lower()
    words = q.split()
    word_count = len(words)
    if word_count == 0:
        return False

    _viet_re = re.compile(
        r"[àáạảãăắằẳẵặâấầẩẫậèéẹẻẽêếềểễệìíịỉĩòóọỏõôốồổỗộơớờởỡợùúụủũưứừửữựỳýỵỷỹđ]"
    )

    # Đếm từ thuần ascii (không chứa ký tự tiếng Việt có dấu)
    ascii_words = [w for w in words if w.isalpha() and not _viet_re.search(w)]
    ascii_word_ratio = len(ascii_words) / word_count

    # Nếu hơn 30% từ vẫn là ascii-only sau expand → cần LLM restore dấu
    if ascii_word_ratio > 0.30:
        return True

    # Fallback: khớp pattern từ không dấu / viết tắt chưa expand
    for pattern in _NEEDS_REWRITE_PATTERNS:
        if pattern.search(q):
            return True

    return False


async def rewrite_query(query: str) -> str:
    """
    Rewrite query tiếng Việt không dấu/viết tắt → tiếng Việt chuẩn.

    Pipeline:
    1. Expand viết tắt offline (instantaneous, no API).
    2. Nếu vẫn cần rewrite → gọi Gemini với timeout 8s.
    3. Fail-safe: trả về bản đã expand viết tắt nếu LLM fail.
    """
    if not query or not query.strip():
        return query

    # Bước 1: expand viết tắt offline trước
    expanded = _expand_abbreviations(query)

    # Bước 2: kiểm tra có cần LLM không (check trên bản đã expand)
    if not _needs_rewrite(expanded):
        # Đã đủ dấu sau khi expand → không cần LLM
        return expanded if expanded != query else query

    cache_key = hashlib.md5(expanded.encode()).hexdigest()
    if cache_key in _REWRITE_CACHE:
        return _REWRITE_CACHE[cache_key]

    try:
        # Timeout 10s cho gemini-2.5-flash (cold start có thể 3-8s)
        rewritten = await asyncio.wait_for(_call_gemini_rewrite(expanded), timeout=10.0)
        rewritten = rewritten.strip().strip('"').strip("'")
        # Sanity check: Gemini có thể trả về câu trả lời thay vì chuẩn hóa
        # Dấu hiệu: output quá dài so với input, hoặc không chứa ký tự tiếng Việt
        _has_viet = bool(__import__("re").search(
            r"[àáạảãăắằẳẵặâấầẩẫậèéẹẻẽêếềểễệìíịỉĩòóọỏõôốồổỗộơớờởỡợùúụủũưứừửữựỳýỵỷỹđ]",
            rewritten
        ))
        _reasonable_length = len(rewritten) < len(expanded) * 4 and len(rewritten) < 400
        if rewritten and len(rewritten) > 2 and _has_viet and _reasonable_length:
            if len(_REWRITE_CACHE) >= _CACHE_MAX:
                keys = list(_REWRITE_CACHE.keys())
                for k in keys[:_CACHE_MAX // 4]:
                    del _REWRITE_CACHE[k]
            _REWRITE_CACHE[cache_key] = rewritten
            logger.info("query_rewriter: '%s' → '%s' (via LLM)", query, rewritten)
            return rewritten
    except asyncio.TimeoutError:
        logger.warning("query_rewriter: LLM timeout (10s) cho query '%s', dùng bản expand viết tắt", query)
    except Exception as e:
        logger.warning("query_rewriter: LLM lỗi cho query '%s': %s — dùng bản expand viết tắt", query, e)

    # Fail-safe: trả về bản đã expand viết tắt (tốt hơn query gốc)
    if expanded != query:
        logger.info("query_rewriter: fail-safe expand '%s' → '%s'", query, expanded)
    return expanded


async def _call_gemini_rewrite(query: str) -> str:
    """
    Gọi Gemini để rewrite query.
    - Dùng GEMINI_MODEL từ settings (không hardcode, giữ đúng model người dùng cấu hình).
    - KHÔNG import _key_pool từ engine để tránh circular import và deadlock.
      (Lý do: _key_pool import lúc engine.py load có thể block nếu key đang cooldown,
       gây timeout ngay cả khi chưa gọi API. Rewrite dùng trực tiếp GEMINI_API_KEY.)
    - Chạy trong asyncio.to_thread để không block event loop.
    """
    import google.generativeai as genai
    from app.config import settings

    def _sync_call() -> str:
        key = settings.GEMINI_API_KEY
        genai.configure(api_key=key)
        model = genai.GenerativeModel(
            settings.GEMINI_MODEL,
            generation_config={
                "max_output_tokens": 120,
                "temperature": 0.0,
            },
        )
        response = model.generate_content(_REWRITE_PROMPT + query)
        return response.text or query

    return await asyncio.to_thread(_sync_call)
