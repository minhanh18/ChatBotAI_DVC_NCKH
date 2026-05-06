"""
HybridRetriever v3 — RAG-first friendly retrieval.

Mục tiêu bản v3:
- Không để RAG bị bỏ qua chỉ vì vector score thấp.
- Kết hợp candidate từ vector search + lexical SQL search + BM25.
- Giữ ưu tiên nguồn nội bộ trước khi fallback web.
- Tăng khả năng hiểu truy vấn tiếng Việt, viết tắt và câu hỏi nối tiếp.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.db import Document, DocumentSegment
from app.rag.embedder import embedding_service

logger = logging.getLogger(__name__)


_VI_STOPWORDS = {
    "là", "và", "của", "cho", "với", "trong", "khi", "thì", "này", "kia", "đó", "đây",
    "bạn", "mình", "tôi", "giúp", "hỏi", "được", "không", "có", "về", "theo", "như", "ra",
    "các", "những", "đến", "từ", "một", "cũng", "đã", "sẽ", "vào", "đối", "lại", "tại",
    "nếu", "hoặc", "để", "hay", "mà", "nên", "còn", "quy", "định", "nào", "sao",
    "làm", "thế", "nào", "muốn", "cần", "phải", "ai", "ở", "đâu", "khi", "bao", "nhiêu",
}

_ABBREVIATIONS = {
    "đk": "đăng ký",
    "dk": "đăng ký",
    "đăng kí": "đăng ký",
    "cccd": "căn cước công dân",
    "bhyt": "bảo hiểm y tế",
    "bhxh": "bảo hiểm xã hội",
    "dvctt": "dịch vụ công trực tuyến",
    "hkd": "hộ kinh doanh",
    "tthc": "thủ tục hành chính",
    "dvc": "dịch vụ công",
    "ubnd": "ủy ban nhân dân",
    "cmnd": "chứng minh nhân dân",
    "hkk": "hộ khẩu",
    "gplx": "giấy phép lái xe",
    "ktt": "kết thúc",
    "tthh": "thủ tục hành chính",
    "vp": "văn phòng",
    "tt": "tạm trú",
    "tn": "thường trú",
    "cư trú": "đăng ký cư trú",
}


_DOMAIN_SYNONYMS = {
    "tạm trú": ["đăng ký tạm trú", "gia hạn tạm trú", "tạm trú", "cư trú"],
    "thường trú": ["đăng ký thường trú", "thường trú", "cư trú"],
    "cư trú": ["luật cư trú", "đăng ký cư trú", "cư trú"],
    "căn cước": ["căn cước", "thẻ căn cước", "căn cước công dân", "cccd"],
    "hộ kinh doanh": ["hộ kinh doanh", "đăng ký hộ kinh doanh", "kinh doanh"],
    "khai sinh": ["đăng ký khai sinh", "khai sinh", "hộ tịch"],
    "kết hôn": ["đăng ký kết hôn", "kết hôn", "hộ tịch"],
}


def _normalize_query(text: str) -> str:
    """Chuẩn hóa nhẹ: lowercase + expand viết tắt còn sót.
    Query đã được chuẩn hóa sâu hơn bởi query_rewriter.py (LLM) trước khi vào đây.
    """
    normalized = " ".join((text or "").split()).lower()
    for short, expanded in _ABBREVIATIONS.items():
        normalized = re.sub(rf"(?<!\w){re.escape(short)}(?!\w)", expanded, normalized, flags=re.IGNORECASE)
    return " ".join(normalized.split())


def _tokenize_vi(text: str) -> list[str]:
    text = _normalize_query(text)
    tokens = re.findall(r"[a-zà-ỹđ\d]+", text)
    return [t for t in tokens if len(t) >= 2 and t not in _VI_STOPWORDS]


def _query_terms(query: str) -> list[str]:
    normalized = _normalize_query(query)
    terms: list[str] = []

    for key, synonyms in _DOMAIN_SYNONYMS.items():
        if key in normalized:
            terms.extend(synonyms)

    words = _tokenize_vi(normalized)
    # Cụm 4/3/2 từ để bắt tên thủ tục: "đăng ký tạm trú", "cấp lại thẻ căn cước"...
    for size in (4, 3, 2):
        for i in range(len(words) - size + 1):
            phrase = " ".join(words[i:i + size]).strip()
            if len(phrase) >= 7:
                terms.append(phrase)

    terms.extend(words)

    ordered: list[str] = []
    seen: set[str] = set()
    for term in terms:
        term = " ".join(term.split()).strip().lower()
        if not term or term in seen or term in _VI_STOPWORDS:
            continue
        seen.add(term)
        ordered.append(term)
    return ordered[:18]


_RRF_K = 60


def _rrf_score(rank: int) -> float:
    return 1.0 / (_RRF_K + rank + 1)


def _rrf_fuse(vector_ranking: list[str], bm25_ranking: list[str], lexical_ranking: list[str], alpha: float = 0.50) -> list[tuple[str, float]]:
    """Hợp nhất 3 ranking: vector, BM25 và lexical SQL."""
    all_ids = set(vector_ranking) | set(bm25_ranking) | set(lexical_ranking)
    if not all_ids:
        return []
    vec_rank = {sid: i for i, sid in enumerate(vector_ranking)}
    bm25_rank = {sid: i for i, sid in enumerate(bm25_ranking)}
    lex_rank = {sid: i for i, sid in enumerate(lexical_ranking)}
    scores: dict[str, float] = {}
    for sid in all_ids:
        scores[sid] = (
            alpha * _rrf_score(vec_rank.get(sid, len(vector_ranking)))
            + 0.32 * _rrf_score(bm25_rank.get(sid, len(bm25_ranking)))
            + 0.18 * _rrf_score(lex_rank.get(sid, len(lexical_ranking)))
        )
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


@dataclass
class RetrievedChunk:
    segment_id: str
    document_id: str
    document_name: str
    content: str
    score: float
    position: int
    document_meta: dict | None = None
    segment_meta: dict | None = None


class HybridRetriever:
    def __init__(
        self,
        top_k: int = settings.RETRIEVAL_TOP_K,
        score_threshold: float = settings.RETRIEVAL_SCORE_THRESHOLD,
        fetch_multiplier: int = 10,
    ):
        self.top_k = top_k
        # RAG-first: không dùng ngưỡng vector quá cao để loại mất tài liệu liên quan.
        self.score_threshold = min(float(score_threshold or 0.0), 0.30)
        self.fetch_multiplier = fetch_multiplier

    async def retrieve(self, query: str, db: AsyncSession, dataset_id: Optional[str] = None) -> list[RetrievedChunk]:
        query = _normalize_query(query)
        if not query:
            return []

        candidate_limit = max(self.top_k * self.fetch_multiplier, 40)
        vector_candidates = await self._vector_candidates(query, db, dataset_id, candidate_limit)
        lexical_candidates = await self._lexical_candidates(query, db, dataset_id, candidate_limit)

        candidates_by_id: dict[str, RetrievedChunk] = {}
        vector_scores: dict[str, float] = {}
        lexical_scores: dict[str, float] = {}

        for chunk in vector_candidates:
            candidates_by_id[chunk.segment_id] = chunk
            vector_scores[chunk.segment_id] = chunk.score
        for chunk in lexical_candidates:
            old = candidates_by_id.get(chunk.segment_id)
            if old is None or chunk.score > old.score:
                candidates_by_id[chunk.segment_id] = chunk
            lexical_scores[chunk.segment_id] = max(lexical_scores.get(chunk.segment_id, 0.0), chunk.score)

        candidates = self._dedupe_and_filter(list(candidates_by_id.values()))
        if not candidates:
            return []

        query_tokens = _tokenize_vi(query)
        corpus = [_tokenize_vi(c.content + " " + c.document_name) for c in candidates]
        sid_list = [c.segment_id for c in candidates]

        bm25_scores: dict[str, float] = {}
        try:
            from rank_bm25 import BM25Okapi
            bm25 = BM25Okapi(corpus)
            raw_scores = bm25.get_scores(query_tokens)
            max_bm25 = max(raw_scores) if len(raw_scores) and max(raw_scores) > 0 else 1.0
            for i, sid in enumerate(sid_list):
                bm25_scores[sid] = float(raw_scores[i]) / max_bm25
        except Exception as exc:
            logger.debug("BM25 không khả dụng, dùng vector+lexical only: %s", exc)

        vector_ranking = [c.segment_id for c in sorted(candidates, key=lambda c: vector_scores.get(c.segment_id, 0.0), reverse=True)]
        bm25_ranking = [sid for sid, _ in sorted(bm25_scores.items(), key=lambda x: x[1], reverse=True)]
        lexical_ranking = [sid for sid, _ in sorted(lexical_scores.items(), key=lambda x: x[1], reverse=True)]

        fused = _rrf_fuse(vector_ranking, bm25_ranking, lexical_ranking)
        if not fused:
            fused = [(c.segment_id, c.score) for c in sorted(candidates, key=lambda c: c.score, reverse=True)]

        chunk_by_id = {c.segment_id: c for c in candidates}
        result_chunks: list[RetrievedChunk] = []
        for sid, fused_score in fused:
            chunk = chunk_by_id.get(sid)
            if not chunk:
                continue
            sem = vector_scores.get(sid, 0.0)
            lex = lexical_scores.get(sid, 0.0)
            bm = bm25_scores.get(sid, 0.0)
            # Điểm cuối không chỉ dựa vào vector, để evaluator không loại mất chunk keyword khớp mạnh.
            chunk.score = max(chunk.score, sem * 0.60 + bm * 0.25 + lex * 0.15, fused_score * 8)
            result_chunks.append(chunk)
            if len(result_chunks) >= self.top_k:
                break

        logger.info(
            "HybridRetriever v3: vector=%d lexical=%d merged=%d returned=%d terms=%s",
            len(vector_candidates), len(lexical_candidates), len(candidates), len(result_chunks), _query_terms(query)[:8]
        )
        return result_chunks

    async def _vector_candidates(self, query: str, db: AsyncSession, dataset_id: Optional[str], limit: int) -> list[RetrievedChunk]:
        try:
            query_vector = await embedding_service.embed_query(query)
        except Exception as exc:
            logger.warning("Embed query failed, fallback lexical only: %s", exc)
            return []

        distance_expr = DocumentSegment.embedding.cosine_distance(query_vector)
        stmt = (
            select(
                DocumentSegment.id,
                DocumentSegment.document_id,
                DocumentSegment.content,
                DocumentSegment.position,
                Document.name.label("document_name"),
                Document.meta.label("document_meta"),
                DocumentSegment.meta.label("segment_meta"),
                (1 - distance_expr).label("score"),
            )
            .join(Document, DocumentSegment.document_id == Document.id)
            .where(DocumentSegment.embedding.is_not(None))
            .where(Document.status == "ready")
            .order_by(distance_expr)
            .limit(limit)
        )
        if dataset_id:
            stmt = stmt.where(DocumentSegment.dataset_id == UUID(dataset_id))
        rows = (await db.execute(stmt)).fetchall()
        out: list[RetrievedChunk] = []
        for row in rows:
            score = float(row.score or 0.0)
            # Chỉ loại các vector thật sự xa; để BM25 có cơ hội cứu candidate.
            if score < self.score_threshold:
                continue
            out.append(self._row_to_chunk(row, score))
        return out

    async def _lexical_candidates(self, query: str, db: AsyncSession, dataset_id: Optional[str], limit: int) -> list[RetrievedChunk]:
        terms = _query_terms(query)
        if not terms:
            return []
        # Ưu tiên cụm dài, tránh OR quá rộng.
        sql_terms = [t for t in terms if len(t) >= 3][:12]
        if not sql_terms:
            return []
        conditions = []
        for term in sql_terms:
            like = f"%{term}%"
            conditions.append(DocumentSegment.content.ilike(like))
            conditions.append(Document.name.ilike(like))
        stmt = (
            select(
                DocumentSegment.id,
                DocumentSegment.document_id,
                DocumentSegment.content,
                DocumentSegment.position,
                Document.name.label("document_name"),
                Document.meta.label("document_meta"),
                DocumentSegment.meta.label("segment_meta"),
            )
            .join(Document, DocumentSegment.document_id == Document.id)
            .where(Document.status == "ready")
            .where(or_(*conditions))
            .limit(limit)
        )
        if dataset_id:
            stmt = stmt.where(DocumentSegment.dataset_id == UUID(dataset_id))
        rows = (await db.execute(stmt)).fetchall()
        out: list[RetrievedChunk] = []
        for row in rows:
            text = f"{row.document_name} {row.content}".lower()
            score = self._lexical_score(text, terms)
            if score <= 0:
                continue
            out.append(self._row_to_chunk(row, score))
        return sorted(out, key=lambda c: c.score, reverse=True)[:limit]

    def _lexical_score(self, text: str, terms: list[str]) -> float:
        if not text:
            return 0.0
        score = 0.0
        for term in terms:
            if term in text:
                score += 4.0 if " " in term else 1.0
        if score <= 0:
            return 0.0
        return min(0.98, 0.35 + score / max(12.0, len(terms) * 2.0))

    def _row_to_chunk(self, row, score: float) -> RetrievedChunk:
        return RetrievedChunk(
            segment_id=str(row.id),
            document_id=str(row.document_id),
            document_name=row.document_name,
            content=row.content,
            score=float(score),
            position=row.position,
            document_meta=row.document_meta or {},
            segment_meta=row.segment_meta or {},
        )

    def _dedupe_and_filter(self, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        out: list[RetrievedChunk] = []
        seen_hashes: set[str] = set()
        for chunk in sorted(chunks, key=lambda c: c.score, reverse=True):
            meta = chunk.document_meta or {}
            lifecycle_status = str(meta.get("lifecycle_status") or "active").lower()
            if lifecycle_status in {"deprecated", "archived"}:
                continue
            if meta.get("is_active_for_retrieval") is False:
                continue
            seg_meta = chunk.segment_meta or {}
            content_hash = str(seg_meta.get("content_hash") or "").strip()
            if content_hash and content_hash in seen_hashes:
                continue
            if content_hash:
                seen_hashes.add(content_hash)
            out.append(chunk)
        return out

    def invalidate_cache(self, dataset_id: str | None = None) -> None:
        pass


VectorRetriever = HybridRetriever
retriever = HybridRetriever()
