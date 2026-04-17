"""
Chunking pipeline — chia tài liệu thành các chunk theo ngưỡng cấu hình.

Cấu hình tại config.py:
  CHUNK_SIZE       — kích thước chunk (ký tự), mặc định 800
  CHUNK_OVERLAP    — độ chồng lấp (ký tự), mặc định 120
  CHUNK_SEPARATORS — danh sách phân cách ưu tiên
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator

from app.config import settings


@dataclass
class Chunk:
    content: str
    position: int
    word_count: int = 0
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        self.word_count = len(self.content.split())


class RecursiveCharacterChunker:
    """
    Chunker đệ quy theo ký tự — tương tự RecursiveCharacterTextSplitter của LangChain
    nhưng không có thêm phụ thuộc. Ưu tiên phân tách tại các ký tự trong
    `separators` trước khi cắt cứng theo `chunk_size`.
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

    # ── Public ────────────────────────────────────────────────────────────────

    def split(self, text: str) -> list[Chunk]:
        """Nhận văn bản thô, trả về danh sách Chunk."""
        text = self._clean(text)
        raw = list(self._split_recursive(text, self.separators))
        merged = self._merge_with_overlap(raw)
        return [
            Chunk(content=c, position=i, meta={"char_start": self._find_offset(text, c, i)})
            for i, c in enumerate(merged)
        ]

    # ── Private ───────────────────────────────────────────────────────────────

    def _clean(self, text: str) -> str:
        # Chuẩn hoá khoảng trắng thừa, giữ xuống dòng
        text = re.sub(r"\r\n", "\n", text)
        text = re.sub(r"\r", "\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r"\n{4,}", "\n\n\n", text)
        return text.strip()

    def _split_recursive(self, text: str, separators: list[str]) -> Iterator[str]:
        """Chia đệ quy — thử từng separator, nếu vẫn quá lớn thì chia tiếp."""
        if len(text) <= self.chunk_size:
            if text.strip():
                yield text
            return

        # Tìm separator đầu tiên xuất hiện trong text
        chosen_sep = ""
        remaining_seps: list[str] = []
        for i, sep in enumerate(separators):
            if sep == "" or sep in text:
                chosen_sep = sep
                remaining_seps = separators[i + 1 :]
                break

        if chosen_sep == "":
            # Không có separator nào khớp → cắt cứng
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
        """Cắt cứng khi không còn separator nào."""
        start = 0
        while start < len(text):
            end = start + self.chunk_size
            yield text[start:end]
            start = end - self.chunk_overlap

    def _merge_with_overlap(self, pieces: list[str]) -> list[str]:
        """
        Gộp các piece nhỏ vào đủ chunk_size, đồng thời thêm overlap
        từ chunk trước.
        """
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
                # Lấy overlap từ cuối current
                overlap_text = current[-self.chunk_overlap :] if self.chunk_overlap else ""
                current = (overlap_text + "\n" + pieces[i]).strip() if overlap_text else pieces[i]

        if current.strip():
            merged.append(current.strip())

        return merged

    def _find_offset(self, full_text: str, chunk: str, position: int) -> int:
        """Tìm vị trí bắt đầu (offset) của chunk trong văn bản gốc."""
        try:
            return full_text.index(chunk[:50])
        except ValueError:
            return position * self.chunk_size
