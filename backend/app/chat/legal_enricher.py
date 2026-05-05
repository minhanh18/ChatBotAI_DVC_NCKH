"""
LegalEnricher — bổ sung 3 cơ chế sau khi Gemini sinh xong câu trả lời:

1. Trích xuất căn cứ pháp lý → link thuvienphapluat.vn + vbpl.vn (nguồn chính thống)
2. Kiểm tra hiệu lực (Gemini phân tích)
3. Trích xuất link dịch vụ công dichvucong.gov.vn từ context RAG hoặc response
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field, asdict
from typing import Optional
from urllib.parse import quote

import google.generativeai as genai

from app.config import settings

logger = logging.getLogger(__name__)

# ── Patterns nhận dạng văn bản pháp luật Việt Nam ────────────────────────────

_LEGAL_PATTERNS: list[tuple[str, str]] = [
    # (pattern, loại văn bản)
    (r"Luật\s+(?:[\w\s]+?\s+)?(?:số\s+)?(\d+/\d{4}/QH\d+)", "Luật"),
    (r"Luật\s+([\w\s]{3,40}?)(?=\s+\d{4}|\s+số|\s*[\.,;]|\s*\(|$)", "Luật"),
    (r"Nghị\s+định\s+(?:số\s+)?(\d+/\d{4}/NĐ-CP)", "Nghị định"),
    (r"Thông\s+tư\s+(?:số\s+)?(\d+/\d{4}/TT-\w+)", "Thông tư"),
    (r"Quyết\s+định\s+(?:số\s+)?(\d+/\d{4}/QĐ-\w+)", "Quyết định"),
    (r"Chỉ\s+thị\s+(?:số\s+)?(\d+/CT-\w+)", "Chỉ thị"),
    (r"Pháp\s+lệnh\s+([\w\s]{3,40}?)(?=\s+\d{4}|\s+số|\s*[\.,;]|$)", "Pháp lệnh"),
    (r"Thông\s+tư\s+liên\s+tịch\s+(?:số\s+)?(\d+/\d{4}/TTLT-\w+)", "TTLT"),
]

_SERVICE_URL_RE = re.compile(
    r"https://dichvucong\.gov\.vn/[^\s\"'<>\)\]]+", re.IGNORECASE
)

# Regex extract full reference string cho mỗi loại
_FULL_REF_PATTERNS = [
    re.compile(r"Luật\s+[\w\s]+?(?:số\s+\d+/\d{4}/QH\d+)?(?=\s*[\.,;()]|\s+(?:quy|về|của|ngày)|$)", re.IGNORECASE),
    re.compile(r"Nghị\s+định\s+(?:số\s+)?\d+/\d{4}/NĐ-CP(?:\s+[\w\s]+?)?(?=\s*[\.,;(]|$)", re.IGNORECASE),
    re.compile(r"Thông\s+tư\s+(?:liên\s+tịch\s+)?(?:số\s+)?\d+/\d{4}/(?:TT|TTLT)-\w+(?:\s+[\w\s]+?)?(?=\s*[\.,;(]|$)", re.IGNORECASE),
    re.compile(r"Quyết\s+định\s+(?:số\s+)?\d+/\d{4}/QĐ-\w+(?:\s+[\w\s]+?)?(?=\s*[\.,;(]|$)", re.IGNORECASE),
    re.compile(r"Chỉ\s+thị\s+(?:số\s+)?\d+/CT-\w+(?:\s+[\w\s]+?)?(?=\s*[\.,;(]|$)", re.IGNORECASE),
]


@dataclass
class LegalRef:
    reference: str
    doc_type: str
    search_url_tvpl: str
    search_url_vbpl: str
    valid: Optional[bool]
    validity_note: str


@dataclass
class ServiceLink:
    title: str
    url: str


@dataclass
class SourceRef:
    """Nguồn tham khảo — nơi Gemini / tài liệu lấy thông tin."""
    label: str    # Tên nguồn: "Cổng TTĐT Bộ Công an", "Thư viện Pháp luật"...
    url: str      # URL trực tiếp hoặc search URL


@dataclass
class EnrichResult:
    legal_refs: list[LegalRef] = field(default_factory=list)
    service_links: list[ServiceLink] = field(default_factory=list)
    source_refs: list[SourceRef] = field(default_factory=list)


# ── Main enricher ─────────────────────────────────────────────────────────────

class LegalEnricher:
    def __init__(self):
        self._model = genai.GenerativeModel(
            settings.GEMINI_MODEL,
            generation_config=genai.GenerationConfig(temperature=0, max_output_tokens=1024),
        )

    async def enrich(
        self,
        response_text: str,
        context_chunks_text: str = "",
    ) -> EnrichResult:
        """Nhận response đã sinh + context RAG, trả về legal refs + service links + source refs."""
        combined = response_text + "\n\n" + context_chunks_text
        raw_refs = self._extract_references(combined)
        service_links = self._extract_service_links_raw(combined)

        if not raw_refs:
            source_refs = self._build_source_refs([], response_text)
            return EnrichResult(legal_refs=[], service_links=service_links, source_refs=source_refs)

        legal_refs = await self._check_validity_batch(raw_refs)
        source_refs = self._build_source_refs(legal_refs, response_text)
        return EnrichResult(legal_refs=legal_refs, service_links=service_links, source_refs=source_refs)

    def _build_source_refs(self, legal_refs: list, response_text: str) -> list[SourceRef]:
        """Tạo danh sách nguồn tham khảo từ các văn bản pháp lý và nội dung phản hồi."""
        refs: list[SourceRef] = []
        seen_labels: set[str] = set()

        def _add(label: str, url: str):
            if label not in seen_labels:
                seen_labels.add(label)
                refs.append(SourceRef(label=label, url=url))

        # Luôn có: CSDL quốc gia
        _add("Cơ sở dữ liệu quốc gia VBPL", "https://vbpl.vn")
        _add("Thư viện Pháp luật", "https://thuvienphapluat.vn")

        # Theo cơ quan ban hành từ văn bản pháp lý
        agency_map = {
            "TT-BCA": ("Cổng TTĐT Bộ Công an", "https://bocongan.gov.vn"),
            "NĐ-CP": ("Cổng TTĐT Chính phủ", "https://chinhphu.vn"),
            "TT-BTC": ("Cổng TTĐT Bộ Tài chính", "https://mof.gov.vn"),
            "TT-BTNMT": ("Cổng TTĐT Bộ TN&MT", "https://monre.gov.vn"),
            "TT-BYT": ("Cổng TTĐT Bộ Y tế", "https://moh.gov.vn"),
            "TT-BGD": ("Cổng TTĐT Bộ GD&ĐT", "https://moet.gov.vn"),
            "QH": ("Cổng TTĐT Quốc hội", "https://quochoi.vn"),
        }
        for ref in legal_refs:
            for key, (label, url) in agency_map.items():
                if key in ref.reference:
                    _add(label, url)
                    break

        # Nếu đề cập đến thuế → thêm cổng ngành thuế
        if any(kw in response_text.lower() for kw in ["thuế", "gtgt", "tncn", "kê khai", "nộp thuế"]):
            _add("Cổng dịch vụ Thuế điện tử (eTax)", "https://thuedientu.gdt.gov.vn")
            _add("Hỗ trợ người nộp thuế", "https://hotronnt.gdt.gov.vn")

        # Nếu đề cập dịch vụ công VÀ có từ khóa thủ tục cụ thể (không chỉ đề cập chung)
        if ("dichvucong" in response_text or "dịch vụ công" in response_text.lower()) and any(
            kw in response_text.lower() for kw in ["thủ tục", "đăng ký", "nộp hồ sơ", "cấp giấy", "trực tuyến", "nộp trực tuyến"]
        ):
            _add("Cổng Dịch vụ công Quốc gia", "https://dichvucong.gov.vn")

        return refs[:6]  # Tối đa 6 nguồn

    def extract_service_links_sync(self, text: str) -> list[dict]:
        """Trích link dịch vụ công — chỉ regex, không gọi API, trả về list[dict]."""
        from dataclasses import asdict
        return [asdict(s) for s in self._extract_service_links_raw(text)]

    # ── Extract references ─────────────────────────────────────────────────────

    def _extract_references(self, text: str) -> list[tuple[str, str]]:
        """Trả về list (full_reference_string, doc_type), loại trùng lặp."""
        found: dict[str, str] = {}  # reference -> doc_type

        type_map = {
            "nghị định": "Nghị định",
            "thông tư liên tịch": "Thông tư liên tịch",
            "thông tư": "Thông tư",
            "quyết định": "Quyết định",
            "chỉ thị": "Chỉ thị",
            "pháp lệnh": "Pháp lệnh",
            "luật": "Luật",
        }

        for pattern in _FULL_REF_PATTERNS:
            for match in pattern.finditer(text):
                ref = match.group(0).strip().rstrip(".,;()")
                ref = re.sub(r"\s+", " ", ref).strip()
                if len(ref) < 8:
                    continue

                # Xác định doc_type
                ref_lower = ref.lower()
                doc_type = "Văn bản pháp luật"
                for key, val in type_map.items():
                    if ref_lower.startswith(key):
                        doc_type = val
                        break

                # Deduplicate: dùng 30 ký tự đầu làm key
                key = ref[:35].lower()
                if key not in found:
                    found[key] = (ref, doc_type)

        return list(found.values())[:10]  # tối đa 10 refs

    # ── Extract service links ──────────────────────────────────────────────────

    def _extract_service_links_raw(self, text: str) -> list[ServiceLink]:
        """Tìm tất cả URL dichvucong.gov.vn, gắn tên thủ tục nếu có."""
        links: list[ServiceLink] = []
        seen: set[str] = set()

        for m in _SERVICE_URL_RE.finditer(text):
            url = m.group(0).rstrip(".,;)")
            if url in seen:
                continue
            seen.add(url)

            # Cố tìm tiêu đề trong văn bản xung quanh (50 ký tự trước URL)
            start = max(0, m.start() - 120)
            before = text[start:m.start()]
            title = self._guess_service_title(before, url)
            links.append(ServiceLink(title=title, url=url))

        return links

    def _guess_service_title(self, before_text: str, url: str) -> str:
        """Đoán tên thủ tục từ text trước URL hoặc query param."""
        # Thử lấy từ "Bước 1: Truy cập ... thủ tục X"
        m = re.search(r"(?:thủ tục|đường dẫn|truy cập)[^.:\n]{0,80}?([A-ZĐÀÁÂÃÈÉÊÌÍÒÓÔÕÙÚƯÝA-Z][\w\s]{5,60})", before_text, re.IGNORECASE)
        if m:
            return m.group(1).strip()

        # Thử lấy từ "Thủ tục: Tên thủ tục" hoặc tiêu đề trước đó
        m = re.search(r"Thủ tục[:\s]+(.{5,80}?)(?:\n|$)", before_text, re.IGNORECASE)
        if m:
            return m.group(1).strip()

        # Lấy từ query param ma_thu_tuc
        m = re.search(r"ma_thu_tuc=([\d.]+)", url)
        if m:
            return f"Thủ tục {m.group(1)} trên Cổng DVCQG"

        return "Thủ tục trực tuyến trên Cổng Dịch vụ công"

    # ── Check validity via Gemini ──────────────────────────────────────────────

    async def _check_validity_batch(
        self, raw_refs: list[tuple[str, str]]
    ) -> list[LegalRef]:
        """Gọi Gemini 1 lần để kiểm tra hiệu lực tất cả văn bản."""
        refs_text = "\n".join(f"- {r}" for r, _ in raw_refs)

        prompt = f"""Bạn là chuyên gia pháp luật Việt Nam. Với mỗi văn bản pháp luật dưới đây, hãy cho biết:
1. Văn bản có còn hiệu lực không (tính đến năm 2025)?
2. Nếu hết hiệu lực / bị thay thế, bị sửa đổi bổ sung thì bởi văn bản nào?

Danh sách văn bản:
{refs_text}

Trả lời ĐÚNG định dạng JSON sau, KHÔNG thêm bất kỳ text nào khác:
{{
  "results": [
    {{
      "reference": "<tên văn bản>",
      "valid": true,
      "note": "Còn hiệu lực"
    }},
    {{
      "reference": "<tên văn bản>",
      "valid": false,
      "note": "Đã bị thay thế bởi Nghị định XX/YYYY/NĐ-CP"
    }}
  ]
}}

Nếu không chắc chắn, đặt valid=null và note="Cần xác minh tại thuvienphapluat.vn"."""

        validity_map: dict[str, tuple[Optional[bool], str]] = {}

        try:
            resp = await asyncio.to_thread(
                self._model.generate_content, prompt
            )
            raw = resp.text.strip()
            # Strip markdown fences nếu có
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            data = json.loads(raw)
            for item in data.get("results", []):
                ref_key = item.get("reference", "").strip()[:35].lower()
                valid = item.get("valid")  # True/False/None
                note = item.get("note", "Cần xác minh")
                validity_map[ref_key] = (valid, note)
        except Exception as e:
            logger.warning("Không kiểm tra được hiệu lực văn bản: %s", e)

        # Build LegalRef objects
        result: list[LegalRef] = []
        for ref, doc_type in raw_refs:
            key = ref[:35].lower()
            valid, note = validity_map.get(key, (None, "Cần xác minh tại thuvienphapluat.vn"))

            result.append(LegalRef(
                reference=ref,
                doc_type=doc_type,
                search_url_tvpl=_build_tvpl_url(ref),
                search_url_vbpl=_build_vbpl_url(ref),
                valid=valid,
                validity_note=note,
            ))

        return result


# ── URL builders ──────────────────────────────────────────────────────────────

def _build_tvpl_url(ref: str) -> str:
    """Tạo search URL trên thuvienphapluat.vn."""
    encoded = quote(ref, safe="")
    return (
        f"https://thuvienphapluat.vn/page/tim-van-ban.aspx"
        f"?keyword={encoded}&area=0&match=False&type=0&status=0&signer=0&sort=1&num=10&offset=0"
    )


def _build_vbpl_url(ref: str) -> str:
    """Tạo search URL trên vbpl.vn (Cơ sở dữ liệu quốc gia văn bản pháp luật)."""
    encoded = quote(ref, safe="")
    return f"https://vbpl.vn/TW/Pages/vbpqtimkiem.aspx?type=0&s={encoded}"


# Singleton
legal_enricher = LegalEnricher()
