from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any

from app.rag.retriever import RetrievedChunk

LEGAL_QUERY_PATTERNS = [
    r"\bluật\b", r"\bnghị định\b", r"\bthông tư\b", r"\bquyết định\b",
    r"\bđiều\s*\d+", r"\bkhoản\s*\d+", r"\bđiểm\s+[a-zđ]\b",
    r"\bcư trú\b", r"\btạm trú\b", r"\bthường trú\b", r"\bhộ tịch\b",
    r"\bkhai sinh\b", r"\bđăng ký\b", r"\blệ phí\b", r"\bmức phạt\b",
    r"\bthuế\b", r"\bthủ tục\b", r"\bhành chính\b", r"\bcăn cứ pháp lý\b",
    r"\bbhyt\b", r"\bbhxh\b", r"\bbảo hiểm\b", r"\bbảo hiểm y tế\b", r"\bbảo hiểm xã hội\b",
]

PROCEDURE_PATTERNS = [
    r"\bthủ tục\b", r"\bhồ sơ\b", r"\bcần chuẩn bị\b", r"\bnộp ở đâu\b",
    r"\bbước\s*1\b", r"\btrình tự\b", r"\bcách làm\b", r"\bdịch vụ công\b",
    r"\bđăng ký\b", r"\bxin\b", r"\bcấp\b", r"\bchuyển\b", r"\bđiều chỉnh\b",
]

FRESHNESS_PATTERNS = [
    "mới nhất", "hiện hành", "hiện tại", "còn hiệu lực", "đang có hiệu lực",
    "sửa đổi", "bổ sung", "thay thế", "quy định mới", "luật mới", "nghị định mới", "thông tư mới",
]

GREETING_PATTERNS = [
    r"^\s*(xin\s*chào|chào\s*bạn|chào|hello|hi|hey)\s*[!.?… ]*$",
    r"^\s*(chào\s*ad|alo|a\s*lô)\s*[!.?… ]*$",
]


def is_greeting_query(query: str) -> bool:
    q = (query or "").strip().lower()
    if not q:
        return False
    return any(re.match(pattern, q) for pattern in GREETING_PATTERNS)


@dataclass
class RetrievalAssessment:
    mode: str
    chunk_count: int
    distinct_documents: int
    best_score: float
    avg_score: float
    confidence: str
    should_use_rag: bool
    should_force_web: bool
    should_refuse_precise: bool
    reason: str
    authority_hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def is_legal_query(query: str) -> bool:
    q = (query or "").lower()
    return any(re.search(pattern, q) for pattern in LEGAL_QUERY_PATTERNS)


def is_procedure_query(query: str) -> bool:
    q = (query or "").lower()
    return any(re.search(pattern, q) for pattern in PROCEDURE_PATTERNS)


def needs_freshness_check(query: str) -> bool:
    q = " ".join((query or "").split()).lower()
    return any(token in q for token in FRESHNESS_PATTERNS)


def authority_hint_for_query(query: str) -> str:
    q = (query or "").lower()
    if any(token in q for token in ["tạm trú", "thường trú", "cư trú", "căn cước", "cccd"]):
        return "Công an cấp xã/phường hoặc bộ phận một cửa nơi cư trú"
    if any(token in q for token in ["khai sinh", "kết hôn", "hộ tịch", "chứng thực"]):
        return "UBND cấp xã/phường hoặc bộ phận một cửa nơi cư trú"
    if any(token in q for token in ["thuế", "hoàn thuế"]):
        return "cơ quan thuế quản lý trực tiếp hoặc bộ phận hỗ trợ người nộp thuế"
    if any(token in q for token in ["bảo hiểm", "bhyt", "bhxh", "bảo hiểm y tế", "bảo hiểm xã hội"]):
        return "cơ quan bảo hiểm xã hội hoặc bộ phận một cửa có thẩm quyền"
    if "dịch vụ công" in q:
        return "Cổng Dịch vụ công Quốc gia hoặc bộ phận một cửa của cơ quan có thẩm quyền"
    return "cơ quan có thẩm quyền hoặc bộ phận một cửa tại địa phương"


COMMON_STOPWORDS = {
    "là", "và", "của", "cho", "với", "trong", "khi", "thì", "này", "kia", "đó", "đây",
    "bạn", "mình", "tôi", "giúp", "hỏi", "được", "không", "có", "về", "theo", "như", "ra",
}


def _query_keywords(query: str) -> list[str]:
    words = re.findall(r"[\wÀ-ỹ]+", (query or "").lower())
    out: list[str] = []
    seen: set[str] = set()
    for word in words:
        if word in COMMON_STOPWORDS:
            continue
        if len(word) < 3 and word not in {"bhyt", "bhxh"}:
            continue
        if word not in seen:
            seen.add(word)
            out.append(word)
    return out


def _max_keyword_overlap(query: str, chunks: list[RetrievedChunk]) -> int:
    keywords = _query_keywords(query)
    if not keywords or not chunks:
        return 0
    best = 0
    for chunk in chunks:
        haystack = f"{chunk.document_name} {chunk.content[:800]}".lower()
        overlap = sum(1 for token in keywords if token in haystack)
        if overlap > best:
            best = overlap
    return best


def assess_retrieval(
    query: str,
    chunks: list[RetrievedChunk],
    mode_hint: Any = None,
    force_web: bool = False,
) -> RetrievalAssessment:
    scores = [float(chunk.score) for chunk in chunks]
    best = max(scores) if scores else 0.0
    avg = (sum(scores) / len(scores)) if scores else 0.0
    distinct_documents = len({chunk.document_id for chunk in chunks})
    greeting = is_greeting_query(query)
    legal = is_legal_query(query)
    procedure = is_procedure_query(query)
    freshness = needs_freshness_check(query)
    max_overlap = _max_keyword_overlap(query, chunks)

    if greeting:
        return RetrievalAssessment(
            mode=mode_hint.value if hasattr(mode_hint, "value") else str(mode_hint or "auto"),
            chunk_count=0,
            distinct_documents=0,
            best_score=0.0,
            avg_score=0.0,
            confidence="high",
            should_use_rag=False,
            should_force_web=False,
            should_refuse_precise=False,
            reason="greeting",
            authority_hint=None,
        )

    strong = bool(chunks) and (
        (best >= 0.62 and (max_overlap >= 1 or best >= 0.72))
        or (best >= 0.55 and avg >= 0.48 and max_overlap >= 1)
        or (best >= 0.50 and len(chunks) >= 2 and max_overlap >= 1)
    )
    medium = bool(chunks) and max_overlap >= 1 and (
        best >= 0.45
        or avg >= 0.40
        or (best >= 0.40 and len(chunks) >= 2)
    )

    should_use_rag = strong or ((legal or procedure) and medium and best >= 0.50 and len(chunks) >= 2)
    should_force_web = ((legal or procedure) and not should_use_rag) or (freshness and not should_use_rag)
    should_refuse_precise = (legal or procedure) and not should_use_rag and not (force_web or should_force_web)

    if should_use_rag:
        confidence = "high" if strong else "medium"
        reason = "rag_grounded"
    elif should_force_web:
        confidence = "low"
        reason = "need_web_validation"
    elif should_refuse_precise:
        confidence = "low"
        reason = "insufficient_legal_basis"
    else:
        confidence = "medium" if chunks else "low"
        reason = "general_ai"

    mode = mode_hint.value if hasattr(mode_hint, "value") else str(mode_hint or "auto")
    return RetrievalAssessment(
        mode=mode,
        chunk_count=len(chunks),
        distinct_documents=distinct_documents,
        best_score=round(best, 4),
        avg_score=round(avg, 4),
        confidence=confidence,
        should_use_rag=should_use_rag,
        should_force_web=should_force_web,
        should_refuse_precise=should_refuse_precise,
        reason=reason,
        authority_hint=authority_hint_for_query(query) if should_refuse_precise else None,
    )


def build_safe_fallback_answer(query: str, assessment: RetrievalAssessment) -> str:
    authority = assessment.authority_hint or authority_hint_for_query(query)
    if is_procedure_query(query):
        return (
            "Mình chưa đủ cơ sở để khẳng định chính xác hoàn toàn về thủ tục này dựa trên tài liệu hiện có.\n\n"
            "Bạn nên đối chiếu thêm trên nguồn chính thức hoặc liên hệ trực tiếp "
            f"**{authority}** để được hướng dẫn đúng theo trường hợp cụ thể của bạn."
        )
    if is_legal_query(query):
        return (
            "Mình chưa đủ cơ sở để khẳng định chính xác hoàn toàn về quy định này tại thời điểm hiện tại.\n\n"
            "Bạn nên kiểm tra thêm trên văn bản hoặc nguồn chính thức, và nếu cần thì liên hệ trực tiếp "
            f"**{authority}** để được xác nhận theo hồ sơ thực tế."
        )
    return (
        "Mình chưa có đủ căn cứ đáng tin để trả lời chắc chắn câu này. "
        "Bạn vui lòng cung cấp thêm bối cảnh hoặc tài liệu liên quan để mình kiểm tra lại chính xác hơn."
    )