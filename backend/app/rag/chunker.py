"""
Chunking pipeline — chia tài liệu thành các chunk theo ngưỡng cấu hình.

Chiến lược chunking (theo research 2024-2025):
  ┌─────────────────────────────────────────────────────────────────┐
  │  LegalAwareChunker  (ưu tiên)                                   │
  │    → Phát hiện boundary tại Điều/Khoản/Chương/Mục/Điểm         │
  │    → Giữ toàn bộ điều khoản trong 1 chunk                       │
  │    → Fallback sang RecursiveCharacterChunker nếu chunk quá lớn  │
  ├─────────────────────────────────────────────────────────────────┤
  │  RecursiveCharacterChunker  (fallback + tài liệu thường)        │
  │    → Chunk theo ký tự với overlap                                │
  └─────────────────────────────────────────────────────────────────┘

Cấu hình tại config.py:
  CHUNK_SIZE       — kích thước chunk (ký tự), mặc định 800
  CHUNK_OVERLAP    — độ chồng lấp (ký tự), mặc định 120
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator

from app.config import settings
from app.rag.legal_metadata import extract_chunk_legal_metadata

_PAGE_MARKER_RE = re.compile(r"\[\[PAGE:(\d+)\]\]")

# ── Legal structure patterns cho văn bản pháp lý Việt Nam ─────────────────────
# Thứ tự ưu tiên phân tách: Chương > Mục > Điều > Khoản > Điểm
_LEGAL_BOUNDARY_PATTERNS = [
    # Chương (cấp cao nhất)
    re.compile(r"(?m)^(?:CHƯƠNG|Chương)\s+[IVXLC\d]+[\.\s]"),
    # Mục
    re.compile(r"(?m)^(?:MỤC|Mục)\s+\d+[\.\s]"),
    # Điều (quan trọng nhất — thường là đơn vị ngữ nghĩa độc lập)
    re.compile(r"(?m)^(?:Điều|ĐIỀU)\s+\d+[\.:]"),
    # Khoản đánh số
    re.compile(r"(?m)^\d+\.\s+[A-ZĐÀÁÂÃÈÉÊÌÍÒÓÔÕÙÚĂẮẶẲẰẮẶẺẼẸỊỞỜỌỤỰỬỮỨẦẤẪẢẠẼỌỤỶỸỴ]"),
    # Điểm (a), b), c)...)
    re.compile(r"(?m)^[a-zđ]\)\s+"),
]

_DIEU_RE = re.compile(r"(?m)^(?:Điều|ĐIỀU)\s+\d+", re.IGNORECASE)
_CHUONG_RE = re.compile(r"(?m)^(?:CHƯƠNG|Chương)\s+[IVXLC\d]+", re.IGNORECASE)
_MUC_RE = re.compile(r"(?m)^(?:MỤC|Mục)\s+\d+", re.IGNORECASE)


@dataclass
class Chunk:
    content: str
    position: int
    word_count: int = 0
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        self.word_count = len(self.content.split())


def _is_legal_document(text: str) -> bool:
    """Heuristic: phát hiện văn bản pháp lý Việt Nam."""
    sample = text[:3000]
    markers = [
        bool(_DIEU_RE.search(sample)),
        bool(_CHUONG_RE.search(sample)),
        bool(_MUC_RE.search(sample)),
        "Khoản" in sample or "khoản" in sample,
        "Nghị định" in sample or "Thông tư" in sample or "Luật" in sample or "Quyết định" in sample,
    ]
    return sum(markers) >= 2


def _find_legal_boundaries(text: str) -> list[int]:
    """
    Tìm vị trí các boundary pháp lý trong văn bản.
    Trả về list offset tăng dần.
    """
    positions: set[int] = {0}
    for pattern in _LEGAL_BOUNDARY_PATTERNS:
        for m in pattern.finditer(text):
            # Boundary tại đầu dòng — đi lùi để lấy đúng ký tự \n trước
            start = m.start()
            if start > 0:
                positions.add(start)
    return sorted(positions)


def _page_numbers_in(text: str) -> list[int]:
    return [int(v) for v in _PAGE_MARKER_RE.findall(text)]


def _make_meta(text: str, full_text: str, position: int) -> dict:
    """Tạo metadata cho một chunk."""
    meta: dict = {}
    pages = _page_numbers_in(text)
    if pages:
        unique = sorted(set(pages))
        meta["page_numbers"] = unique
        meta["page_start"] = unique[0]
        meta["page_end"] = unique[-1]
        meta["location_label"] = (
            f"trang {unique[0]}" if len(unique) == 1 else f"trang {unique[0]}-{unique[-1]}"
        )
    try:
        meta["char_start"] = full_text.index(text[:40])
    except ValueError:
        meta["char_start"] = position * settings.CHUNK_SIZE
    meta.update(extract_chunk_legal_metadata(text))
    return meta


# ── LegalAwareChunker ─────────────────────────────────────────────────────────

class LegalAwareChunker:
    """
    Chunker nhận biết cấu trúc văn bản pháp lý Việt Nam.

    Thuật toán:
      1. Nếu không phải văn bản pháp lý → dùng RecursiveCharacterChunker.
      2. Tìm tất cả boundary (Điều/Khoản/Chương...).
      3. Gộp các đoạn nhỏ để đạt target_size (tránh chunk quá nhỏ).
      4. Nếu đoạn vẫn quá lớn → cắt cứng có overlap.
      5. Prepend context header (tên Điều/Chương) vào mỗi chunk để chunk
         có thể tự giải thích khi retrieval.
    """

    def __init__(
        self,
        chunk_size: int = settings.CHUNK_SIZE,
        chunk_overlap: int = settings.CHUNK_OVERLAP,
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self._fallback = RecursiveCharacterChunker(chunk_size, chunk_overlap)

    def split(self, text: str) -> list[Chunk]:
        cleaned = self._clean(text)

        if not _is_legal_document(cleaned):
            return self._fallback.split(cleaned)

        boundaries = _find_legal_boundaries(cleaned)

        # Trích các đoạn theo boundary
        raw_segments: list[str] = []
        for i, start in enumerate(boundaries):
            end = boundaries[i + 1] if i + 1 < len(boundaries) else len(cleaned)
            seg = cleaned[start:end].strip()
            if seg:
                raw_segments.append(seg)

        if not raw_segments:
            return self._fallback.split(cleaned)

        # Gộp segments nhỏ, cắt segments quá lớn
        merged = self._merge_segments(raw_segments)

        chunks: list[Chunk] = []
        for i, seg in enumerate(merged):
            content = _PAGE_MARKER_RE.sub("", seg).strip()
            if not content:
                continue
            # Prepend context nếu chunk bắt đầu bằng Khoản/Điểm (không tự chứa ngữ cảnh đủ)
            content = self._add_context_header(content, i, merged)
            meta = _make_meta(seg, cleaned, i)
            chunks.append(Chunk(content=content, position=i, meta=meta))

        return chunks if chunks else self._fallback.split(cleaned)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _clean(self, text: str) -> str:
        text = re.sub(r"\r\n", "\n", text)
        text = re.sub(r"\r", "\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r"\n{4,}", "\n\n\n", text)
        return text.strip()

    def _merge_segments(self, segments: list[str]) -> list[str]:
        """
        Gộp segments nhỏ hơn MIN_SIZE vào segment kế tiếp.
        Cắt segments lớn hơn MAX_SIZE thành nhiều phần có overlap.
        """
        MIN_SIZE = self.chunk_size // 3   # ~267 chars: quá nhỏ
        MAX_SIZE = self.chunk_size * 2    # ~1600 chars: quá lớn

        merged: list[str] = []
        buf = ""

        for seg in segments:
            if not buf:
                buf = seg
            elif len(buf) + len(seg) + 2 <= self.chunk_size:
                buf = buf + "\n\n" + seg
            else:
                merged.append(buf)
                buf = seg

        if buf:
            merged.append(buf)

        # Cắt những segment vẫn quá lớn
        result: list[str] = []
        for seg in merged:
            if len(seg) <= MAX_SIZE:
                result.append(seg)
            else:
                result.extend(self._hard_split_with_overlap(seg))

        return result

    def _hard_split_with_overlap(self, text: str) -> list[str]:
        parts: list[str] = []
        start = 0
        while start < len(text):
            end = min(start + self.chunk_size, len(text))
            parts.append(text[start:end])
            if end >= len(text):
                break
            start = end - self.chunk_overlap
        return parts

    def _add_context_header(self, content: str, idx: int, all_segments: list[str]) -> str:
        """
        Nếu chunk bắt đầu bằng Khoản/Điểm (không có tên Điều),
        trích tên Điều từ segment trước và prepend.
        """
        has_dieu = bool(_DIEU_RE.match(content.lstrip()))
        if has_dieu:
            return content

        # Tìm tên Điều gần nhất trong các segment trước
        for prev_idx in range(idx - 1, -1, -1):
            m = _DIEU_RE.search(all_segments[prev_idx])
            if m:
                dieu_line = all_segments[prev_idx][m.start():].split("\n")[0].strip()
                if dieu_line and not content.startswith(dieu_line):
                    return f"[{dieu_line}]\n{content}"
                break
        return content


# ── RecursiveCharacterChunker (giữ nguyên, dùng làm fallback) ─────────────────

class RecursiveCharacterChunker:
    """
    Chunker đệ quy theo ký tự — tương tự RecursiveCharacterTextSplitter của LangChain.
    Ưu tiên phân tách tại các ký tự trong `separators` trước khi cắt cứng.
    """

    def __init__(
        self,
        chunk_size: int = settings.CHUNK_SIZE,
        chunk_overlap: int = settings.CHUNK_OVERLAP,
        separators: list[str] | None = None,
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = separators or settings.CHUNK_SEPARATORS

    def split(self, text: str) -> list[Chunk]:
        text = self._clean(text)
        raw = list(self._split_recursive(text, self.separators))
        merged = self._merge_with_overlap(raw)

        chunks: list[Chunk] = []
        for i, raw_chunk in enumerate(merged):
            page_numbers = [int(value) for value in _PAGE_MARKER_RE.findall(raw_chunk)]
            cleaned_chunk = _PAGE_MARKER_RE.sub("", raw_chunk)
            cleaned_chunk = re.sub(r"\n{3,}", "\n\n", cleaned_chunk).strip()
            if not cleaned_chunk:
                continue

            meta = {"char_start": self._find_offset(text, raw_chunk, i)}
            if page_numbers:
                unique_pages = sorted(set(page_numbers))
                meta["page_numbers"] = unique_pages
                meta["page_start"] = unique_pages[0]
                meta["page_end"] = unique_pages[-1]
                meta["location_label"] = (
                    f"trang {unique_pages[0]}"
                    if len(unique_pages) == 1
                    else f"trang {unique_pages[0]}-{unique_pages[-1]}"
                )
            meta.update(extract_chunk_legal_metadata(cleaned_chunk))
            chunks.append(Chunk(content=cleaned_chunk, position=i, meta=meta))
        return chunks

    def _clean(self, text: str) -> str:
        text = re.sub(r"\r\n", "\n", text)
        text = re.sub(r"\r", "\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r"\n{4,}", "\n\n\n", text)
        return text.strip()

    def _split_recursive(self, text: str, separators: list[str]) -> Iterator[str]:
        if len(text) <= self.chunk_size:
            if text.strip():
                yield text
            return

        chosen_sep = ""
        remaining_seps: list[str] = []
        for i, sep in enumerate(separators):
            if sep == "" or sep in text:
                chosen_sep = sep
                remaining_seps = separators[i + 1:]
                break

        if chosen_sep == "":
            for chunk in self._hard_split(text):
                yield chunk
            return

        parts = text.split(chosen_sep) if chosen_sep else list(text)
        buf = ""
        for part in parts:
            piece = (buf + chosen_sep + part).lstrip(chosen_sep) if buf else part
            if len(piece) <= self.chunk_size:
                buf = piece
            else:
                if buf.strip():
                    if len(buf) > self.chunk_size and remaining_seps:
                        yield from self._split_recursive(buf, remaining_seps)
                    else:
                        yield buf
                buf = part

        if buf.strip():
            if len(buf) > self.chunk_size and remaining_seps:
                yield from self._split_recursive(buf, remaining_seps)
            else:
                yield buf

    def _hard_split(self, text: str) -> Iterator[str]:
        start = 0
        while start < len(text):
            end = start + self.chunk_size
            yield text[start:end]
            start = end - self.chunk_overlap

    def _merge_with_overlap(self, pieces: list[str]) -> list[str]:
        if not pieces:
            return []
        merged: list[str] = []
        current = pieces[0]
        for i in range(1, len(pieces)):
            candidate = current + "\n\n" + pieces[i]
            if len(candidate) <= self.chunk_size:
                current = candidate
            else:
                merged.append(current.strip())
                overlap_text = current[-self.chunk_overlap:] if self.chunk_overlap else ""
                current = (overlap_text + "\n" + pieces[i]).strip() if overlap_text else pieces[i]
        if current.strip():
            merged.append(current.strip())
        return merged

    def _find_offset(self, full_text: str, chunk: str, position: int) -> int:
        try:
            return full_text.index(chunk[:50])
        except ValueError:
            return position * self.chunk_size


# ── Public interface ──────────────────────────────────────────────────────────

def get_chunker(text: str = "") -> LegalAwareChunker | RecursiveCharacterChunker:
    """Factory: trả về chunker phù hợp."""
    return LegalAwareChunker()
