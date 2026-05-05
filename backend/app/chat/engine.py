"""
Chat Engine — lõi sinh câu trả lời với streaming.
Ưu tiên RAG trước, có gate đánh giá bằng chứng trước khi phản hồi,
và chỉ hiển thị nguồn đã thực sự dùng.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict, deque
import queue
import re
import threading
import time
from collections.abc import AsyncGenerator
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import google.generativeai as genai
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.router import AnswerMode, RouteDecision
from app.chat.evaluator import (
    build_safe_fallback_answer,
    is_greeting_query,
    is_legal_query,
    is_procedure_query,
)
from app.config import settings
from app.models.db import Conversation, Message, UsageLog
from app.rag.retriever import RetrievedChunk
from app.rag.source_hints import (
    get_document_hint,
    pretty_document_name,
    resolve_document_source_url,
)
from app.web.live_search import maybe_fetch_web_context, should_search_web

logger = logging.getLogger(__name__)


# ── API Key Pool — xoay vòng khi bị quota exhausted ──────────────────────────

class _GeminiKeyPool:
    """
    Quản lý danh sách API key với per-key cooldown.

    - Round-robin + cooldown 65s khi key bị 429/RESOURCE_EXHAUSTED.
    - Parse retry-after từ lỗi Gemini để set cooldown chính xác.
    - Thread-safe.
    - Nếu tất cả key đang cooling → đợi key gần hết nhất.
    """

    _COOLDOWN_SEC = 65   # default cooldown khi không có retry-after cụ thể

    def __init__(self) -> None:
        self._keys: list[str] = self._load_keys()
        self._current: int = 0
        self._cooldowns: dict[str, float] = {}   # key → unix timestamp hết cooling
        self._lock = threading.Lock()
        self._configure_current()

    def _load_keys(self) -> list[str]:
        keys: list[str] = []
        if settings.GEMINI_API_KEY:
            keys.append(settings.GEMINI_API_KEY)
        extra = getattr(settings, "GEMINI_API_KEYS", "") or ""
        if isinstance(extra, list):
            keys.extend([k for k in extra if k and k not in keys])
        elif isinstance(extra, str) and extra:
            import re as _re
            for k in _re.split(r"[,\s]+", extra):
                k = k.strip()
                if k and k not in keys:
                    keys.append(k)
        if not keys:
            logger.warning("Không có GEMINI_API_KEY nào được cấu hình!")
        return keys

    def _configure_current(self) -> None:
        if self._keys:
            genai.configure(api_key=self._keys[self._current])

    def current_key(self) -> str:
        return self._keys[self._current] if self._keys else ""

    def get_available_key(self) -> str:
        """Trả về key khả dụng đầu tiên (không cooling). Block nếu cần."""
        with self._lock:
            now = time.time()
            for _ in range(len(self._keys)):
                key = self._keys[self._current]
                if now >= self._cooldowns.get(key, 0):
                    genai.configure(api_key=key)
                    return key
                self._current = (self._current + 1) % len(self._keys)

            # Tất cả đang cooling → đợi key hết cooldown sớm nhất
            earliest_key = min(self._keys, key=lambda k: self._cooldowns.get(k, 0))
            wait = max(0.0, self._cooldowns.get(earliest_key, 0) - now)
            logger.warning("Tất cả Gemini keys đang cooling, đợi %.1fs", wait)

        if wait > 0:
            time.sleep(min(wait, 30))

        with self._lock:
            try:
                self._current = self._keys.index(earliest_key)
            except ValueError:
                pass
            genai.configure(api_key=earliest_key)
            return earliest_key

    def rotate_on_quota(self, failed_key: str | None = None, retry_after: float | None = None) -> bool:
        """
        Đánh dấu key bị quota, xoay sang key tiếp theo.
        Trả về True nếu còn key khả dụng khác.
        """
        with self._lock:
            key_to_cool = failed_key or (self._keys[self._current] if self._keys else None)
            if key_to_cool and key_to_cool in self._keys:
                cooldown = (
                    retry_after + 5
                    if retry_after and 5 <= retry_after <= 300
                    else self._COOLDOWN_SEC
                )
                self._cooldowns[key_to_cool] = time.time() + cooldown
                logger.warning(
                    "Gemini key ...%s cooling %.0fs (total keys=%d)",
                    key_to_cool[-6:], cooldown, len(self._keys),
                )
                # Xoay sang key tiếp
                try:
                    idx = self._keys.index(key_to_cool)
                    self._current = (idx + 1) % len(self._keys)
                except ValueError:
                    self._current = (self._current + 1) % len(self._keys)
                self._configure_current()

            # Kiểm tra còn key nào không cooling không
            now = time.time()
            available = sum(1 for k in self._keys if now >= self._cooldowns.get(k, 0))
            return available > 0

    @staticmethod
    def parse_retry_after(exc: Exception) -> float | None:
        """Trích thời gian retry từ thông báo lỗi Gemini."""
        import re as _re
        m = _re.search(r"retry in ([\d.]+)s", str(exc), _re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
        return None

    @staticmethod
    def is_quota_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        return any(k in msg for k in (
            "quota", "resource_exhausted", "429",
            "rate_limit", "too_many_requests", "ratequota",
        ))

    @staticmethod
    def is_transient_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        return any(k in msg for k in (
            "503", "unavailable", "high demand", "overloaded",
            "deadline_exceeded", "timeout",
        ))


_key_pool = _GeminiKeyPool()
# Ghi đè cấu hình ban đầu (genai.configure đã được gọi một lần với primary key)
genai.configure(api_key=_key_pool.current_key() or settings.GEMINI_API_KEY)

def _get_identity() -> str:
    """Phần định danh + phạm vi + nguyên tắc trực tuyến — dùng chung cho cả RAG và AI."""
    return f"""Bạn là **Trợ lý Thủ tục Hành chính Số** — chuyên hỗ trợ người dân tìm hiểu và thực hiện các thủ tục hành chính, dịch vụ công, quy định pháp luật hành chính tại Việt Nam.

**Thời gian hiện tại:** {_current_datetime_context()}

## Phạm vi hoạt động
Trả lời các câu hỏi liên quan đến:
- Thủ tục hành chính (cấp giấy tờ, đăng ký, khai báo, đăng ký kinh doanh...)
- Quy định pháp luật liên quan đến thủ tục hành chính
- Nghĩa vụ thuế, phí, lệ phí của cá nhân và doanh nghiệp
- Hướng dẫn sử dụng dịch vụ công trực tuyến

## Nguyên tắc trực tuyến ưu tiên
⚠️ Theo Chỉ thị số 24/CT-TTg, các thủ tục hành chính phải thực hiện trực tuyến. Khi hướng dẫn thủ tục trực tuyến:
- Bước truy cập Cổng DVCQG **luôn dùng link**: [Cổng Dịch vụ công Quốc gia](https://dichvucong.gov.vn/p/home/dvc-trang-chu.html) — KHÔNG dùng URL làm label
- Nếu nguồn web có URL dẫn thẳng đến thủ tục cụ thể → đặt tại bước tìm/thực hiện thủ tục, không đặt tại bước "truy cập trang chủ"
- **Không tự tạo URL** — chỉ dùng URL xuất hiện rõ ràng trong tài liệu/ngữ cảnh được cung cấp"""


def _common_format_rules() -> str:
    """Quy tắc định dạng dùng chung."""
    return """
## Quy tắc định dạng danh sách nhiều cấp
- Cấp 1: `- mục chính`
- Cấp 2: `  - mục phụ` (thụt lề 2 dấu cách)
- Cấp 3: `    - mục con` (thụt lề 4 dấu cách)
- Không biến danh sách nhiều cấp thành danh sách phẳng một cấp.

## Quy tắc trích dẫn điều khoản pháp luật
- Chỉ dùng blockquote (>) để trích nguyên văn điều khoản pháp luật khi đoạn trích là một câu/đoạn có ý nghĩa đầy đủ, có chủ thể và nghĩa vụ/quyền/hành vi rõ ràng.
- **Tên điều khoản** (vd: "Điều 27. Điều kiện đăng ký tạm trú") chỉ được viết MỘT LẦN duy nhất — hoặc là tiêu đề phía trên blockquote (không dùng dấu >) HOẶC là dòng đầu tiên trong blockquote (dùng >), KHÔNG viết cả hai. Ưu tiên: viết tên điều khoản phía trên, rồi trích nội dung bên dưới bằng blockquote.
- **Tất cả các khoản trong cùng một điều** (vd: khoản 1 và khoản 2 của cùng Điều 27) phải nằm CÙNG trong một khối blockquote liên tục, không tách thành khoản ngoài và khoản trong blockquote riêng biệt.
- Không trích rời rạc các tiêu đề hoặc mảnh danh sách như `1. Trình tự...`, `a) Tiếp nhận...`; các mảnh này phải được diễn giải thành danh sách thường.
- Trong blockquote điều khoản: không dùng **in đậm**, không bọc lặp ngoặc kép; nếu cần nhấn mạnh thì dùng *in nghiêng* toàn đoạn trích.
- Không blockquote câu văn thông thường, hướng dẫn thực tế, ví dụ, hoặc câu diễn giải.
- Sau blockquote phải xuống đoạn riêng để diễn giải dễ hiểu và/hoặc ví dụ bằng text thường. Tuyệt đối không lặp lại y nguyên nội dung vừa trích dẫn.
- Không chèn link/số nguồn tham khảo kiểu [1](url) trong thân câu trả lời; nguồn sẽ hiển thị ở khu vực Tham khảo thêm. Chỉ giữ link thao tác trực tiếp như biểu mẫu hoặc cổng nộp hồ sơ nếu URL xuất hiện rõ trong tài liệu/web context."""


def _build_rag_prompt(context: str, query: str, domain_instructions: str) -> str:
    """Xây dựng system prompt cho chế độ RAG."""
    return f"""{_get_identity()}

## Cách trả lời dựa trên tài liệu nội bộ

### Quy tắc sử dụng tài liệu
- Chỉ trả lời dựa trên ngữ cảnh tài liệu được cung cấp bên dưới. Không bịa thêm.
- Nếu không tìm thấy thông tin trong ngữ cảnh, trả về đúng chuỗi [[RAG_NO_ANSWER]] và không viết gì thêm.
- **Giữ NGUYÊN VẸN tất cả các bước** có trong tài liệu — không bỏ sót bước nào.
- **Giữ NGUYÊN các đường link** trong tài liệu, đặt link đúng tại bước tương ứng.
- Không lặp lại tên tài liệu trong nội dung trả lời (tên đã hiển thị ở khu vực Tham khảo thêm).
- Không dùng đuôi .pdf trong tên tài liệu. Dẫn nhập tự nhiên: "Theo thông tin trong ...".

### Quy tắc trích dẫn số trang
Mỗi đoạn trong ngữ cảnh được gắn nhãn [Tài liệu N] — N là số thứ tự tài liệu.
- **1 tài liệu:** ghi `(trang X)` ở cuối câu nếu biết số trang. Không dùng `[1]`.
- **2+ tài liệu:** ghi `([N], trang X)` ở cuối câu (N = số tài liệu tương ứng).
- Không tạo số tham chiếu mới. Đừng dùng [2], [3]... cho đoạn khác từ CÙNG một tài liệu.
- Không in dòng "Nguồn tham khảo: [1]..." ở cuối câu trả lời.

### Quy tắc xử lý đường link trong tài liệu
- **QUAN TRỌNG:** Nếu trong ngữ cảnh tài liệu có đường link thực (https://...) dẫn đến biểu mẫu, dịch vụ công, văn bản pháp luật — phải đưa link đó **nguyên vẹn** vào câu trả lời dưới dạng `[Tên hiển thị](url)`.
- Không tự đặt ra URL — chỉ dùng URL có sẵn trong ngữ cảnh.
- Link phải mở tab mới: luôn giữ nguyên URL, frontend sẽ xử lý `target="_blank"`.
- Nếu link là biểu mẫu/tải về: hiển thị `📄 [Tải biểu mẫu tại đây](url)`.
- Nếu link là dịch vụ công: hiển thị `👉 [Thực hiện trực tuyến tại đây](url)`.
- Nếu tài liệu có link biểu mẫu/hồ sơ: hiển thị `📄 [Tải biểu mẫu/hồ sơ tại đây](url)`.
- Nếu không có URL thực trong ngữ cảnh, tuyệt đối không tự tạo link dịch vụ công/hồ sơ. Chỉ nêu tên dịch vụ/thủ tục, không thêm bất kỳ thông báo giải thích về việc thiếu link.
- Nếu link là văn bản pháp luật chỉ dùng khi đó là link thao tác/xem văn bản thật sự cần thiết; không dùng để gắn số nguồn tham khảo trong từng câu.
```
✅ Thủ tục này thực hiện trực tuyến theo Chỉ thị 24/CT-TTg

## [Tên thủ tục]

**Thời hạn giải quyết:** ...
**Lệ phí:** ...
**Cơ quan thực hiện:** ...

### Các bước thực hiện
**Bước 1:** [Mô tả đầy đủ]
👉 [Thực hiện tại đây](URL nếu có)

**Bước 2:** ...
```
{_common_format_rules()}

## Yêu cầu mở rộng theo lĩnh vực
{domain_instructions}

## Ngữ cảnh từ tài liệu nội bộ
{context}

## Câu hỏi của người dùng
{query}"""


def _build_ai_prompt(domain_instructions: str) -> str:
    """Xây dựng system prompt cho chế độ AI (web/kiến thức tổng hợp)."""
    return f"""{_get_identity()}

## Khi trả lời từ kiến thức tổng hợp

### Thông tin pháp luật
- Ghi rõ tên + số hiệu văn bản: "Nghị định 13/2023/NĐ-CP"
- Ghi rõ điều khoản: "theo Điều 25, Khoản 1 Luật Căn cước 2023"
- Nếu không nhớ chính xác số hiệu → ghi tên văn bản + năm, không bịa số.
- Ưu tiên thông tin mới nhất, nhắc người dùng xác minh tại thuvienphapluat.vn hoặc vbpl.vn.
- Với câu hỏi pháp lý/thủ tục, ưu tiên format:
  1. Câu trả lời trực diện gộp kết luận.
  2. "Theo quy định tại Khoản... Điều... [Tên văn bản]:"
  3. Blockquote trích nguyên văn điều khoản liên quan nhất.
  4. Diễn giải dễ hiểu + ví dụ nếu cần. Không lặp lại nội dung đã trích.

### Quy tắc chống lặp
- Không tách "Có, ..." rồi thêm "Kết luận ngắn:" ngay bên dưới — gộp thành 1 câu.
- Không tách mục "Căn cứ pháp lý" rồi thêm một khối "Điều ..." riêng nếu cùng căn cứ.
- Không in dòng "Nguồn tham khảo: [1]..." trong nội dung (phần nguồn có hiển thị riêng).
- Nếu đã trích blockquote điều khoản → phần ngay sau phải là diễn giải/ví dụ, không nhắc lại.

### Quy tắc xử lý đường link
- Nếu nguồn web/tài liệu có URL thực dẫn đến biểu mẫu, dịch vụ công, văn bản pháp luật — đưa nguyên vẹn vào câu trả lời dạng `[Tên](url)`.
- Không tự bịa URL. Chỉ dùng URL xuất hiện rõ ràng trong nguồn.
- Biểu mẫu/tải về: `📄 [Tải biểu mẫu tại đây](url)`.
- Dịch vụ công: `👉 [Thực hiện trực tuyến tại đây](url)`.
- Văn bản pháp luật: `📖 [Xem văn bản đầy đủ](url)`.

### Khi chưa đủ căn cứ
- Nói rõ "chưa đủ cơ sở để khẳng định chính xác hoàn toàn".
- Với câu hỏi lệ phí/tên thủ tục cụ thể → đối chiếu đúng tên thủ tục trước khi kết luận.
{_common_format_rules()}

## Yêu cầu mở rộng theo lĩnh vực
{domain_instructions}"""


@dataclass
class Citation:
    document_name: str
    content: str
    score: float
    segment_id: str
    url: str | None = None
    source_type: str = "document"
    domain: str | None = None
    page_date: str | None = None
    fetched_at: str | None = None
    reliability_score: float | None = None
    document_id: str | None = None   # ID tài liệu gốc để build PDF link
    page_number: int | None = None   # Số trang đầu tiên của chunk


@dataclass
class StreamEvent:
    type: str
    data: object


def _current_datetime_context() -> str:
    tz = ZoneInfo(settings.APP_TIMEZONE)
    now = datetime.now(tz)
    return now.strftime("%H:%M:%S ngày %d/%m/%Y (%Z)")


def _domain_instructions(query: str, *, rag: bool) -> str:
    if is_legal_query(query):
        source_scope = (
            "- Nếu có nhiều nguồn, ưu tiên văn bản pháp luật, cổng thông tin cơ quan nhà nước, hoặc tài liệu nội bộ có trích dẫn văn bản.\n"
            if not rag
            else "- Ưu tiên các đoạn ngữ cảnh có nêu rõ Điều, Khoản, điểm, tên luật/nghị định/thông tư hoặc hiệu lực văn bản.\n"
        )
        guidance = (
            "- Nếu đây là câu hỏi thủ tục hành chính, trình bày theo cấu trúc các mục sau (theo đúng thứ tự):\n"
            "  1. Sơ lược thông tin liên quan\n"
            "  2. Điều kiện / đối tượng áp dụng (nếu có)\n"
            "  3. Hồ sơ cần chuẩn bị\n"
            "  4. Nơi nộp\n"
            "  5. Trình tự các bước thực hiện — bước đầu tiên truy cập Cổng DVCQG PHẢI dùng link: "
            "[Cổng Dịch vụ công Quốc gia](https://dichvucong.gov.vn/p/home/dvc-trang-chu.html). "
            "TUYỆT ĐỐI KHÔNG dùng URL làm label — không được viết [https://...](https://...). "
            "Luôn dùng tên mô tả ngắn gọn làm label, ví dụ: [Cổng Dịch vụ công Quốc gia](...), [Truy cập trực tiếp thủ tục](...). "
            "Nếu nguồn có URL dẫn đến thủ tục cụ thể, ưu tiên URL dạng "
            "'dvc-chi-tiet-thu-tuc-nganh-doc.html?ma_thu_tuc=...' (trang thông tin thủ tục chính thức), "
            "nếu không có thì dùng 'dvc-tthc-thu-tuc-hanh-chinh-chi-tiet.html?ma_thu_tuc=...'. "
            "Hướng dẫn bước tìm thủ tục: 'Bạn có thể gõ \"[tên thủ tục]\" vào ô tìm kiếm hoặc "
            "👉 [Truy cập trực tiếp thủ tục tại đây](URL_thủ_tục_cụ_thể)'. "
            "Không đặt link đăng nhập/đăng ký vào phần này.\n"
            "  6. Lưu ý\n"
            "  7. Căn cứ pháp lý — đặt ở GẦN CUỐI, sau phần Lưu ý, không đặt ở đầu hoặc giữa câu trả lời.\n"
            "  8. Nếu hợp lý, kết thúc bằng gợi ý tự nhiên những bước tiếp theo, viết liền mạch không cần tiêu đề riêng, "
            "ví dụ: 'Bạn có thể tiến hành trước bằng cách...', 'Nếu chưa có CCCD, bạn nên...' v.v."
            if is_procedure_query(query)
            else ""
        )
        return (
            "### Khi câu hỏi thuộc lĩnh vực pháp lý / thủ tục hành chính\n"
            "- Mở đầu bằng 1 câu trả lời trực diện, ngắn gọn, gộp luôn kết luận; không lặp lại thêm mục 'Kết luận ngắn' ngay bên dưới.\n"
            "- Sau câu mở đầu, nếu có căn cứ cụ thể thì viết: 'Theo quy định tại Khoản..., Điều..., tên văn bản:'.\n"
            "- Ngay sau câu đó, trích 1 blockquote ngắn là đúng phần câu chữ pháp lý liên quan nhất.\n"
            "- Không tách riêng một mục 'Căn cứ pháp lý' rồi lại tách tiếp một mục 'Điều ...' nếu cùng nói về một căn cứ.\n"
            "- Sau khi nêu căn cứ pháp lý và thông tin pháp lý hoặc thông tin liên quan, sẽ tiến hành diễn giải thêm thoe cách dễ hiểu và có thể đưa ra ví dụ gần gũi, dễ hiểu.\n"
            "- Nếu chưa đủ căn cứ để khẳng định, phải nói rõ là chưa đủ cơ sở để khẳng định chính xác hoàn toàn.\n"
            "- Không trả lời kiểu chung chung nếu đã có căn cứ cụ thể trong nguồn.\n"
            "- Nếu nguồn có nhiều mục gần giống nhau như tạm trú và thường trú, phải kiểm tra lại tên thủ tục ngay trước mỗi kết luận.\n"
            "- Cuối cùng, trước khi hiển thị nguồn tham khảo thì nên có 1 câu chốt lại hoặc câu lưu ý liên quan hoặc hỏi thêm nếu hợp lý hoặc gợi ý các bước nên làm bây giờ.\n"
            f"{guidance}"
            f"{source_scope}"
        )

    return (
        "### Khi câu hỏi không thuộc lĩnh vực pháp lý\n"
        "- Trả lời tự nhiên, rõ ý, có thể ngắn hoặc dài tùy độ phức tạp của câu hỏi.\n"
    )


def _page_label_from_meta(meta: dict[str, Any] | None) -> str | None:
    info = meta or {}
    page_start = info.get("page_start")
    page_end = info.get("page_end")
    if isinstance(page_start, int):
        if isinstance(page_end, int) and page_end != page_start:
            return f"trang {page_start}-{page_end}"
        return f"trang {page_start}"
    location = info.get("location_label")
    if isinstance(location, str) and location.strip() and location.strip().lower().startswith("trang"):
        return location.strip()
    return None


def _chunk_location_label(chunk: RetrievedChunk) -> str | None:
    meta = chunk.segment_meta or {}
    parts: list[str] = []
    page_label = _page_label_from_meta(meta)
    if page_label:
        parts.append(page_label)
    for key in ("article_ref", "clause_ref", "point_ref"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return " • ".join(parts) if parts else None


def _build_rag_context(chunks: list[RetrievedChunk]) -> tuple[str, list[Citation]]:
    """
    Build context string for LLM prompt + citation list.
    Groups chunks by document — every chunk from the same document shares the same [Tài liệu N] index.
    """
    # Pass 1: assign document indices in order of first appearance
    doc_order: list[str] = []           # normalized names in appearance order
    doc_display: dict[str, str] = {}    # norm → display name
    doc_idx: dict[str, int] = {}        # norm → 1-based index
    doc_url: dict[str, str | None] = {}

    for chunk in chunks:
        display = pretty_document_name(chunk.document_name)
        norm = display.strip().lower()
        if norm not in doc_idx:
            doc_order.append(norm)
            doc_display[norm] = display
            doc_idx[norm] = len(doc_order)   # 1-based
            doc_url[norm] = resolve_document_source_url(chunk.document_name, chunk.document_meta)

    num_unique_docs = len(doc_order)

    # Pass 2: build context parts + collect best citation per document
    parts: list[str] = []
    doc_best: dict[str, Citation] = {}   # norm → best-scored Citation
    doc_hint_shown: set[str] = set()

    for chunk in chunks:
        display = pretty_document_name(chunk.document_name)
        norm = display.strip().lower()
        idx = doc_idx[norm]

        hint = get_document_hint(chunk.document_name, chunk.document_meta) or {}
        source_url = doc_url[norm]
        source_label = hint.get("source_label")
        page_label = _page_label_from_meta(chunk.segment_meta or {})

        header_lines = [f"[Tài liệu {idx}] {display}"]
        if source_label:
            header_lines.append(f"Ban hành bởi: {source_label}")
        if page_label:
            header_lines.append(f"Trang: {page_label}")
        if source_url:
            header_lines.append(f"URL: {source_url}")
        # Show lead only once per document
        if hint.get("lead") and norm not in doc_hint_shown:
            header_lines.append(f"Dẫn nhập: {hint['lead']}")
            doc_hint_shown.add(norm)

        parts.append("\n".join(header_lines) + f"\nNội dung: {chunk.content}")

        # Keep best citation per document (highest score)
        if norm not in doc_best or chunk.score > doc_best[norm].score:
            # Trích số trang đầu tiên từ segment_meta
            seg_meta = chunk.segment_meta or {}
            pages = seg_meta.get("page_numbers") or []
            first_page = int(pages[0]) if pages else None
            if first_page is None and isinstance(seg_meta.get("page_start"), int):
                first_page = int(seg_meta.get("page_start"))

            doc_best[norm] = Citation(
                document_name=display,
                content=page_label or "",
                score=chunk.score,
                segment_id=chunk.segment_id,
                url=source_url,
                source_type="document",
                document_id=getattr(chunk, "document_id", ""),
                page_number=first_page,
            )

    # Append inline citation-format hint so the LLM knows how to cite
    if num_unique_docs == 1:
        cite_hint = (
            "\n\n[Hướng dẫn trích dẫn] Chỉ có 1 tài liệu trong ngữ cảnh. "
            "Dùng (trang X) ở cuối câu nếu biết số trang. Không dùng [1] hay số tham chiếu khác."
        )
    else:
        cite_hint = (
            f"\n\n[Hướng dẫn trích dẫn] Có {num_unique_docs} tài liệu. "
            "Dùng ([1], trang X) hoặc ([2], trang X) v.v. ở cuối câu. "
            "Số trong [] là số [Tài liệu N] tương ứng. "
            "Không tạo thêm số ngoài các số đã có."
        )

    context = "\n\n---\n\n".join(parts) + cite_hint

    # Build final citations in document-appearance order
    citations: list[Citation] = [doc_best[norm] for norm in doc_order if norm in doc_best]

    return context, citations


def _merge_citations(existing: list[Citation], incoming: list[Citation]) -> list[Citation]:
    merged: list[Citation] = []
    seen: set[str] = set()

    for citation in [*existing, *incoming]:
        if citation.source_type == "document":
            key = f"doc::{citation.document_name.strip().lower()}::{citation.segment_id}"
        else:
            key = (
                citation.url
                or f"web::{(citation.document_name or '').strip().lower()}::"
                   f"{(citation.domain or '').strip().lower()}::"
                   f"{(citation.page_date or '').strip().lower()}::"
                   f"{citation.segment_id}"
            ).strip().lower()

        if not key or key in seen:
            continue

        seen.add(key)
        merged.append(citation)

    return merged


def _dedupe_legal_quotes(text: str) -> str:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line
        if raw_line.lstrip().startswith(">"):
            prefix, _, rest = raw_line.partition(">")
            quote_body = rest.lstrip()
            quote_body = re.sub(r'^[“”"]{2,}', '"', quote_body)
            quote_body = re.sub(r'^[“”](?=[“"])', '"', quote_body)
            quote_body = re.sub(r'(?<=[^\s])[”"]{2,}\s*$', '"', quote_body)
            line = f"{prefix}> {quote_body}".rstrip()
        lines.append(line)
    return "\n".join(lines)


def _remove_redundant_legal_basis_section(text: str) -> str:
    if not re.search(r"(?m)^>\s+", text):
        return text
    pattern = re.compile(
        r"(?:^|\n)(?:\*\*)?Căn cứ pháp lý:?\*\*?\s*\n(?:\s*[-*]\s.*(?:\n|$)){1,8}",
        re.IGNORECASE,
    )
    return pattern.sub("\n", text, count=1)


def _replace_pdf_suffixes(text: str) -> str:
    return re.sub(r"(?i)\.pdf\b", "", text or "").strip()


def _linkify_raw_urls(text: str) -> str:
    """
    Chuyển đổi mọi URL thô trong text thành link Markdown.
    Áp dụng cho: URL trong backtick, angle bracket, và URL bare (không có markdown link bao ngoài).
    Không convert URL đã nằm trong [text](url) hoặc blockquote pháp lý.
    """
    if not text:
        return text

    # 1. `<url>` và <url>
    text = re.sub(r'`<(https?://[^>]+)>`', lambda m: f'[{m.group(1)}]({m.group(1)})', text)
    text = re.sub(r'<(https?://[^>]+)>', lambda m: f'[{m.group(1)}]({m.group(1)})', text)

    # 2. `url` trong backtick
    text = re.sub(r'`(https?://[^`\s]+)`', lambda m: f'[{m.group(1)}]({m.group(1)})', text)

    # 3. Bare URLs — không nằm trong markdown link [...](...) đã có
    #    Pattern: https://... không có [text]( trước và không có ) ngay sau
    def _make_md_link(m: re.Match) -> str:
        url = m.group(0).rstrip('.,;)')
        # Lấy nhãn rõ ràng theo domain
        label = _url_display_label(url)
        return f'[{label}]({url})'

    # Tìm bare URL: phải đứng sau khoảng trắng/newline/dấu câu, không phải sau ](
    text = re.sub(
        r'(?<!\]\()(?<!\[)(https?://[^\s\)\]\"\'\`<>]+)',
        lambda m: (
            m.group(0) if re.search(r'\]\(' + re.escape(m.group(0)), text)
            else _make_md_link(m)
        ),
        text,
    )

    return text


def _url_display_label(url: str) -> str:
    """Tạo nhãn hiển thị thân thiện cho URL."""
    _DOMAIN_LABELS = {
        'dichvucong.gov.vn': '🔗 Cổng Dịch vụ công',
        'dvc.gov.vn': '🔗 Cổng DVC',
        'thuvienphapluat.vn': '📖 Thư viện Pháp luật',
        'vbpl.vn': '📖 Văn bản Pháp luật',
        'danhmuctthc.gov.vn': '🔗 Danh mục TTHC',
        'csdl.dichvucong.gov.vn': '🔗 CSDL Dịch vụ công',
        'motcua.danang.gov.vn': '🔗 Một cửa Đà Nẵng',
        'baohiem.gov.vn': '🔗 Bảo hiểm xã hội',
        'toakhoa.gov.vn': '🔗 Tòa khoa',
        'drive.google.com': '📄 Xem biểu mẫu',
        'docs.google.com': '📄 Xem tài liệu',
        'maudon.vn': '📄 Mẫu đơn',
    }
    try:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.lower().lstrip('www.')
        for key, label in _DOMAIN_LABELS.items():
            if domain == key or domain.endswith('.' + key):
                return label
        # Fallback: domain name
        return domain or url[:40]
    except Exception:
        return url[:40]


def _is_enumerated_fragment(text: str) -> bool:
    body = re.sub(r'^\s*>\s?', '', text or '').strip()
    body = body.strip("“”\"'`*_ ")
    # Các mảnh danh sách như 1., a), b) thường bị model hiểu nhầm thành điều khoản.
    return bool(re.match(r'^(?:\d+[.)]|[a-zđ][.)])\s+', body, flags=re.IGNORECASE))


def _is_meaningful_legal_quote(text: str) -> bool:
    body = re.sub(r'^\s*>\s?', '', text or '').strip()
    body = re.sub(r'[*_`"“”]+', '', body).strip()
    if not body:
        return False
    # Không quote tiêu đề/mảnh liệt kê rời rạc.
    if _is_enumerated_fragment(body):
        return False
    if len(body) < 70 or len(re.findall(r'[A-Za-zÀ-ỹđĐ0-9]+', body)) < 10:
        return False
    if body.endswith((':', ';')) and len(body) < 160:
        return False
    legal_signal = bool(re.search(r'(điều\s+\d+|khoản\s+\d+|điểm\s+[a-zđ]|luật|nghị định|thông tư|quyết định)', body.lower()))
    obligation_signal = any(k in body.lower() for k in (
        'công dân', 'người', 'cơ quan', 'tổ chức', 'phải', 'được', 'có trách nhiệm',
        'thực hiện', 'nộp', 'đăng ký', 'cấp', 'thu hồi', 'xử phạt', 'thời hạn',
    ))
    return legal_signal or obligation_signal


def _is_legal_quote_block(block: str) -> bool:
    return _is_meaningful_legal_quote(block)


def _has_legal_quote_intro(line: str) -> bool:
    normalized = (line or '').strip().lower()
    if not normalized:
        return False
    return bool(
        re.search(r'(theo quy định tại|căn cứ tại|theo .*?(điều\s+\d+|khoản\s+\d+|điểm\s+[a-zđ]))', normalized)
    )


def _strip_nonlegal_blockquotes(text: str) -> str:
    if not text:
        return text
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith('>'):
            block = []
            while i < len(lines) and lines[i].lstrip().startswith('>'):
                block.append(lines[i])
                i += 1
            joined = '\n'.join(block)
            previous_nonempty = next((line for line in reversed(out) if line.strip()), '')
            if _is_legal_quote_block(joined):
                out.extend(block)
            else:
                # Dù phía trước có câu dẫn 'Theo quy định...', nếu nội dung quote chỉ là mảnh
                # a), b), c) hoặc tiêu đề rời rạc thì chuyển về text thường để tránh sai nghĩa.
                out.extend([re.sub(r'^\s*>\s?', '', line) for line in block])
            continue
        out.append(lines[i])
        i += 1
    return '\n'.join(out)


def _detach_explanatory_lines_from_legal_quotes(text: str) -> str:
    if not text:
        return text

    explanation_starters = (
        'điều này có nghĩa',
        'có nghĩa là',
        'hiểu đơn giản',
        'nói dễ hiểu',
        'nói cách khác',
        'tức là',
        'ví dụ',
        'lưu ý',
        'tóm lại',
        'bạn cần',
        'bạn nên',
        'trong thực tế',
        'nếu bạn',
        'nói ngắn gọn',
        'giải thích',
        'diễn giải',
    )

    def should_unquote(content: str) -> bool:
        normalized = re.sub(r'^[-*•\s]+', '', content.strip()).lower()
        normalized = re.sub(r'^\*\*([^*]+)\*\*\s*:?\s*', r'\1 ', normalized)
        return any(normalized.startswith(prefix) for prefix in explanation_starters)

    lines = text.splitlines()
    output: list[str] = []
    in_legal_quote = False
    pending_legal_intro = False

    for line in lines:
        if not line.lstrip().startswith('>'):
            output.append(line)
            pending_legal_intro = _has_legal_quote_intro(line)
            if not line.strip():
                in_legal_quote = False
            continue

        quote_body = re.sub(r'^\s*>\s?', '', line)
        normalized_quote = quote_body.strip().lower()

        if pending_legal_intro and quote_body.strip():
            in_legal_quote = True
            pending_legal_intro = False
            output.append(line)
            continue

        if _is_legal_quote_block(quote_body):
            in_legal_quote = True
            output.append(line)
            continue

        if should_unquote(quote_body):
            output.append(quote_body)
            in_legal_quote = False
            continue

        if in_legal_quote and normalized_quote and not re.search(r'^(khoản|điều|điểm|mục|phần|[a-z]\)|\d+[.)])\b', normalized_quote):
            output.append(quote_body)
            in_legal_quote = False
            continue

        output.append(line)

    return '\n'.join(output)


def _normalize_markdown_layout(text: str) -> str:
    if not text:
        return text

    normalized = text.replace('\r\n', '\n').replace('\r', '\n')

    # Tách tiêu đề đánh số đang bị dính vào cuối câu trước đó.
    normalized = re.sub(
        r'(?<!\n)(?<=[\.:])\s+(?=(?:\d+\.\s+[^\n:]{2,120}:))',
        '\n\n',
        normalized,
    )

    # Đổi bullet inline thành danh sách markdown thật.
    normalized = re.sub(r':\s+\*\s+(?=\*{0,2}[A-ZÀ-ỴĐa-zà-ỹ0-9])', ':\n\n- ', normalized)
    # Chỉ convert bullet inline không có thụt lề (tránh mất indent của sub-item)
    normalized = re.sub(r'(?<!\n)(?<! )(?<!\t)[ \t]+\*[ \t]+(?=\*{0,2}[A-ZÀ-ỴĐa-zà-ỹ0-9])', '\n- ', normalized)

    # Nếu sau dấu : là list thì chèn dòng trống để renderer hiểu đúng markdown.
    normalized = re.sub(r'(:)\n(-\s)', r'\1\n\n\2', normalized)
    normalized = re.sub(r'(:)\n(\d+\.\s)', r'\1\n\n\2', normalized)

    # Xử lý từng dòng: giữ indent cho list items, dọn khoảng trắng cho dòng thường.
    lines = normalized.split('\n')
    result_lines: list[str] = []
    for line in lines:
        stripped = line.lstrip(' \t')
        is_list_item = bool(
            stripped and (
                (stripped[0] in '-*+' and len(stripped) > 1 and stripped[1] == ' ')
                or re.match(r'^\d+\.\s', stripped)
            )
        )
        if is_list_item:
            # Giữ indent (chuẩn hoá về bội số 2), chỉ dọn space nội dung
            indent = len(line) - len(stripped)
            result_lines.append(' ' * indent + re.sub(r'[ \t]{2,}', ' ', stripped).rstrip())
        else:
            # Dòng thường: bỏ indent thừa, dọn space nội bộ
            result_lines.append(re.sub(r'[ \t]{2,}', ' ', stripped).rstrip())

    normalized = '\n'.join(result_lines)
    normalized = re.sub(r'[ \t]+\n', '\n', normalized)
    normalized = re.sub(r'\n{3,}', '\n\n', normalized)
    return normalized.strip()


def _remove_inline_source_references(text: str) -> str:
    """
    Xóa phần 'Nguồn tham khảo:' khỏi body vì CitationsPanel đã hiển thị riêng.
    Bắt cả dạng:
      - 'Nguồn tham khảo: [1] ...'
      - '**Nguồn tham khảo:**\\n- Sổ tay...'  (bulleted list sau heading)
      - 'Nguồn:\\n- ...'
    """
    if not text:
        return text

    # Dạng inline: Nguồn tham khảo: [1] ...
    text = re.sub(
        r'\n?\*{0,2}Nguồn\s+tham\s+khảo\s*:?\*{0,2}:?\s*\[\d+\][^\n]*(?:\n(?!\n)[^\n]*)*',
        '',
        text,
        flags=re.IGNORECASE,
    )

    # Dạng heading + bulleted list nguồn
    # Xóa từ tiêu đề "Nguồn tham khảo:" / "Nguồn:" đến hết danh sách bullet liền sau
    text = re.sub(
        r'\n{0,2}\*{0,2}(?:Nguồn\s+tham\s+khảo|Nguồn)\s*:?\*{0,2}\s*\n'
        r'(?:[-*•]\s+[^\n]+\n?)+',
        '\n',
        text,
        flags=re.IGNORECASE,
    )

    return text.strip()


def _remove_duplicate_legal_content(text: str) -> str:
    """
    Xóa những đoạn văn xuôi lặp lại gần như y nguyên nội dung của blockquote liền trước.
    """
    if not text:
        return text
    lines = text.splitlines()
    out: list[str] = []
    last_quote_words: set[str] = set()
    in_quote = False

    for line in lines:
        if line.lstrip().startswith('>'):
            in_quote = True
            body = re.sub(r'^\s*>\s?', '', line)
            last_quote_words.update(w.lower() for w in re.findall(r'\w+', body) if len(w) > 3)
            out.append(line)
        else:
            if in_quote and not line.strip():
                in_quote = False
                out.append(line)
                continue
            in_quote = False
            if last_quote_words:
                line_words = set(w.lower() for w in re.findall(r'\w+', line) if len(w) > 3)
                if line_words and len(line_words & last_quote_words) / max(len(line_words), 1) > 0.75:
                    # Dòng này trùng quá nhiều với blockquote trước → bỏ
                    continue
                else:
                    last_quote_words = set()
            out.append(line)
    return '\n'.join(out)
    if not text or not citations:
        return text

    document_citations = [c for c in citations if c.source_type == 'document']
    web_citations = [c for c in citations if c.source_type == 'web']

    doc_index: dict[str, int] = {}
    for idx, c in enumerate(document_citations, start=1):
        doc_index.setdefault(c.document_name.strip().lower(), idx)

    def page_of(c: Citation) -> str | None:
        val = (c.content or '').strip()
        if not val:
            return None
        m = re.search(r'trang\s*([0-9-]+)', val, flags=re.IGNORECASE)
        if m:
            return f'Trang {m.group(1)}'
        return val

    def doc_repl(match: re.Match[str]) -> str:
        raw_name = re.sub(r'(?i)\.pdf\b', '', match.group(1)).strip()
        raw_page = match.group(2)
        target = None
        for c in document_citations:
            name = c.document_name.strip().lower()
            rn = raw_name.lower()
            if rn == name or rn in name or name in rn:
                target = c
                break
        if not target:
            return ''

        page_label = raw_page or page_of(target)
        if len(document_citations) == 1:
            return f'({page_label})' if page_label else ''

        idx = doc_index.get(target.document_name.strip().lower(), 1)
        return f'([{idx}], {page_label})' if page_label else f'([{idx}])'

    pattern = re.compile(
        r'\[(?:Tài liệu|Nguồn)\s*:\s*([^,\]]+?)(?:\.pdf)?(?:,\s*(?:đoạn\s*\d+|(?:(trang\s*[0-9-]+))))?\]'
    )
    text = pattern.sub(doc_repl, text)

    if len(web_citations) <= 1:
        text = re.sub(r'(?<!\()\s*\[1\](?!\()', '', text)
    else:
        max_idx = len(web_citations)

        def web_repl(match: re.Match[str]) -> str:
            idx = int(match.group(1))
            return f'[{idx}]' if 1 <= idx <= max_idx else ''

        text = re.sub(r'\[(\d+)\]', web_repl, text)

    # Giữ nguyên xuống dòng markdown, chỉ sửa khoảng trắng ngang và dấu câu.
    text = re.sub(r'[ \t]{2,}', ' ', text)
    text = re.sub(r'[ \t]+([,.;:!?])', r'\1', text)
    text = re.sub(r'([,.;:!?])(\([^)]+\)|\[[^\]]+\])', r'\1 \2', text)
    text = re.sub(r'[ \t]+\n', '\n', text)
    text = re.sub(r'\n[ \t]+', '\n', text)
    return text


def _linkify_inline_doc_references(text: str, citations: list[Citation]) -> str:
    if not text or not citations:
        return text

    page_queues: dict[str, deque[str]] = defaultdict(deque)
    url_by_name: dict[str, str] = {}
    display_by_name: dict[str, str] = {}

    for citation in citations:
        if citation.source_type not in {"document", "web"}:
            continue
        display = pretty_document_name(citation.document_name)
        norm = re.sub(r"\s+", " ", display.lower()).strip()
        display_by_name[norm] = display
        if citation.url:
            url_by_name[norm] = citation.url
        page_label = (citation.content or "").strip()
        if page_label and page_label not in page_queues[norm]:
            page_queues[norm].append(page_label)

    def _pick_name(raw_name: str) -> tuple[str | None, str | None, str | None]:
        candidate = re.sub(r"(?i)\.pdf\b", "", raw_name or "").strip()
        norm = re.sub(r"\s+", " ", candidate.lower()).strip()
        for known_norm, display in display_by_name.items():
            if norm == known_norm or norm in known_norm or known_norm in norm:
                page = page_queues[known_norm].popleft() if page_queues.get(known_norm) else None
                return display, url_by_name.get(known_norm), page
        return pretty_document_name(candidate), None, None

    pattern = re.compile(
        r"\[(?:Tài liệu|Nguồn)\s*:\s*([^,\]]+?)(?:\.pdf)?(?:,\s*(?:đoạn\s*\d+|trang\s*[0-9-]+))?\]"
    )

    def repl(match: re.Match[str]) -> str:
        display, url, page = _pick_name(match.group(1))
        if page:
            return f"({page})"
        return ""

    return pattern.sub(repl, text)




def _remove_model_reference_sections(text: str) -> str:
    """Chỉ cho hiển thị nguồn trong CitationsPanel, không để model tự in phần nguồn trong thân bài."""
    if not text:
        return text
    lines = text.splitlines()
    out: list[str] = []
    skipping = False
    _SOURCE_HEADINGS = re.compile(
        r'^(?:#{1,6}\s*)?(?:\*\*)?(?:'
        r'tham\s+khảo\s+thêm'
        r'|nguồn\s+tham\s+khảo'
        r'|tài\s+liệu\s+tham\s+khảo'
        r'|nguồn'
        r')\s*:?(?:\*\*)?$',
        flags=re.IGNORECASE,
    )
    for line in lines:
        stripped = line.strip()
        if _SOURCE_HEADINGS.match(stripped):
            skipping = True
            continue
        if skipping:
            if not stripped:
                continue
            if re.match(r'^(?:[-*•]\s+|\d+[.)]\s+)', stripped) or 'http' in stripped.lower() or 'xem tài liệu' in stripped.lower():
                continue
            if re.match(r'^#{1,6}\s+', stripped) or re.match(
                r'^(Lưu ý|Tóm lại|Kết luận|Ví dụ|Các bước|Hồ sơ|Điều kiện)\b', stripped, flags=re.IGNORECASE
            ):
                skipping = False
            else:
                continue
        out.append(line)
    return '\n'.join(out).strip()

def _strip_inline_source_links(text: str) -> str:
    """Gỡ link/số tham khảo bị lẫn vào thân câu trả lời; nguồn đã hiển thị ở panel riêng.
    KHÔNG xóa:
      - ([N], trang X)  → frontend linkify thành PDF page link
      - (trang X...)    → frontend linkify thành PDF page link
    XÓA:
      - ([N])           → web citation inline, nguồn đã ở panel Tham khảo thêm
    """
    if not text:
        return text
    # [1](https://...), [[1]](https://...) — link markdown gắn số nguồn trực tiếp
    text = re.sub(r'\s*\[\[?\d+\]?\]\((?:https?://|/)[^)]+\)', '', text)
    # ([1])(https://...) — dạng biến thể ít gặp
    text = re.sub(r'\s*\(\s*\[\d+\]\s*\)\((?:https?://|/)[^)]+\)', '', text)
    # ([N]) standalone — web citation, nguồn đã hiển thị ở panel; KHÔNG xóa ([N], trang X)
    text = re.sub(r'\s*\(\s*\[\d+\]\s*\)(?!\s*,?\s*trang)', '', text, flags=re.IGNORECASE)
    # [1] standalone (không trong ngoặc, không trước trang) — xóa
    # Bảo vệ: ([N], trang X) và [1](url)
    text = re.sub(r'(?<!\()\s*\[\d+\](?!\s*,\s*trang)(?!\()(?!\]?\()', '', text, flags=re.IGNORECASE)
    # Dọn dấu câu thừa
    text = re.sub(r'\s+([,.;:!?])', r'\1', text)
    text = re.sub(r'\(\s*[,;]\s*\)', '', text)
    return text


def _format_legal_blockquotes(text: str) -> str:
    """Chuẩn hóa trích điều khoản: không ngoặc kép lặp, không in đậm, chỉ in nghiêng."""
    if not text:
        return text

    lines = text.splitlines()
    out: list[str] = []
    previous_was_quote = False

    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith('>'):
            prefix = line[: len(line) - len(stripped)]
            body = re.sub(r'^>\s?', '', stripped).strip()
            # Gỡ emphasis/ngoặc kép bao ngoài bị model lặp.
            body = body.replace('**', '')
            body = re.sub(r'^[“”"\'`\s]+', '', body)
            body = re.sub(r'[“”"\'`\s]+$', '', body)
            body = body.strip()
            if body:
                # Tránh wrap hai lần nếu model đã italic.
                body = body.strip('*_')
                out.append(f'{prefix}> *{body}*')
            else:
                out.append(f'{prefix}>')
            previous_was_quote = True
            continue

        if previous_was_quote and line.strip():
            # Tách phần diễn giải ra khỏi quote bằng một dòng trống.
            if out and out[-1].strip():
                out.append('')
        out.append(line)
        previous_was_quote = False

    return '\n'.join(out)


def _allowed_urls_from_citations(citations: list[Citation] | None) -> set[str]:
    urls: set[str] = set()
    for c in citations or []:
        if c.url:
            urls.add(c.url.strip())
        # Nếu web context trích được link thao tác/hồ sơ, URL đó được nhúng trong content citation.
        for m in re.finditer(r"https?://[^\s\)\]<>\"']+", c.content or ''):
            urls.add(m.group(0).rstrip('.,;)'))
    return urls


def _is_action_link_label(label: str) -> bool:
    normalized = (label or '').lower()
    return any(k in normalized for k in (
        'thực hiện', 'trực tuyến', 'nộp hồ sơ', 'dịch vụ công', 'tải', 'biểu mẫu',
        'mẫu đơn', 'hồ sơ', 'tại đây', 'xem văn bản', 'xem đầy đủ'
    ))


def _guard_untrusted_action_links(text: str, citations: list[Citation] | None = None) -> str:
    """Không cho LLM tự bịa link thao tác/hồ sơ.

    Chỉ giữ action link nếu URL có trong citations. Nếu chưa có nguồn URL rõ ràng,
    chuyển link thành text thường để tránh dẫn người dân vào link sai.
    """
    if not text:
        return text
    allowed = _allowed_urls_from_citations(citations)
    if not allowed:
        # Nếu không có citations URL, vẫn cho phép link văn bản đã xuất hiện dưới dạng URL thật trong text,
        # nhưng action link do model bịa sẽ bị bỏ.
        allowed = set()

    def repl(match: re.Match[str]) -> str:
        label = match.group(1).strip()
        url = match.group(2).strip()
        if not _is_action_link_label(label):
            return match.group(0)
        if url in allowed:
            return match.group(0)
        # Cho phép link nội bộ /api/documents vì do hệ thống tạo, không phải model bịa.
        if url.startswith('/api/documents/'):
            return match.group(0)
        # URL không tin cậy: chỉ giữ tên hiển thị, không hiện thông báo lỗi
        return label

    return re.sub(r'\[([^\]]+)\]\((https?://[^\)]+|/api/documents/[^\)]+)\)', repl, text)


def _format_inline_references(text: str, citations: list[Citation]) -> str:
    """Không biến [1] thành link trong thân câu trả lời. Nguồn xem ở CitationsPanel."""
    return text


def _fix_url_as_label_links(text: str) -> str:
    """
    Sửa pattern LLM hay tạo: [https://example.com](https://example.com)
    → giữ lại URL nhưng thay label bằng tên thân thiện dựa trên domain/path.
    Ví dụ: [https://dichvucong.gov.vn/p/home/dvc-trang-chu.html](https://dichvucong.gov.vn/p/home/dvc-trang-chu.html)
           → [Cổng Dịch vụ công Quốc gia](https://dichvucong.gov.vn/p/home/dvc-trang-chu.html)
    """
    _DOMAIN_LABELS = {
        'dichvucong.gov.vn': 'Cổng Dịch vụ công Quốc gia',
        'dichvucong.bocongan.gov.vn': 'Cổng DVC Bộ Công an',
        'thuvienphapluat.vn': 'Thư viện Pháp luật',
        'luatvietnam.vn': 'Luật Việt Nam',
        'chinhphu.vn': 'Cổng Thông tin Chính phủ',
        'xaydungchinhsach.chinhphu.vn': 'Xây dựng Chính sách',
        'bocongan.gov.vn': 'Bộ Công an',
        'moj.gov.vn': 'Bộ Tư pháp',
        'gdt.gov.vn': 'Tổng cục Thuế',
        'vbpl.vn': 'Văn bản Pháp luật',
    }

    def _replace(m: re.Match) -> str:
        label = m.group(1).strip()
        url = m.group(2).strip()
        # Chỉ xử lý khi label chính là URL (bắt đầu bằng http)
        if not (label.startswith('http://') or label.startswith('https://')):
            return m.group(0)
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            domain = parsed.netloc.lower().lstrip('www.')
            friendly = _DOMAIN_LABELS.get(domain)
            if not friendly:
                # Tạo label từ domain ngắn gọn
                friendly = domain.replace('.gov.vn', '').replace('.vn', '').replace('.', ' ').title()
            return f'[{friendly}]({url})'
        except Exception:
            return f'[Truy cập tại đây]({url})'

    return re.sub(r'\[([^\]]+)\]\((https?://[^\)]+)\)', _replace, text)


def _clean_response_text(text: str, citations: list[Citation] | None = None) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return cleaned
    cleaned = _normalize_markdown_layout(cleaned)
    cleaned = _fix_url_as_label_links(cleaned)   # sửa [URL](URL) → [Tên thân thiện](URL)
    cleaned = _strip_nonlegal_blockquotes(cleaned)
    cleaned = _detach_explanatory_lines_from_legal_quotes(cleaned)
    cleaned = _format_legal_blockquotes(cleaned)
    cleaned = _remove_redundant_legal_basis_section(cleaned)
    cleaned = _remove_inline_source_references(cleaned)
    cleaned = _remove_model_reference_sections(cleaned)
    cleaned = _remove_duplicate_legal_content(cleaned)
    cleaned = _replace_pdf_suffixes(cleaned)
    cleaned = _linkify_raw_urls(cleaned)
    cleaned = _guard_untrusted_action_links(cleaned, citations or [])
    cleaned = _linkify_inline_doc_references(cleaned, citations or [])
    cleaned = _format_inline_references(cleaned, citations or [])
    cleaned = _strip_inline_source_links(cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned

def _normalize_legal_answer_structure(text: str) -> str:
    content = (text or "").strip()
    if not content:
        return content

    content = re.sub(
        r'(?is)^(Có[,:\s].*?)\n+\s*(?:\*\*)?Kết luận ngắn:?\*\*?\s*(.+?)(?=\n{2,}|\n(?:Căn cứ pháp lý|Theo quy định tại)|$)',
        lambda m: m.group(2).strip()
        if m.group(2).strip().lower().startswith(("có,", "không,", "được,", "phải,", "không phải"))
        else f"{m.group(1).strip()} {m.group(2).strip()}",
        content,
        count=1,
    )

    content = re.sub(
        r"(?im)^(?:\*\*)?Căn cứ pháp lý:?\*\*?\s*(Theo quy định tại .+)$",
        r"\1",
        content,
    )

    content = re.sub(
        r'(?im)^\s*[“"\']?(Điều\s+\d+[^"\n]*)[”"\']?\s*$\n?',
        "",
        content,
    )

    content = re.sub(r"\n{3,}", "\n\n", content).strip()
    return content


def _ensure_rag_source_lead(text: str, citations: list[Citation]) -> str:
    if not text.strip():
        return text
    first_doc = next((citation for citation in citations if citation.source_type == "document"), None)
    if not first_doc:
        return text

    display_name = pretty_document_name(first_doc.document_name)
    hint = get_document_hint(display_name, {}) or {}
    lowered_head = text[:260].lower()
    if display_name.lower() in lowered_head or "theo thông tin trong" in lowered_head:
        return text

    lead = str(
        hint.get("lead")
        or f"Theo thông tin trong tài liệu {display_name}, tôi trả lời câu hỏi của bạn như sau:"
    ).strip()
    return lead + "\n\n" + text.lstrip()


_RAG_NO_ANSWER_MARKERS = (
    "tài liệu không đề cập rõ",
    "tài liệu chưa đề cập rõ",
    "không tìm thấy thông tin trong ngữ cảnh",
    "không tìm thấy thông tin trong tài liệu",
    "ngữ cảnh không đề cập",
    "không có thông tin trong tài liệu",
    "không đủ thông tin trong tài liệu",
    "chưa có thông tin trong tài liệu",
    "không có trong tài liệu",
    "không được đề cập trong tài liệu",
    "không có nội dung nào đề cập",
    "không có nội dung đề cập",
    "không đề cập đến",
    "chưa đề cập đến",
    "không nói đến",
    "chưa nói đến",
    "không có thông tin nào về",
    "các tài liệu này tập trung vào",
    "tài liệu hiện có không chứa",
)


def _rag_answer_is_total_no_answer(text: str) -> bool:
    normalized = " ".join((text or "").lower().split())
    if not normalized:
        return True
    if normalized.strip() == "[[rag_no_answer]]":
        return True
    if len(normalized) < 260 and any(marker in normalized for marker in _RAG_NO_ANSWER_MARKERS):
        return True
    return False


def _should_fallback_to_web_after_rag(query: str, text: str) -> bool:
    normalized = " ".join((text or "").lower().split())
    if not normalized:
        return True

    if "[[rag_no_answer]]" in normalized:
        return True

    if any(marker in normalized for marker in _RAG_NO_ANSWER_MARKERS):
        return True

    if is_legal_query(query) or is_procedure_query(query):
        fallback_patterns = (
            ("không" in normalized and "đề cập" in normalized),
            ("không" in normalized and "thông tin" in normalized and "tài liệu" in normalized),
            ("chưa đủ cơ sở" in normalized and "tài liệu" in normalized),
            ("không có" in normalized and "tài liệu" in normalized and "về" in normalized),
        )
        if any(fallback_patterns):
            return True

    return False


def _build_history(messages: list[Message]) -> list[dict]:
    history: list[dict] = []
    limit = settings.CONVERSATION_HISTORY_LIMIT
    recent = messages[-limit * 2:] if len(messages) > limit * 2 else messages
    for msg in recent:
        role = "user" if msg.role == "user" else "model"
        history.append({"role": role, "parts": [msg.content]})
    return history


def _coerce_citation(raw: Any) -> Citation:
    if isinstance(raw, Citation):
        return raw
    if not isinstance(raw, dict):
        raise TypeError("Invalid citation payload")

    url = raw.get("url") or raw.get("source_url") or raw.get("link") or raw.get("href")

    document_name = (
        raw.get("document_name")
        or raw.get("title")
        or raw.get("name")
        or raw.get("domain")
        or "Nguồn web"
    )

    content = raw.get("content") or raw.get("snippet") or raw.get("excerpt") or ""

    source_type = raw.get("source_type") or ("web" if url else "document")

    segment_id = (
        raw.get("segment_id")
        or raw.get("id")
        or url
        or f"{document_name}::{raw.get('page_date') or raw.get('fetched_at') or ''}"
    )

    return Citation(
        document_name=document_name,
        content=content,
        score=float(raw.get("score") or 0.0),
        segment_id=str(segment_id),
        url=url,
        source_type=source_type,
        domain=raw.get("domain"),
        page_date=raw.get("page_date"),
        fetched_at=raw.get("fetched_at"),
        reliability_score=raw.get("reliability_score"),
    )


def _safe_chunk_text(response_chunk: Any) -> str:
    try:
        text = getattr(response_chunk, "text")
        if text:
            return str(text)
    except Exception:
        pass

    parts_text: list[str] = []
    try:
        candidates = getattr(response_chunk, "candidates", None) or []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", None) or []
            for part in parts:
                part_text = getattr(part, "text", None)
                if part_text:
                    parts_text.append(str(part_text))
    except Exception:
        return ""

    return "".join(parts_text)


async def _gemini_stream_in_thread(
    chat_factory,   # callable() → chat session (để tạo mới khi rotate key)
    prompt: Any,
    max_retries: int = 3,
) -> AsyncGenerator[str, None]:
    """
    Stream Gemini response trong thread riêng.
    Khi gặp quota/transient error: rotate API key và retry (tối đa max_retries lần).
    Không bao giờ raise lỗi quota ra ngoài nếu còn retry — người dùng không thấy bị gián đoạn.
    """
    _SENTINEL = object()
    loop = asyncio.get_event_loop()

    for attempt in range(max_retries + 1):
        token_queue: queue.Queue = queue.Queue()

        # Tạo chat session mới với key hiện tại (đã rotate nếu cần)
        chat = chat_factory()

        _usage_meta: dict = {}

        def _worker():
            try:
                response = chat.send_message(prompt, stream=True)
                for chunk in response:
                    chunk_text = _safe_chunk_text(chunk)
                    if chunk_text:
                        token_queue.put(chunk_text)
                # Sau khi stream xong, lấy usage_metadata nếu có
                try:
                    meta = response.usage_metadata
                    if meta:
                        _usage_meta['prompt'] = getattr(meta, 'prompt_token_count', 0) or 0
                        _usage_meta['candidates'] = getattr(meta, 'candidates_token_count', 0) or 0
                        _usage_meta['total'] = getattr(meta, 'total_token_count', 0) or 0
                except Exception:
                    pass
            except Exception as e:
                token_queue.put(e)
            finally:
                token_queue.put(_SENTINEL)

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()

        error_encountered: Optional[Exception] = None
        emitted_any = False

        while True:
            item = await loop.run_in_executor(None, token_queue.get)
            if item is _SENTINEL:
                break
            if isinstance(item, Exception):
                error_encountered = item
                break
            emitted_any = True
            yield item

        if error_encountered is None:
            # Phát usage_metadata nếu có (để caller tính token chính xác)
            if _usage_meta.get('total'):
                yield f"__usage__:{json.dumps(_usage_meta)}"
            return   # success

        # Nếu đã phát token thì không thể retry (stream đã bắt đầu)
        if emitted_any:
            raise error_encountered

        is_quota = _GeminiKeyPool.is_quota_error(error_encountered)
        is_transient = _GeminiKeyPool.is_transient_error(error_encountered)

        if is_quota:
            retry_after = _key_pool.parse_retry_after(error_encountered)
            failed_key = _key_pool.current_key()
            rotated = _key_pool.rotate_on_quota(failed_key=failed_key, retry_after=retry_after)
            if rotated and attempt < max_retries:
                logger.info("Quota error → key rotated (retry_after=%.0fs), retry %d/%d",
                            retry_after or _key_pool._COOLDOWN_SEC, attempt + 1, max_retries)
                await asyncio.sleep(1.5)
                continue
        elif is_transient and attempt < max_retries:
            wait = 2.0 * (attempt + 1)
            logger.info("Transient error → retry %d/%d sau %.1fs", attempt + 1, max_retries, wait)
            await asyncio.sleep(wait)
            continue

        raise error_encountered


def _sse(event: StreamEvent) -> str:
    payload = json.dumps({"type": event.type, "data": event.data}, ensure_ascii=False)
    return f"data: {payload}\n\n"


async def _stream_static_text(text: str) -> AsyncGenerator[StreamEvent, None]:
    remaining = text or ""
    while remaining:
        chunk = remaining[:120]
        remaining = remaining[120:]
        yield StreamEvent("token", chunk)
        await asyncio.sleep(0)


class ChatEngine:
    def __init__(self):
        self._gen_config = genai.GenerationConfig(
            temperature=settings.GEMINI_TEMPERATURE,
            max_output_tokens=settings.GEMINI_MAX_OUTPUT_TOKENS,
            top_p=settings.GEMINI_TOP_P,
        )

    def _make_model(self) -> genai.GenerativeModel:
        """Tạo model mới với key khả dụng (không cooling)."""
        key = _key_pool.get_available_key()
        if key:
            genai.configure(api_key=key)
        return genai.GenerativeModel(
            settings.GEMINI_MODEL,
            generation_config=self._gen_config,
        )

    async def stream_response(
        self,
        query: str,
        decision: RouteDecision,
        history: list[Message],
        db: AsyncSession,
        conversation: Conversation,
        image_part: Optional[Any] = None,
        force_web: bool = False,
        session_key: Optional[str] = None,
        request_start_ms: Optional[float] = None,
    ) -> AsyncGenerator[str, None]:
        from app.chat.session_cache import (
            cache_chunks, get_cached_chunks, maybe_update_summary,
            get_session_summary,
            _SUMMARY_EVERY_N_TURNS as SUMMARY_EVERY_N_TURNS,
        )

        # Dùng thời điểm bắt đầu từ endpoint (bao gồm routing/RAG overhead) nếu được truyền vào
        start_ms = request_start_ms if request_start_ms is not None else time.time()
        full_text = ""
        citations: list[Citation] = []
        tokens_used = 0
        latency_ms = 0
        response_mode = decision.mode
        logger.info("decision.mode = %s", getattr(decision.mode, "value", decision.mode))
        logger.info("decision.reason = %s", decision.reason)
        logger.info("assessment = %s", decision.assessment)
        logger.info("force_web_at_engine = %s", force_web)
        logger.info("chunks_count = %s", len(decision.chunks or []))
        try:
            assessment = decision.assessment or {}
            is_greeting = decision.reason == "greeting" or is_greeting_query(query)

            if is_greeting and image_part is None:
                response_mode = AnswerMode.AI
                yield _sse(StreamEvent("mode", response_mode.value))

                greeting_text = "Xin chào! Tôi có thể giúp gì cho bạn hôm nay?"
                async for event in _stream_static_text(greeting_text):
                    full_text += str(event.data)
                    yield _sse(event)

            elif (
                assessment.get("should_refuse_precise")
                and decision.mode == AnswerMode.AI
                and image_part is None
                and not force_web
            ):
                response_mode = AnswerMode.AI
                yield _sse(StreamEvent("mode", response_mode.value))

                fallback_text = build_safe_fallback_answer(query, _AssessmentAdapter(assessment))
                async for event in _stream_static_text(fallback_text):
                    full_text += str(event.data)
                    yield _sse(event)

            else:
                if decision.mode == AnswerMode.RAG:
                    # ── Lớp 1: kiểm tra retrieval cache ─────────────────────
                    effective_chunks = decision.chunks
                    if session_key and not is_greeting and image_part is None:
                        cached = get_cached_chunks(session_key, query)
                        if cached:
                            # Merge cached chunks với fresh chunks (fresh chunks ưu tiên hơn)
                            fresh_ids = {getattr(c, "segment_id", "") for c in (effective_chunks or [])}
                            extra = [c for c in cached if getattr(c, "segment_id", "") not in fresh_ids]
                            if extra:
                                effective_chunks = list(effective_chunks or []) + extra
                                logger.info("Session cache HIT — appended %d cached chunks (total=%d)", len(extra), len(effective_chunks))

                    rag_text, rag_citations, rag_usage = await self._generate_rag_answer(
                        query=query,
                        chunks=effective_chunks,
                        history=history,
                        session_summary=get_session_summary(session_key) if session_key else None,
                    )
                    rag_text = _clean_response_text(rag_text, rag_citations)

                    # Lưu chunks vào cache nếu RAG thành công
                    if session_key and effective_chunks and not _should_fallback_to_web_after_rag(query, rag_text):
                        cache_chunks(session_key, effective_chunks)

                    if _should_fallback_to_web_after_rag(query, rag_text):
                        logger.info(
                            "RAG did not answer sufficiently; fallback to web search for query=%s",
                            query,
                        )

                        response_mode = AnswerMode.AI
                        total_no_answer = _rag_answer_is_total_no_answer(rag_text)
                        yield _sse(StreamEvent("mode", response_mode.value if total_no_answer else "ai_rag"))

                        fallback_support_chunks = [] if total_no_answer else effective_chunks
                        # Nếu RAG hoàn toàn không trả lời được → web-only, không hiển thị tài liệu nội bộ.
                        # Nếu RAG trả lời được một phần nhưng còn thiếu → dùng AI + RAG + web.
                        async for event in self._stream_ai(
                            query=query,
                            history=history,
                            support_chunks=fallback_support_chunks,
                            image_part=image_part,
                            force_web=True,
                            emit_support_citations=not total_no_answer,
                        ):
                            if event.type == "token":
                                full_text += str(event.data)
                                yield _sse(event)
                            elif event.type == "citations":
                                citations = _merge_citations(
                                    citations,
                                    [_coerce_citation(c) for c in event.data],
                                )
                            elif event.type == "usage":
                                ai_total = (event.data or {}).get('total', 0)
                                if ai_total:
                                    tokens_used = ai_total
                    elif force_web:
                        # Câu hỏi cần cập nhật/kiểm chứng nhưng RAG có căn cứ: dùng cả RAG + web.
                        response_mode = AnswerMode.AI
                        yield _sse(StreamEvent("mode", "ai_rag"))

                        async for event in self._stream_ai(
                            query=query,
                            history=history,
                            support_chunks=effective_chunks,
                            image_part=image_part,
                            force_web=True,
                            emit_support_citations=True,
                        ):
                            if event.type == "token":
                                full_text += str(event.data)
                                yield _sse(event)
                            elif event.type == "citations":
                                citations = _merge_citations(
                                    citations,
                                    [_coerce_citation(c) for c in event.data],
                                )
                    else:
                        response_mode = AnswerMode.RAG
                        citations = _merge_citations(citations, rag_citations)
                        yield _sse(StreamEvent("mode", response_mode.value))

                        async for event in _stream_static_text(rag_text):
                            full_text += str(event.data)
                            yield _sse(event)

                else:
                    response_mode = AnswerMode.AI
                    yield _sse(StreamEvent("mode", response_mode.value))

                    support_chunks: list[RetrievedChunk] = []
                    best_score = float(assessment.get("best_score") or 0.0)

                    allow_internal_support = (
                        decision.chunks
                        and best_score >= 0.45
                        and not (
                            force_web
                            and assessment.get("should_force_web")
                            and not assessment.get("should_use_rag")
                        )
                    )

                    if allow_internal_support:
                        support_chunks = decision.chunks

                    async for event in self._stream_ai(
                        query=query,
                        history=history,
                        support_chunks=support_chunks,
                        image_part=image_part,
                        force_web=force_web,
                    ):
                        if event.type == "token":
                            full_text += str(event.data)
                            yield _sse(event)
                        elif event.type == "citations":
                            citations = _merge_citations(
                                citations,
                                [_coerce_citation(c) for c in event.data],
                            )
                        elif event.type == "usage":
                            # Cập nhật token từ Gemini usage_metadata
                            ai_total = (event.data or {}).get('total', 0)
                            if ai_total:
                                tokens_used = ai_total

            full_text = _clean_response_text(full_text, citations)
            if is_legal_query(query) or is_procedure_query(query):
                full_text = _normalize_legal_answer_structure(full_text)

            if response_mode == AnswerMode.RAG and full_text and not is_greeting:
                full_text = _ensure_rag_source_lead(full_text, citations)

            # Token chính xác: dùng Gemini usage_metadata nếu có, fallback ước lượng
            rag_total_tokens = (rag_usage or {}).get('total', 0) if 'rag_usage' in dir() else 0
            tokens_used = rag_total_tokens if rag_total_tokens else max(0, len(full_text) // 4)
            latency_ms = int((time.time() - start_ms) * 1000)

            used_doc_sources = any(c.source_type == "document" for c in citations)
            used_web_sources = any(c.source_type == "web" for c in citations)
            if used_doc_sources and used_web_sources:
                answer_mode_value = "ai_rag"
            elif used_doc_sources:
                answer_mode_value = "rag"
            else:
                answer_mode_value = response_mode.value

            msg = Message(
                conversation_id=conversation.id,
                role="assistant",
                content=full_text,
                answer_mode=answer_mode_value,
                citations=[asdict(c) for c in citations],
                tokens_used=tokens_used,
                latency_ms=latency_ms,
            )
            conversation.updated_at = datetime.utcnow()
            db.add(msg)
            await db.flush()

            from app.utils.data_crypto import mask_pii as _mask_pii
            usage = UsageLog(
                conversation_id=conversation.id,
                message_id=msg.id,
                query_text=_mask_pii(query[:500]),
                answer_mode=answer_mode_value,
                tokens_used=tokens_used,
                latency_ms=latency_ms,
                is_rag=used_doc_sources,
                retrieved_chunks=len(decision.chunks),
            )
            db.add(usage)
            await db.commit()

            # ── Cập nhật session summary (async, non-blocking) ───────────────
            if session_key and full_text and not is_greeting:
                history_pairs = [
                    (m.content, "")
                    for m in history[-SUMMARY_EVERY_N_TURNS * 2:]
                    if m.role == "user"
                ]
                asyncio.create_task(
                    maybe_update_summary(
                        session_key=session_key,
                        query=query,
                        answer=full_text,
                        history_pairs=history_pairs,
                        gemini_model=self._make_model(),
                    )
                )

            # ── Legal enrichment: trích link DVC + căn cứ pháp lý ──────────
            if not is_greeting and response_mode in (AnswerMode.RAG, AnswerMode.AI):
                try:
                    from app.chat.legal_enricher import legal_enricher as _enricher
                    from dataclasses import asdict as _asdict

                    rag_context_text = (
                        "\n\n".join(c.content for c in (decision.chunks or []))
                    )

                    if response_mode == AnswerMode.RAG:
                        service_links_data = _enricher.extract_service_links_sync(
                            full_text + "\n\n" + rag_context_text
                        )
                        if service_links_data:
                            for _link in service_links_data:
                                _url = (_link.get('url') or '').strip()
                                if _url:
                                    citations = _merge_citations(citations, [Citation(
                                        document_name=_link.get('title') or _link.get('label') or 'Đường link dịch vụ công',
                                        content='Đường link thao tác/hồ sơ được trích từ tài liệu hoặc nguồn kiểm chứng.',
                                        score=0.95,
                                        segment_id=_url,
                                        url=_url,
                                        source_type='web',
                                        domain='dichvucong.gov.vn' if 'dichvucong.gov.vn' in _url else None,
                                    )])
                            yield _sse(StreamEvent("service_links", service_links_data))
                    else:
                        service_links_data = _enricher.extract_service_links_sync(full_text)
                        if service_links_data:
                            for _link in service_links_data:
                                _url = (_link.get('url') or '').strip()
                                if _url:
                                    citations = _merge_citations(citations, [Citation(
                                        document_name=_link.get('title') or _link.get('label') or 'Đường link dịch vụ công',
                                        content='Đường link thao tác/hồ sơ được trích từ tài liệu hoặc nguồn kiểm chứng.',
                                        score=0.95,
                                        segment_id=_url,
                                        url=_url,
                                        source_type='web',
                                        domain='dichvucong.gov.vn' if 'dichvucong.gov.vn' in _url else None,
                                    )])
                            yield _sse(StreamEvent("service_links", service_links_data))

                        enrich = await _enricher.enrich(
                            response_text=full_text,
                            context_chunks_text="",
                        )
                        if enrich.legal_refs:
                            yield _sse(StreamEvent("legal_refs", [_asdict(r) for r in enrich.legal_refs]))
                        if enrich.source_refs:
                            yield _sse(StreamEvent("source_refs", [_asdict(r) for r in enrich.source_refs]))
                except Exception as _enrich_err:
                    logger.warning("Enrichment thất bại (bỏ qua): %s", _enrich_err)

            if citations and not is_greeting and not assessment.get("should_refuse_precise"):
                yield _sse(StreamEvent("citations", [asdict(c) for c in citations]))

            # ── Done event gửi SAU enrichment nhưng latency_ms đo TRƯỚC enrichment ──
            # Điều này đảm bảo latency_ms = thời gian thực tế client chờ nhận done
            latency_ms = int((time.time() - start_ms) * 1000)
            # Cập nhật lại latency trong DB cho chính xác
            try:
                msg.latency_ms = latency_ms
                usage.latency_ms = latency_ms
                await db.commit()
            except Exception:
                pass

            yield _sse(
                StreamEvent(
                    "done",
                    {"tokens": tokens_used, "latency_ms": latency_ms},
                )
            )

        except Exception as e:
            logger.exception("Chat engine error: %s", e)
            yield _sse(StreamEvent("error", str(e)))

    async def _generate_rag_answer(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        history: list[Message],
        session_summary: Optional[str] = None,
    ) -> tuple[str, list[Citation]]:
        context, citations = _build_rag_context(chunks)

        # Inject session summary nếu có
        summary_section = ""
        if session_summary:
            summary_section = (
                f"\n\n## Tóm tắt ngữ cảnh phiên hội thoại\n{session_summary}\n"
                "(Dùng ngữ cảnh trên để hiểu câu hỏi follow-up, không cần lặp lại.)\n"
            )

        full_prompt = _build_rag_prompt(
            context=context,
            query=query,
            domain_instructions=_domain_instructions(query, rag=True),
        ) + summary_section

        gemini_history = _build_history(history)

        def chat_factory():
            return self._make_model().start_chat(history=gemini_history)

        parts: list[str] = []
        gemini_usage: dict = {}
        async for text in _gemini_stream_in_thread(chat_factory, full_prompt):
            if text and text.startswith("__usage__:"):
                try:
                    gemini_usage = json.loads(text[len("__usage__:"):])
                except Exception:
                    pass
            elif text:
                parts.append(text)

        full_text = "".join(parts).strip()
        if not full_text:
            full_text = "Tôi chưa nhận được nội dung phản hồi hợp lệ từ mô hình. Bạn vui lòng thử lại giúp tôi."

        return full_text, citations, gemini_usage

    async def _stream_ai(
        self,
        query: str,
        history: list[Message],
        support_chunks: Optional[list[RetrievedChunk]] = None,
        image_part: Optional[Any] = None,
        force_web: bool = False,
        emit_support_citations: bool = True,
    ) -> AsyncGenerator[StreamEvent, None]:
        system_prompt = _build_ai_prompt(domain_instructions=_domain_instructions(query, rag=False))

        support_chunks = support_chunks or []

        # Khởi động web fetch sớm (song song) nếu cần, để không phải đợi tuần tự
        web_fetch_task: asyncio.Task | None = None
        if force_web or should_search_web(query):
            web_fetch_task = asyncio.create_task(maybe_fetch_web_context(query, force=force_web))

        if support_chunks:
            rag_context, rag_citations = _build_rag_context(support_chunks)
            system_prompt += (
                "\n\n## Ngữ cảnh hỗ trợ từ dataset nội bộ\n"
                f"{rag_context}\n\n"
                "## Quy tắc dùng dataset nội bộ\n"
                "- Dùng ngữ cảnh nội bộ ở trên như lớp kiểm chứng bổ sung, đặc biệt khi câu hỏi nối tiếp ngữ cảnh trước đó.\n"
                "- Nếu dữ liệu web mới hơn và làm thay đổi thông tin nội bộ cũ, phải ưu tiên nguồn mới hơn nhưng nêu rõ phần thay đổi.\n"
                "- Chỉ dùng dữ liệu nội bộ khi nội dung thật sự khớp; nếu chỉ khớp một phần thì phải nói rõ giới hạn.\n"
            )
            if emit_support_citations:
                yield StreamEvent("citations", rag_citations)

        # Await kết quả web fetch (đã chạy song song trong lúc build RAG context)
        if web_fetch_task is not None:
            try:
                web_context, web_citations = await web_fetch_task
            except Exception as exc:
                logger.warning("Web fetch task failed: %s", exc)
                web_context, web_citations = "", []
        else:
            web_context, web_citations = "", []

        logger.info("force_web = %s", force_web)
        logger.info("web_context_found = %s", bool(web_context))
        logger.info("web_citations_count = %s", len(web_citations))
        logger.info("support_chunks_count = %s", len(support_chunks or []))

        if force_web and not web_context and not support_chunks:
            message = (
                "Tôi đã thử tra cứu trên các nguồn web phù hợp nhưng hiện chưa lấy được kết quả đủ tin cậy để trả lời chắc chắn.\n\n"
                "Bạn có thể nêu rõ hơn tên thủ tục, cơ quan thực hiện hoặc địa phương liên quan để tôi tra cứu lại chính xác hơn."
            )
            async for event in _stream_static_text(message):
                yield event
            return
        
        if web_context:
            system_prompt += (
                "\n\n## Dữ liệu web kiểm chứng cập nhật\n"
                f"{web_context}\n\n"
                "## Quy tắc dùng dữ liệu web\n"
                "- Chỉ dùng dữ liệu web ở trên khi truy vấn cần kiểm tra tính cập nhật, hiệu lực, sửa đổi, thay thế của văn bản pháp lý/hành chính.\n"
                "- Chỉ coi các URL trong ngữ cảnh web ở trên là nguồn tham khảo có thể hiển thị; không được tự bịa thêm link khác.\n"
                "- Nếu dữ liệu web chưa đủ mới hoặc chưa chắc chắn, nói rõ giới hạn thay vì khẳng định chắc chắn.\n"
                "- Với câu hỏi pháp lý hoặc lệ phí, phải đối chiếu đúng tên thủ tục trong câu hỏi trước khi kết luận mức tiền.\n"
                "- **TUYỆT ĐỐI KHÔNG** dùng `(trang X)` hay `([N], trang X)` cho nội dung từ web — định dạng đó chỉ dùng cho tài liệu nội bộ có số trang thực.\n"
                "- Khi trích dẫn thông tin từ nguồn web, cuối câu dùng `([N])` trong đó N là số thứ tự nguồn web (bắt đầu từ 1 nếu không có tài liệu nội bộ, hoặc từ số tiếp theo sau tài liệu nội bộ). Ví dụ: câu trích từ nguồn web thứ nhất → `([1])`, nguồn web thứ hai → `([2])`. Các số này khớp với thứ tự trong panel Tham khảo thêm và có thể click được.\n"
                "- Không dùng `(nguồn)` — dùng `([N])` thay thế.\n"
            )
            yield StreamEvent("citations", [_coerce_citation(c) for c in web_citations])

        elif force_web:
            system_prompt += (
                "\n\n## Lưu ý về dữ liệu hiện tại\n"
                "- Đây là truy vấn cần kiểm tra nguồn web cập nhật, nhưng hiện chưa lấy được đủ nguồn phù hợp.\n"
                "- Nếu chưa đủ căn cứ mới, hãy nói rõ giới hạn này thay vì suy đoán.\n"
            )

        gemini_history = _build_history(history)
        if gemini_history and gemini_history[0]["role"] == "user":
            gemini_history[0]["parts"][0] = system_prompt + "\n\n" + gemini_history[0]["parts"][0]

        if image_part is not None:
            prompt_parts: Any = [
                system_prompt,
                query or "Hãy phân tích hình ảnh này và trả lời bằng tiếng Việt.",
                image_part,
            ]
        elif not gemini_history:
            prompt_parts = system_prompt + "\n\n" + query
        else:
            prompt_parts = query

        def chat_factory():
            return self._make_model().start_chat(history=gemini_history)

        emitted = False
        async for text in _gemini_stream_in_thread(chat_factory, prompt_parts):
            if text and text.startswith("__usage__:"):
                # Phát usage_metadata như StreamEvent đặc biệt để caller cập nhật token
                try:
                    usage = json.loads(text[len("__usage__:"):])
                    yield StreamEvent("usage", usage)
                except Exception:
                    pass
            else:
                emitted = True
                yield StreamEvent("token", text)

        if not emitted:
            yield StreamEvent(
                "token",
                "Tôi chưa nhận được nội dung phản hồi hợp lệ từ mô hình. Bạn vui lòng thử lại giúp tôi.",
            )

class _AssessmentAdapter:
    def __init__(self, payload: dict[str, Any]):
        self.__dict__.update(payload or {})


chat_engine = ChatEngine()