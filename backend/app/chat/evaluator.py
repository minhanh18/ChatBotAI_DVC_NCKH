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
    # Câu hỏi về lệ phí / thời gian / hồ sơ (kể cả follow-up ngắn)
    r"\blệ phí\b", r"\bphí\b", r"\bmất bao lâu\b", r"\bthời hạn\b",
    r"\bgiấy tờ\b", r"\btài liệu cần\b", r"\bđiều kiện\b",
    # Không dấu phổ biến
    r"\bho so\b", r"\ble phi\b", r"\bthu tuc\b", r"\bdang ky\b",
]

FRESHNESS_PATTERNS = [
    "mới nhất", "hiện hành", "hiện tại", "còn hiệu lực", "đang có hiệu lực",
    "sửa đổi", "bổ sung", "thay thế", "quy định mới", "luật mới", "nghị định mới", "thông tư mới",
]

GREETING_PATTERNS = [
    r"^\s*(xin\s*chào|chào\s*bạn|chào|hello|hi|hey)\s*[!.?… ]*$",
    r"^\s*(chào\s*ad|alo|a\s*lô)\s*[!.?… ]*$",
    # Tiếng Việt thông thường / trêu đùa
    r"^\s*(hong\s*bé\s*ơi|bé\s*ơi|ơi\s*bé|hey\s*bé|bé\s*đâu|bé\s*ơi\s*bé)\s*[!.?… ]*$",
    r"^\s*(ơi\s*bạn|bạn\s*ơi|ê\s*bạn|ê\s*bot|bot\s*ơi)\s*[!.?… ]*$",
    r"^\s*(có\s*ai\s*đó\s*không|có\s*ai\s*ở\s*đây\s*không|ai\s*đó\s*không)\s*[!.?… ]*$",
]

# Các mẫu chitchat: cảm ơn, tạm biệt, biểu đạt cảm xúc, xác nhận
CHITCHAT_PATTERNS = [
    r"^\s*(cảm\s*ơn|cám\s*ơn|thanks?|thank\s*you|cảm\s*ơn\s*bạn|cảm\s*ơn\s*nhiều)\s*[!.?… ]*$",
    r"^\s*(tạm\s*biệt|bye|goodbye|gặp\s*lại)\s*[!.?… ]*$",
    r"^\s*(ok|oke|okay|được\s*rồi|hiểu\s*rồi|rõ\s*rồi|ừ|uh|vâng|dạ)\s*[!.?… ]*$",
    r"^\s*(hay\s*đó|tốt|tốt\s*lắm|tuyệt|tuyệt\s*vời|ngon|đỉnh|hữu\s*ích)\s*[!.?… ]*$",
    r"^\s*(mệt\s*quá|mệt\s*rồi|khó\s*quá|phức\s*tạp\s*quá|rối\s*quá|stress)\s*[!.?!… ]*$",
    r"^\s*(bực\s*quá|bực\s*mình|khó\s*chịu|chán\s*quá|thất\s*vọng)\s*[!.?!… ]*$",
    r"^\s*(không\s*hiểu|chưa\s*hiểu|vẫn\s*chưa\s*hiểu|hả)\s*[!.?… ]*$",
    r"^\s*(ừ\s*nhỉ|ờ\s*nhỉ|ừ\s*đúng|vậy\s*à|thế\s*à|ồ|ôi)\s*[!.?… ]*$",
    # Câu ngắn vô nghĩa / thử nghiệm bot
    r"^\s*([a-z]{1,4})\s*[!.?]*$",                        # "abc", "test", "asdf"
    r"^\s*\d+\s*[!.?]*$",                                  # "123", "1"
    r"^\s*(bla\s*bla|la\s*la|ha\s*ha|haha|hihi|lol)\s*[!.?… ]*$",
]


def is_greeting_query(query: str) -> bool:
    q = (query or "").strip().lower()
    if not q:
        return False
    return any(re.match(pattern, q) for pattern in GREETING_PATTERNS)


def is_chitchat_query(query: str) -> bool:
    """Phát hiện các câu hỏi/phát biểu ngắn mang tính chitchat (cảm ơn, biểu đạt cảm xúc, xác nhận...)."""
    q = (query or "").strip().lower()
    if not q:
        return False
    return any(re.match(pattern, q) for pattern in CHITCHAT_PATTERNS)


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


def is_focused_aspect_query(query: str) -> bool:
    """
    Phát hiện khi user hỏi VỀ MỘT KHÍA CẠNH CỤ THỂ của thủ tục
    (chỉ hỏi hồ sơ / lệ phí / thời gian / điều kiện / nơi nộp...)
    thay vì hỏi toàn bộ thủ tục.

    Khi True → _domain_instructions KHÔNG inject hướng dẫn 8 mục đầy đủ,
    Gemini chỉ trả lời đúng khía cạnh được hỏi.
    """
    q = (query or "").lower()
    _FOCUSED_PATTERNS = [
        # Hỏi về hồ sơ (cả có dấu lẫn không dấu)
        r"\b(hồ sơ|giấy tờ|tài liệu|văn bản|cần (chuẩn bị|mang|nộp|có)|cần những gì|gồm (những gì|gì|các gì))\b",
        r"\b(ho so|giay to|can chuan bi|can nhung gi|gom nhung gi)\b",  # không dấu
        # Hỏi về lệ phí / chi phí (cả có dấu và không dấu, và câu hỏi tiếp theo ngắn)
        r"\b(lệ phí|chi phí|mất bao nhiêu tiền|bao nhiêu tiền|miễn phí không|có mất phí không)\b",
        r"\b(le phi|mat bao nhieu|bao nhieu tien|mien phi khong)\b",  # không dấu
        # Hỏi về thời gian — chỉ dạng câu hỏi rõ ràng về thời hạn
        r"\b(mất bao lâu|bao nhiêu ngày|bao nhiêu giờ|bao lâu|thời hạn giải quyết|khi nào xong)\b",
        r"\b(mat bao lau|bao nhieu ngay|bao lau|khi nao xong)\b",  # không dấu
        # Hỏi về điều kiện / đối tượng
        r"\b(điều kiện|yêu cầu|đối tượng|tiêu chuẩn|tiêu chí)\b",
        r"\b(dieu kien|yeu cau|doi tuong|tieu chuan)\b",  # không dấu
        # Hỏi về nơi nộp / địa điểm
        r"\b(nộp ở đâu|nơi nộp|địa điểm|trụ sở|phòng ban|cơ quan nào|đến đâu|ở đâu nộp)\b",
        r"\b(nop o dau|noi nop|dia diem|co quan nao|den dau|o dau nop)\b",  # không dấu
        # Hỏi về kết quả
        r"\b(kết quả|nhận ở đâu|trả kết quả|nhận lại|nhận được gì|nhận như thế nào)\b",
        # Hỏi riêng về căn cứ pháp lý
        r"\b(căn cứ pháp lý|quy định (nào|tại đâu)|luật nào|theo (điều|khoản|nghị định|thông tư) nào)\b",
        # Hỏi về TRƯỜNG HỢP / TÌNH HUỐNG cụ thể — phải có từ "trường hợp" rõ ràng
        r"\b(trường hợp nào|trường hợp nào thì|những trường hợp|các trường hợp|trường hợp (bắt buộc|được miễn|không cần|cần))\b",
        r"\b(khi nào (phải|cần|bắt buộc|được phép|không cần)|có bắt buộc không|bắt buộc không|có cần không|có phải không)\b",
        r"\b(truong hop nao|khi nao phai|co bat buoc khong)\b",  # không dấu
        # Hỏi về định nghĩa / khái niệm — chỉ khi có "là gì" / "nghĩa là gì" rõ ràng
        r"\b(là gì|nghĩa là gì|được hiểu là)\b",
        r"\b(la gi|nghia la gi)\b",  # không dấu
        # Hỏi về phạm vi / đối tượng áp dụng
        r"\b(áp dụng cho (ai|những ai|đối tượng nào)|đối tượng nào (phải|cần|bắt buộc)|ai (phải|cần|bắt buộc))\b",
    ]
    # KHÔNG phải focused nếu có từ "toàn bộ", "tất cả", "đầy đủ", "hướng dẫn", "cách thực hiện"
    # Thêm: "thế nào", "như thế nào", "ra sao" khi đi kèm thủ tục → là hỏi toàn bộ thủ tục
    _FULL_PROCEDURE_OVERRIDES = [
        r"\b(toàn bộ|tất cả|đầy đủ|hướng dẫn (thủ tục|cách|làm)|cách thực hiện|thực hiện như thế nào|quy trình|các bước)\b",
        r"\b(thủ tục (đăng ký|xin|cấp|làm|nộp)|làm thủ tục|thực hiện thủ tục)\b",
        # Fix #7: "thế nào / như thế nào / ra sao" đứng sau động từ thủ tục → hỏi TOÀN BỘ, không phải focused
        r"\b(đăng ký|làm|xin|cấp|nộp|thực hiện).{0,30}(thế nào|như thế nào|ra sao)\b",
        r"\b(thủ tục|quy trình|cách).{0,30}(thế nào|như thế nào|ra sao)\b",
    ]
    has_focused = any(re.search(p, q) for p in _FOCUSED_PATTERNS)
    has_full_override = any(re.search(p, q) for p in _FULL_PROCEDURE_OVERRIDES)
    return has_focused and not has_full_override


def is_out_of_domain(query: str) -> bool:
    """
    Phát hiện câu hỏi rõ ràng ngoài phạm vi hành chính/pháp lý/dịch vụ công.
    Trả về True nếu câu hỏi thuộc lĩnh vực như giáo dục phổ thông, giải trí,
    khoa học tự nhiên, thể thao, v.v. mà không liên quan đến thủ tục hành chính.
    """
    q = (query or "").lower()
    # Các chủ đề rõ ràng ngoài phạm vi
    _OUT_OF_DOMAIN_SIGNALS = [
        # Giáo dục phổ thông / học thuật
        r"chương trình.*(dạy|học|đào tạo|môn|lớp|năm học|tiết|bài|giáo án|giáo trình)",
        r"môn (ngữ văn|toán|vật lý|hóa học|sinh học|địa lý|lịch sử|tin học|thể dục|âm nhạc|mỹ thuật|tiếng anh)",
        r"lớp (1|2|3|4|5|6|7|8|9|10|11|12)\b",
        r"(phương trình|tích phân|đạo hàm|hình học|đại số|số học|xác suất)",
        r"(protein|adn|arn|tế bào|quang hợp|tiến hóa|phản ứng hóa học|nguyên tố)",
        # Giải trí / văn hóa
        r"(phim|bộ phim|diễn viên|ca sĩ|bài hát|âm nhạc|nhạc|album|mv|concert)",
        r"(game|trò chơi|esport|liên quân|lol|pubg|minecraft)",
        r"(bóng đá|bóng rổ|cầu lông|tennis|thể thao|giải đấu|vô địch|cầu thủ)",
        # Nấu ăn / sức khoẻ / làm đẹp không liên quan hành chính
        r"(công thức nấu|món ăn|nấu ăn|ẩm thực|bữa ăn|dinh dưỡng)\b(?!.*phí)",
        r"(giảm cân|tập gym|yoga|skincare|làm đẹp|mỹ phẩm)\b",
        # Công nghệ / lập trình không liên quan dịch vụ công
        r"(lập trình|python|javascript|code|coding|framework|database|server|api)\b(?!.*hành chính|.*dịch vụ|.*thuế)",
        # Thiên văn / địa lý tự nhiên
        r"(hành tinh|vũ trụ|ngôi sao|thiên hà|trái đất|núi lửa|động đất)\b",
        # ── Prompt injection / khai thác system ────────────────────────────
        r"(system\s*prompt|system\s*promt|system\s*instruction|prompt\s*của\s*bạn)",
        r"(bạn\s*được\s*lập\s*trình|bạn\s*là\s*ai\s*thực\s*sự|bạn\s*là\s*gpt|bạn\s*là\s*claude|bạn\s*là\s*gemini)",
        r"(ignore\s*previous|forget\s*instructions|pretend\s*you\s*are|act\s*as\s*)",
        r"(bỏ\s*qua\s*hướng\s*dẫn|giả\s*vờ\s*là|đóng\s*vai\s*là\s*(?!nhân viên|cán bộ|chuyên viên))",
        r"(token|embedding|weight|model|llm|gpt|neural|transformer)\b(?!.*thuế|.*phí|.*hành chính)",
    ]
    # Nếu khớp out-of-domain nhưng cũng chứa từ hành chính thì KHÔNG coi là out-of-domain
    _ADMIN_OVERRIDE = [
        "thủ tục", "hành chính", "dịch vụ công", "đăng ký", "cấp giấy",
        "thuế", "bảo hiểm", "giấy phép", "nghị định", "thông tư", "luật",
        "ubnd", "cơ quan", "nộp hồ sơ", "một cửa", "chứng thực", "công chứng",
    ]
    has_admin = any(kw in q for kw in _ADMIN_OVERRIDE)
    if has_admin:
        return False
    return any(re.search(pat, q) for pat in _OUT_OF_DOMAIN_SIGNALS)


def out_of_domain_reply() -> str:
    return (
        "Mình là trợ lý chuyên về thủ tục hành chính, pháp luật và dịch vụ công — "
        "nên câu hỏi này nằm ngoài lĩnh vực mình hỗ trợ.\n\n"
        "Bạn có câu hỏi nào về thủ tục hành chính, đăng ký giấy tờ, thuế, bảo hiểm "
        "hoặc các dịch vụ công khác không? Mình sẵn sàng giúp!"
    )


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
    chitchat = is_chitchat_query(query)
    legal = is_legal_query(query)
    procedure = is_procedure_query(query)
    freshness = needs_freshness_check(query)
    max_overlap = _max_keyword_overlap(query, chunks)

    if greeting or chitchat:
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
            reason="chitchat" if chitchat else "greeting",
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

    # RAG-first: với câu hỏi pháp lý/thủ tục, nếu đã có chunk liên quan ở mức dùng được
    # thì để RAG trả lời trước; chỉ fallback web khi RAG thật sự không trả lời được.
    weak_but_usable = bool(chunks) and max_overlap >= 1 and best >= 0.38
    should_use_rag = strong or ((legal or procedure) and (medium or weak_but_usable))
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
            "⚠️ **Thông tin chưa được xác thực từ tài liệu chính thức.**\n\n"
            "Hệ thống chưa tìm được căn cứ pháp lý hoặc quy định cụ thể đủ tin cậy để hướng dẫn chính xác thủ tục này.\n\n"
            f"Bạn cần liên hệ trực tiếp **{authority}** để được hướng dẫn đúng theo trường hợp cụ thể, "
            "tránh làm sai hoặc thiếu hồ sơ."
        )
    if is_legal_query(query):
        return (
            "⚠️ **Thông tin chưa được xác thực từ văn bản pháp luật hiện hành.**\n\n"
            "Hệ thống chưa tìm được quy định cụ thể đủ cơ sở để khẳng định chắc chắn về nội dung này.\n\n"
            f"Bạn cần liên hệ **{authority}** hoặc tra cứu trực tiếp tại văn bản gốc "
            "để xác nhận thông tin chính xác theo hồ sơ thực tế."
        )
    return (
        "⚠️ **Thông tin chưa xác thực được từ nguồn tài liệu hiện có.**\n\n"
        f"Vui lòng liên hệ **{authority}** để được cung cấp thông tin chi tiết và chính xác."
    )