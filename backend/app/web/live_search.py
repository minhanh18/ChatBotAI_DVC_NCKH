from __future__ import annotations

import asyncio
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, unquote, urlparse, urljoin
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

from app.config import settings

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)

URL_RE = re.compile(r"https?://[^\s)\]>]+", re.IGNORECASE)
DATE_PATTERNS = [
    re.compile(r"(\d{1,2}/\d{1,2}/\d{4})"),
    re.compile(r"(\d{1,2}-\d{1,2}-\d{4})"),
    re.compile(r"(\d{4}-\d{1,2}-\d{1,2})"),
    re.compile(r"ngày\s*(\d{1,2}/\d{1,2}/\d{4})", re.IGNORECASE),
]

LEGAL_FRESHNESS_KEYWORDS = [
    "mới nhất", "hiện hành", "hiện nay", "hiện tại",
    "còn hiệu lực", "đang có hiệu lực", "hiệu lực",
    "vừa sửa đổi", "vừa bổ sung", "sửa đổi", "bổ sung",
    "thay thế", "thay đổi", "ban hành mới", "cập nhật mới",
    "quy định mới", "luật mới", "nghị định mới", "thông tư mới",
    "nhà nước vừa sửa", "nhà nước vừa sửa đổi",
]

LEGAL_OBJECT_KEYWORDS = [
    "luật", "bộ luật", "nghị định", "thông tư", "quyết định", "quy định",
    "điều", "khoản", "điểm",
    "xử phạt", "mức phạt", "lệ phí",
    "thủ tục", "hành chính", "căn cứ pháp lý",
    "cư trú", "tạm trú", "thường trú", "cccd", "căn cước", "hộ tịch", "thuế",
    "dịch vụ công", "hộ khẩu", "khai sinh", "kết hôn", "ly hôn",
    "bảo hiểm", "bảo hiểm y tế", "bảo hiểm xã hội", "bhyt", "bhxh",
    # Từ KHÔNG DẤU phổ biến — cần thiết vì normalize_query_text() strip dấu
    # trước khi check keyword → "tạm trú" → "tam tru" không match list có dấu
    "tam tru", "thuong tru", "cu tru", "ho so", "thu tuc", "dang ky",
    "khai sinh", "ket hon", "ho khau", "can cuoc", "dich vu cong",
    "bao hiem", "le phi", "hanh chinh",
]

# Tên miền thương mại/ngân hàng bị chặn hoàn toàn — không liên quan thủ tục hành chính
_BLOCKED_COMMERCIAL_DOMAINS = [
    "techcombank.com", "vpbank.com.vn", "mbbank.com.vn", "vietcombank.com.vn",
    "bidv.com.vn", "agribank.com.vn", "vietinbank.vn", "hdbank.com.vn",
    "sacombank.com", "acb.com.vn", "tpbank.vn", "ocb.com.vn",
    "momo.vn", "zalopay.vn", "shopee.vn", "lazada.vn", "tiki.vn",
    "vietnamworks.com", "topcv.vn", "careerbuilder.vn",
    "24h.com.vn", "dantri.com.vn", "vnexpress.net", "tuoitre.vn", "thanhnien.vn",
    "cafef.vn", "cafebiz.vn", "baodautu.vn", "vneconomy.vn",
    "youtube.com", "facebook.com", "tiktok.com", "zalo.me",
    "wikipedia.org",
]

# Domain trả 403 khi fetch full page — chỉ dùng snippet từ Tavily, không fetch trực tiếp
_NO_FETCH_DOMAINS = [
    "thuvienphapluat.vn",
]

DISPUTE_RECHECK_KEYWORDS = [
    "bạn trả lời sai", "trả lời sai", "câu trước sai", "phản hồi trước sai",
    "sai rồi", "không đúng", "chưa đúng",
    "xem lại", "kiểm tra lại", "tra lại", "đối chiếu lại", "tra ngược lại",
]

QUERY_ABBREVIATIONS = {
    "đk": "đăng ký",
    "dk": "đăng ký",
    "tcn": "thu nhập cá nhân",
    "cccd": "căn cước công dân",
    "dvctt": "dịch vụ công trực tuyến",
}

PRIORITY_DOMAINS = [
    "dichvucong.gov.vn",
    "bocongan.gov.vn",
    "chinhphu.vn",
    "baohiemxahoi.gov.vn",
    "vbpl.vn",
    "luatvietnam.vn",
    "thuvienphapluat.vn",
    "gdt.gov.vn",       # Tổng cục thuế
    "mof.gov.vn",       # Bộ Tài chính
    "tracuuthue.gdt.gov.vn",
]

BLOCKED_URL_PATTERNS = [
    "/404",
    "/404.html",
    "page/tim-van-ban.aspx",
    "vbpqtimkiem.aspx",
    "portal.aspx?requesturl=",
    "requesturl=https://vbpl.vn/",
    "pages/portal.aspx",
]

BLOCKED_PAGE_PATTERNS = [
    "không tìm thấy trang",
    "url không tồn tại",
    "tìm kiếm văn bản",
    "tra cứu văn bản",
    "kết quả tìm kiếm",
    "văn bản pháp luật liên quan sau",
]


@dataclass
class WebResult:
    title: str
    url: str
    snippet: str
    content: str
    domain: str
    page_date: str | None = None
    fetched_at: str | None = None
    freshness_score: float = 0.0
    reliability_score: float = 0.0


def _contains_any(text: str, keywords: list[str]) -> bool:
    text = (text or "").lower()
    return any(keyword in text for keyword in keywords)


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def clean_text_preserve_breaks(text: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", (line or "")).strip() for line in (text or "").splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines).strip()


SERVICE_ACTION_KEYWORDS = [
    "thực hiện", "nộp hồ sơ", "nộp trực tuyến", "dịch vụ công", "dvc",
    "biểu mẫu", "mẫu đơn", "tải", "download", "hồ sơ", "thủ tục", "chi tiết",
]


def extract_relevant_links(soup: BeautifulSoup, base_url: str, query: str, limit: int = 12) -> list[tuple[str, str]]:
    """Trích các link thao tác/hồ sơ từ trang đã fetch để LLM không tự bịa URL."""
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    query_l = normalize_query_text(query).lower()
    subject_terms = exact_subject_terms(query_l) + significant_tokens(query_l)
    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        url = urljoin(base_url, href)
        if not url.startswith(("http://", "https://")):
            continue
        label = clean_text(a.get_text(" ", strip=True)) or url
        haystack = f"{label} {url}".lower()
        is_action = any(k in haystack for k in SERVICE_ACTION_KEYWORDS)
        is_official_dvc = "dichvucong.gov.vn" in url.lower()
        is_query_related = any(t in haystack for t in subject_terms)
        if not (is_action or is_official_dvc or is_query_related):
            continue
        if url in seen:
            continue
        seen.add(url)
        links.append((label[:120], url))
        if len(links) >= limit:
            break
    return links


def normalize_query_text(query: str) -> str:
    normalized = clean_text(query)
    if not normalized:
        return ""
    for short, expanded in QUERY_ABBREVIATIONS.items():
        normalized = re.sub(rf"(?<!\w){re.escape(short)}(?!\w)", expanded, normalized, flags=re.IGNORECASE)
    return clean_text(normalized)


# Pattern greeting phổ biến cần strip trước khi gửi Tavily
_GREETING_PREFIX_RE = re.compile(
    r"^\s*(xin\s+chào|chào|hello|hi|hey|alo|a\s+lô)[,!.\s]*",
    re.IGNORECASE,
)


def sanitize_web_query(query: str) -> str:
    normalized = normalize_query_text(query)
    # Strip greeting ở đầu query — "xin chào, tôi cần..." → "tôi cần..."
    normalized = _GREETING_PREFIX_RE.sub("", normalized).strip()
    match = re.search(r"Câu hỏi tiếp theo cùng ngữ cảnh\s*:\s*(.+)$", normalized, flags=re.IGNORECASE | re.DOTALL)
    if match:
        followup = clean_text(match.group(1))
        previous = clean_text(normalized[: match.start()])
        if followup:
            follow_l = followup.lower()
            intent_map = {
                "hồ sơ": ["hồ sơ", "giấy tờ", "cần gì", "chuẩn bị gì"],
                "lệ phí": ["lệ phí", "mức phí", "bao nhiêu tiền", "mất bao nhiêu"],
                "nơi nộp": ["nộp ở đâu", "làm ở đâu", "cơ quan nào", "đơn vị nào"],
                "thời hạn": ["bao lâu", "mấy ngày", "thời gian giải quyết", "thời hạn"],
                "điều kiện": ["điều kiện", "trường hợp nào", "yêu cầu gì"],
            }
            detected_intent = None
            for label, patterns in intent_map.items():
                if any(p in follow_l for p in patterns):
                    detected_intent = label
                    break
            previous_clean = re.sub(r"\b(tôi|mình|muốn|hỏi|cho hỏi|làm sao|như nào|thế nào|là gì|được không)\b", " ", previous, flags=re.IGNORECASE)
            previous_clean = clean_text(previous_clean)
            if detected_intent and previous_clean:
                normalized = f"{detected_intent} {previous_clean}"
            elif previous_clean:
                normalized = f"{followup} {previous_clean}"
            else:
                normalized = followup
    for phrase in sorted(DISPUTE_RECHECK_KEYWORDS, key=len, reverse=True):
        normalized = re.sub(re.escape(phrase), " ", normalized, flags=re.IGNORECASE)
    return clean_text(normalized)


def extract_urls(text: str) -> list[str]:
    return [match.rstrip(').,]') for match in URL_RE.findall(text or "")]


def is_legal_update_query(query: str) -> bool:
    q = normalize_query_text(query).lower()
    return _contains_any(q, LEGAL_OBJECT_KEYWORDS) and _contains_any(q, LEGAL_FRESHNESS_KEYWORDS)


def is_dispute_recheck_query(query: str) -> bool:
    return _contains_any(normalize_query_text(query).lower(), DISPUTE_RECHECK_KEYWORDS)


def should_prioritize_fresh_web_context(query: str) -> bool:
    return is_legal_update_query(query)


def should_search_web(query: str) -> bool:
    return bool(extract_urls(query)) or is_legal_update_query(query) or is_dispute_recheck_query(query)


async def maybe_fetch_web_context(query: str, force: bool = False) -> tuple[str, list[dict]]:
    if not settings.ENABLE_WEB_SEARCH:
        return "", []
    query = (query or "").strip()
    if not query:
        return "", []
    if not (force or should_search_web(query)):
        return "", []
    try:
        results = await search_and_fetch(query)
        if not results:
            return "", []
        context_parts: list[str] = []
        citations: list[dict] = []
        for index, result in enumerate(results, start=1):
            freshness_note = f"Ngày trên trang: {result.page_date}" if result.page_date else "Ngày trên trang: không rõ"
            fetched_note = f"Thu thập lúc: {result.fetched_at}" if result.fetched_at else "Thu thập lúc: không rõ"
            domain_note = f"Tên miền: {result.domain}"
            trust_note = f"Độ tin cậy nguồn: {result.reliability_score:.0%}"
            context_parts.append(
                f"[{index}] {result.title}\n"
                f"URL: {result.url}\n"
                f"{domain_note}\n"
                f"{freshness_note}\n"
                f"{fetched_note}\n"
                f"{trust_note}\n"
                f"Tóm tắt: {result.snippet}\n"
                f"Nội dung: {result.content}"
            )
            citations.append(
                {
                    "document_name": result.title,
                    "content": (result.snippet + "\n" + result.content[-900:] if "Liên kết thao tác/hồ sơ" in result.content else (result.snippet or result.content[:360])),
                    "score": min(0.99, max(0.55, result.freshness_score)),
                    "segment_id": result.url,
                    "url": result.url,
                    "source_type": "web",
                    "domain": result.domain,
                    "page_date": result.page_date,
                    "fetched_at": result.fetched_at,
                    "reliability_score": round(result.reliability_score, 2),
                }
            )
        return "\n\n---\n\n".join(context_parts), citations
    except Exception as exc:  # pragma: no cover
        logger.warning("Web search failed: %s", exc)
        return "", []


async def tavily_search(client: httpx.AsyncClient, query: str) -> list[dict]:
    api_key = (settings.TAVILY_API_KEY or "").strip()
    if not api_key:
        logger.warning("Tavily API key is missing; web search is disabled.")
        return []
    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": "advanced" if should_prioritize_fresh_web_context(query) else "basic",
        "max_results": max(settings.WEB_SEARCH_RESULTS_LIMIT, 5),
        "include_answer": False,
        "include_images": False,
        "include_raw_content": False,
    }
    include_domains = priority_domains_for_query(query)
    if include_domains:
        payload["include_domains"] = include_domains
    response = await client.post("https://api.tavily.com/search", json=payload)
    response.raise_for_status()
    data = response.json() or {}
    items = data.get("results") or []
    results: list[dict] = []
    seen: set[str] = set()
    for item in items:
        url = normalize_search_result_url(str(item.get("url") or "").strip())
        if not url or url in seen:
            continue
        results.append({
            "title": clean_text(str(item.get("title") or url)),
            "url": url,
            "snippet": clean_text(str(item.get("content") or item.get("snippet") or "")),
        })
        seen.add(url)
        if len(results) >= settings.WEB_SEARCH_RESULTS_LIMIT:
            break
    logger.info("Tavily results for [%s]: %s", query, len(results))
    logger.info("Tavily parsed URLs for [%s]: %s", query, [r.get("url") for r in results[:5]])
    return results


async def search_and_fetch(query: str) -> list[WebResult]:
    explicit_urls = extract_urls(query)
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(connect=3.0, read=min(settings.WEB_SEARCH_TIMEOUT_SEC, 5.0), write=3.0, pool=3.0),
        headers={"User-Agent": USER_AGENT},
    ) as client:
        if explicit_urls:
            fetched = await asyncio.gather(
                *[fetch_page_context(client, url, query=query) for url in explicit_urls[: settings.WEB_SEARCH_FETCH_PAGES]],
                return_exceptions=True,
            )
            results = [item for item in fetched if isinstance(item, WebResult)]
            return rank_web_results(query, results)[: settings.WEB_SEARCH_FETCH_PAGES]
        raw_results: list[dict] = []
        search_variants = build_search_queries(query)
        # Thực hiện tất cả các query search song song thay vì tuần tự
        search_tasks = [tavily_search(client, variant) for variant in search_variants]
        search_results = await asyncio.gather(*search_tasks, return_exceptions=True)
        for variant, result in zip(search_variants, search_results):
            if isinstance(result, Exception):
                logger.warning("Tavily query failed for %s: %s", variant, result)
            else:
                raw_results.extend(result)
        if not raw_results:
            return []
        deduped: list[dict] = []
        seen_urls: set[str] = set()
        for item in raw_results:
            url = item.get("url", "")
            if not url or url in seen_urls:
                continue
            if not is_candidate_url_allowed(url, query):
                continue
            seen_urls.add(url)
            deduped.append(item)
        logger.info("raw_results total = %s", len(raw_results))
        logger.info("candidate_results total = %s", len(deduped))
        logger.info("candidate URLs = %s", [r.get("url") for r in deduped[:5]])
        ranked_candidates = sorted(deduped, key=lambda item: score_search_result(query, item), reverse=True)
        fetch_limit = settings.WEB_SEARCH_FETCH_PAGES
        tasks = []
        for item in ranked_candidates[:fetch_limit]:
            logger.info("Fetching page content: %s", item.get("url"))
            tasks.append(fetch_page_context(client, item["url"], item.get("title"), item.get("snippet", ""), query=query))
        fetched = await asyncio.gather(*tasks, return_exceptions=True)
        results = [item for item in fetched if isinstance(item, WebResult)]
        logger.info("fetched WebResult total = %s", len(results))
        logger.info("fetched result URLs = %s", [r.url for r in results[:5]])
        return rank_web_results(query, results)[:fetch_limit]


async def fetch_page_context(client: httpx.AsyncClient, url: str, title_hint: str | None = None, snippet_hint: str = "", query: str = "") -> WebResult | None:
    # Domain trả 403 khi fetch — dùng snippet từ Tavily thay vì fetch full page
    _domain = get_domain(url)
    if any(_domain == d or _domain.endswith(f".{d}") for d in _NO_FETCH_DOMAINS):
        if title_hint and snippet_hint:
            logger.debug("Skip fetch (no-fetch domain), dùng snippet: %s", url)
            fetched_at = now_app_time().strftime("%H:%M:%S %d/%m/%Y")
            result = WebResult(
                title=title_hint,
                url=url,
                snippet=clean_text(snippet_hint),
                content=clean_text(snippet_hint),
                domain=_domain,
                fetched_at=fetched_at,
            )
            result.reliability_score = score_domain_reliability(_domain, query, title_hint)
            result.freshness_score = 0.5
            return result
        return None
    try:
        response = await client.get(url)
        response.raise_for_status()
    except Exception as exc:
        logger.debug("Skip url %s: %s", url, exc)
        return None
    final_url = str(response.url)
    if not is_candidate_url_allowed(final_url, query):
        return None
    soup = BeautifulSoup(response.text, "html.parser")
    for selector in ["script", "style", "noscript", "svg", "header", "footer", "nav", "form", "aside"]:
        for node in soup.select(selector):
            node.decompose()
    title = title_hint or clean_text((soup.title.get_text(" ", strip=True) if soup.title else "")) or final_url
    content_candidates: list[str] = []
    for selector in ["main", "article", "[role='main']", ".content", ".article", ".post-content", "body"]:
        for node in soup.select(selector):
            text = clean_text_preserve_breaks(node.get_text("\n", strip=True))
            if len(text) > 180:
                content_candidates.append(text)
        if content_candidates:
            break
    if not content_candidates:
        return None
    raw_content = max(content_candidates, key=len)
    relevant_links = extract_relevant_links(soup, final_url, query)
    # Nếu trang có bảng dạng nhiều cột (lệ phí, hồ sơ, trình tự...) → dùng
    # _extract_table_aware_content để giữ cấu trúc bảng thay vì cắt 4 blocks ngẫu nhiên.
    # extract_focus_content sẽ bỏ sót dữ liệu trong các hàng/cột không được score cao.
    if _has_table_structure(raw_content):
        content = _extract_table_aware_content(raw_content, query, settings.WEB_SEARCH_MAX_CONTEXT_CHARS * 3)
    else:
        content = extract_focus_content(raw_content, query, settings.WEB_SEARCH_MAX_CONTEXT_CHARS)
    if relevant_links:
        links_note = "\n".join(f"- {label}: {url}" for label, url in relevant_links)
        content = f"{content}\n\nLiên kết thao tác/hồ sơ trích từ trang:\n{links_note}".strip()
    if looks_like_generic_page(final_url, title, raw_content) and not relevant_links:
        return None
    page_date = extract_page_date(soup, response.headers, title, snippet_hint, raw_content)
    legal_status_excerpt = extract_legal_status_excerpt(raw_content)
    snippet = legal_status_excerpt or clean_text(snippet_hint) or content[:260]
    fetched_at = now_app_time().strftime("%H:%M:%S %d/%m/%Y")
    domain = get_domain(final_url)
    result = WebResult(title=title, url=final_url, snippet=snippet, content=content, domain=domain, page_date=page_date, fetched_at=fetched_at)
    result.reliability_score = score_domain_reliability(domain, query, result.title)
    result.freshness_score = score_web_result(query, result)
    return result


def build_search_queries(query: str) -> list[str]:
    base = sanitize_web_query(query)
    if not base:
        return []
    now = now_app_time()
    date_vi = now.strftime("%d/%m/%Y")
    year = now.strftime("%Y")
    base_l = base.lower()

    # Xác định nhóm query
    is_procedure = any(k in base_l for k in ["thủ tục", "hồ sơ", "nộp", "đăng ký", "cấp", "dịch vụ công", "trực tuyến", "tạm trú", "căn cước", "khai sinh", "hộ kinh doanh"])
    is_fee = any(k in base_l for k in ["lệ phí", "mức phí", "mức thu", "phí đăng ký", "phí nộp"])
    is_tax = any(k in base_l for k in ["thuế", "tncn", "thu nhập", "khai thuế", "quyết toán", "tính thuế"])
    is_fresh = should_prioritize_fresh_web_context(base)

    variants: list[str] = []

    if is_procedure:
        # site:dichvucong query thường trả kết quả tốt hơn bare query cho thủ tục
        # → bỏ bare query, chỉ dùng 2 site-specific queries
        variants.extend([
            f"site:dichvucong.gov.vn {base}",
            f"{base} biểu mẫu hồ sơ dichvucong.gov.vn",
        ])
    else:
        variants.append(base)

    if is_fee:
        variants.extend([
            f"site:luatvietnam.vn {base}",
            f"site:thuvienphapluat.vn {base}",
        ])
    if is_tax:
        variants.extend([
            f"site:gdt.gov.vn {base}",
            f"site:luatvietnam.vn {base}",
        ])
    if is_fresh:
        variants.extend([
            f"{base} mới nhất {year}",
            f"{base} hiện hành",
        ])

    # Đảm bảo luôn có ít nhất 1 query
    if not variants:
        variants = [base]

    ordered: list[str] = []
    seen: set[str] = set()
    for item in variants:
        key = item.lower().strip()
        if key and key not in seen:
            seen.add(key)
            ordered.append(item)
    # Giới hạn tối đa 3 Tavily queries để tránh lãng phí thời gian
    return ordered[:3]


def rank_web_results(query: str, results: list[WebResult]) -> list[WebResult]:
    for result in results:
        result.reliability_score = score_domain_reliability(result.domain, query, result.title)
        result.freshness_score = score_web_result(query, result)
    return sorted(results, key=lambda item: (item.reliability_score, item.freshness_score), reverse=True)


def score_search_result(query: str, item: dict) -> float:
    title = clean_text(str(item.get("title", "")))
    snippet = clean_text(str(item.get("snippet", "")))
    url = str(item.get("url", ""))
    haystack = f"{title} {snippet} {url}".lower()
    domain = get_domain(url)
    if not is_candidate_url_allowed(url, query):
        return -999.0
    score = score_domain_reliability(domain, query, title) * 4.0
    # Penalize kết quả dành cho người nước ngoài khi query của người dùng
    # không đề cập đến nước ngoài/người nước ngoài
    _foreign_markers = ["người nước ngoài", "nước ngoài", "ngoại kiều", "foreigner", "foreign national"]
    _query_mentions_foreign = any(m in query.lower() for m in _foreign_markers)
    if not _query_mentions_foreign and any(m in haystack for m in _foreign_markers):
        score -= 3.5  # đẩy xuống dưới kết quả cho người Việt Nam
    for token in significant_tokens(query):
        if token in haystack:
            score += 1.0
        if token in title.lower():
            score += 1.4
    if should_prioritize_fresh_web_context(query):
        year = now_app_time().strftime("%Y")
        if year in haystack:
            score += 1.6
        if any(keyword in haystack for keyword in ["hiện hành", "mới nhất", "hiệu lực", "sửa đổi", "bổ sung", "thay thế"]):
            score += 2.2
    return score


def score_web_result(query: str, result: WebResult) -> float:
    haystack = f"{result.title} {result.snippet} {result.content} {result.url}".lower()
    score = 0.35 + (result.reliability_score * 0.3)
    # Penalize kết quả cho người nước ngoài khi người dùng không hỏi về nước ngoài
    _foreign_markers = ["người nước ngoài", "nước ngoài", "ngoại kiều", "foreigner"]
    _query_mentions_foreign = any(m in query.lower() for m in _foreign_markers)
    if not _query_mentions_foreign and any(m in haystack for m in _foreign_markers):
        score -= 0.40  # penalize trong ranking sau fetch
    for token in significant_tokens(query):
        if token in haystack:
            score += 0.08
        if token in result.title.lower():
            score += 0.11
    if should_prioritize_fresh_web_context(query):
        score += date_recency_bonus(result.page_date)
        if any(keyword in haystack for keyword in ["hiện hành", "mới nhất", "hiệu lực", "sửa đổi", "bổ sung", "thay thế"]):
            score += 0.14
    # Ưu tiên URL DVC dẫn đến trang thông tin thủ tục chính thức (dvc-chi-tiet-thu-tuc-nganh-doc)
    url_l = result.url.lower()
    if "dichvucong.gov.vn" in url_l:
        if "dvc-chi-tiet-thu-tuc-nganh-doc" in url_l:
            score += 0.20   # trang thủ tục chính thức theo ngành dọc
        elif "dvc-tthc-thu-tuc-hanh-chinh-chi-tiet" in url_l:
            score += 0.05   # trang chi tiết có nút nộp — giữ nhưng ưu tiên thấp hơn
        elif "dvc-chi-tiet-thu-tuc-hanh-chinh" in url_l:
            score += 0.08   # trang chi tiết thủ tục hành chính thông thường
    return min(score, 0.99)


def extract_page_date(soup: BeautifulSoup, headers: httpx.Headers, *text_candidates: str) -> str | None:
    meta_candidates = [
        "meta[property='article:published_time']",
        "meta[property='article:modified_time']",
        "meta[name='pubdate']",
        "meta[name='publish-date']",
        "meta[name='date']",
        "meta[itemprop='datePublished']",
        "meta[itemprop='dateModified']",
        "time[datetime]",
    ]
    for selector in meta_candidates:
        node = soup.select_one(selector)
        if not node:
            continue
        raw = node.get("content") or node.get("datetime") or node.get_text(" ", strip=True)
        parsed = normalize_date_string(raw)
        if parsed:
            return parsed
    last_modified = headers.get("last-modified")
    if last_modified:
        try:
            dt = parsedate_to_datetime(last_modified)
            return dt.strftime("%d/%m/%Y")
        except Exception:
            pass
    for text in text_candidates:
        parsed = normalize_date_string(text)
        if parsed:
            return parsed
    return None


def normalize_date_string(value: str | None) -> str | None:
    if not value:
        return None
    value = clean_text(value)
    if not value:
        return None
    for pattern in DATE_PATTERNS:
        match = pattern.search(value)
        if not match:
            continue
        date_str = match.group(1)
        for fmt in ["%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"]:
            try:
                dt = datetime.strptime(date_str, fmt)
                return dt.strftime("%d/%m/%Y")
            except ValueError:
                continue
    return None


def exact_subject_terms(query: str) -> list[str]:
    q = normalize_query_text(query).lower()
    subjects: list[str] = []
    if "tạm trú" in q:
        subjects.extend(["đăng ký tạm trú", "gia hạn tạm trú", "tạm trú"])
    if "thường trú" in q:
        subjects.extend(["đăng ký thường trú", "thường trú"])
    if "cư trú" in q:
        subjects.extend(["cư trú", "luật cư trú"])
    if "lệ phí" in q or "mức thu" in q or ("phí" in q and "đăng ký" in q):
        subjects.extend(["lệ phí", "mức thu", "nộp hồ sơ trực tuyến", "nộp hồ sơ trực tiếp"])
    if "cccd" in q or "căn cước" in q:
        subjects.extend(["căn cước công dân", "cccd"])
    if "khai sinh" in q:
        subjects.extend(["khai sinh", "đăng ký khai sinh"])
    if "kết hôn" in q:
        subjects.extend(["đăng ký kết hôn", "kết hôn"])
    ordered: list[str] = []
    seen: set[str] = set()
    for item in subjects:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def negative_subject_terms(query: str) -> list[str]:
    q = normalize_query_text(query).lower()
    negatives: list[str] = []
    if "tạm trú" in q and "thường trú" not in q:
        negatives.append("thường trú")
    if "thường trú" in q and "tạm trú" not in q:
        negatives.append("tạm trú")
    return negatives


def query_focus_terms(query: str) -> list[str]:
    normalized = normalize_query_text(query)
    words = [token for token in re.findall(r"[\wÀ-ỹ]+", normalized.lower()) if len(token) >= 2]
    phrases: list[str] = []
    for size in (4, 3, 2):
        for i in range(len(words) - size + 1):
            phrase = " ".join(words[i:i + size]).strip()
            if len(phrase) >= 5:
                phrases.append(phrase)
    priority_terms = exact_subject_terms(normalized)
    ordered: list[str] = []
    seen: set[str] = set()
    for term in priority_terms + sorted(phrases + significant_tokens(normalized), key=len, reverse=True):
        if term and term not in seen:
            seen.add(term)
            ordered.append(term)
    return ordered[:12]


def score_focus_snippet(snippet: str, query: str) -> float:
    snippet_l = snippet.lower()
    score = 0.0
    for term in exact_subject_terms(query):
        if term in snippet_l:
            score += 4.0 if len(term) > 6 else 2.5
    for term in query_focus_terms(query):
        if term in snippet_l:
            score += 1.2
    for term in negative_subject_terms(query):
        if term in snippet_l:
            score -= 3.0
    if any(token in snippet_l for token in ["đồng", "mức thu", "lệ phí", "phạt", "hiệu lực"]):
        score += 0.8
    return score



# Các section header bảng trong trang thủ tục hành chính — dùng để detect bảng.
# Chú ý: KHÔNG đưa từ phổ biến trong câu văn ("trực tuyến", "trực tiếp") vào đây
# vì sẽ gây false positive với bài viết thường.
_TABLE_SECTION_HEADERS = [
    "cách thức thực hiện", "hình thức nộp", "phí, lệ phí", "phí :", "lệ phí :",
    "thành phần hồ sơ", "trình tự thực hiện", "yêu cầu, điều kiện", "đối tượng thực hiện",
    "căn cứ pháp lý", "kết quả thực hiện", "thời hạn giải quyết",
    # Row headers trong bảng DVC — chỉ là header khi đứng RIÊNG trên 1 dòng ngắn
]

# Ký hiệu trang có bảng: nhiều dòng ngắn liên tiếp (dữ liệu cột)
# hoặc có nhiều khoảng trắng liên tiếp trong dòng (tab giả lập cột)
def _has_table_structure(text: str) -> bool:
    """
    Phát hiện trang có bảng dữ liệu nhiều cột/hàng.

    Dấu hiệu:
    - Có section header bảng quen thuộc → True ngay
    - Nhiều dòng có khoảng trắng lớn (tab flatten cột) → True
    - Kết hợp short lines + ít nhất 1 tab-like (tránh false positive với bài viết thường)
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return False
    # Dấu hiệu 1: có section header bảng rõ ràng
    has_header = any(
        any(h in l.lower() for h in _TABLE_SECTION_HEADERS)
        for l in lines[:60]
    )
    # "Trực tiếp" / "Trực tuyến" là header bảng chỉ khi đứng RIÊNG trên 1 dòng ngắn (< 30 chars)
    # — không phải khi nằm giữa câu văn dài
    has_row_header = any(
        l.lower() in ("trực tiếp", "trực tuyến") or
        (len(l) < 30 and l.lower() in ("trực tiếp:", "trực tuyến:", "trực tiếp :", "trực tuyến :"))
        for l in lines[:60]
    )
    if has_header or has_row_header:
        return True
    # Dấu hiệu 2: nhiều dòng có tab / nhiều khoảng trắng (cột flatten)
    tab_like_lines = sum(1 for l in lines if "    " in l or "\t" in l)
    if tab_like_lines >= 3:
        return True
    # Dấu hiệu 3: kết hợp nhiều dòng ngắn + ít nhất 1 tab-like
    # Bài viết thường ít khi có tab — cần cả 2 điều kiện để tránh false positive
    short_ratio = sum(1 for l in lines if len(l) < 60) / max(len(lines), 1)
    return short_ratio > 0.55 and tab_like_lines >= 1


def _extract_table_aware_content(text: str, query: str, max_chars: int) -> str:
    """
    Trích xuất nội dung từ trang có bảng nhiều cột/hàng.

    Chiến lược:
    1. Tìm section liên quan nhất đến query (dùng keyword matching).
    2. Giữ TOÀN BỘ section đó — không cắt theo block score như extract_focus_content.
    3. Nếu content vẫn quá dài → cắt ưu tiên đoạn đầu của section liên quan.

    Mục đích: đảm bảo Gemini nhận đủ mọi hàng bảng (vd: Trực tiếp VÀ Trực tuyến,
    mọi mức phí, mọi loại hồ sơ) thay vì chỉ thấy hàng được score cao nhất.
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return text[:max_chars]

    query_l = (query or "").lower()
    # Keyword từ query để tìm section liên quan
    query_tokens = set(t for t in re.split(r"\s+", query_l) if len(t) >= 3)

    # Map section header → danh sách dòng thuộc section đó
    sections: list[tuple[str, list[str]]] = []
    current_header = "intro"
    current_lines: list[str] = []

    for line in lines:
        ll = line.lower()
        is_header = any(h in ll for h in _TABLE_SECTION_HEADERS) and len(line) < 120
        if is_header and current_lines:
            sections.append((current_header, current_lines))
            current_header = line
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines:
        sections.append((current_header, current_lines))

    if not sections:
        return text[:max_chars]

    # Score từng section theo query
    def score_section(header: str, sec_lines: list[str]) -> float:
        blob = " ".join([header] + sec_lines).lower()
        return sum(1.5 if t in header.lower() else 1.0 for t in query_tokens if t in blob)

    scored = sorted(sections, key=lambda s: score_section(s[0], s[1]), reverse=True)

    # Ghép section liên quan nhất trước, sau đó thêm các section khác cho đến hết max_chars
    result_parts: list[str] = []
    total = 0
    for header, sec_lines in scored:
        block = "\n".join(sec_lines)
        if total + len(block) + 2 <= max_chars:
            result_parts.append(block)
            total += len(block) + 2
        elif total == 0:
            # Section đầu tiên quá dài → cắt nhưng giữ phần đầu quan trọng
            result_parts.append(block[:max_chars])
            break
        if total >= max_chars:
            break

    # Sắp xếp lại theo thứ tự xuất hiện trong văn bản gốc
    result_parts_ordered: list[str] = []
    used = set(id(p) for p in result_parts)
    for _, sec_lines in sections:
        block = "\n".join(sec_lines)
        if any(block == p for p in result_parts):
            result_parts_ordered.append(block)

    combined = "\n\n".join(result_parts_ordered or result_parts)
    return combined[:max_chars]


def extract_focus_content(text: str, query: str, max_chars: int) -> str:
    text = clean_text_preserve_breaks(text)
    if len(text) <= max_chars:
        return text
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines:
        scored_blocks: list[tuple[float, str]] = []
        for idx, line in enumerate(lines):
            block = line
            if idx + 1 < len(lines) and len(block) < 120:
                block = f"{block} {lines[idx + 1]}"
            score = score_focus_snippet(block, query)
            if score > 0:
                scored_blocks.append((score, block))
        if scored_blocks:
            scored_blocks.sort(key=lambda item: (item[0], len(item[1])), reverse=True)
            chosen: list[str] = []
            total = 0
            for _, block in scored_blocks:
                if block in chosen:
                    continue
                negative_hits = sum(1 for term in negative_subject_terms(query) if term in block.lower())
                positive_hits = sum(1 for term in exact_subject_terms(query) if term in block.lower())
                if negative_hits > positive_hits:
                    continue
                remaining = max_chars - total
                if remaining <= 0:
                    break
                snippet = block[:remaining]
                chosen.append(snippet)
                total += len(snippet) + 4
                if len(chosen) >= 4:
                    break
            if chosen:
                return "\n".join(chosen)
    plain_text = clean_text(text)
    if len(plain_text) <= max_chars:
        return plain_text
    lowered = plain_text.lower()
    windows: list[tuple[int, int]] = []
    subject_terms = exact_subject_terms(query)
    for term in subject_terms or query_focus_terms(query):
        start_pos = 0
        while True:
            idx = lowered.find(term, start_pos)
            if idx == -1:
                break
            win_start = max(0, idx - 80)
            win_end = min(len(plain_text), idx + max(180, len(term) + 160))
            windows.append((win_start, win_end))
            start_pos = idx + len(term)
            if len(windows) >= 8:
                break
        if len(windows) >= 8:
            break
    if not windows:
        return plain_text[:max_chars]
    scored: list[tuple[float, int, int]] = []
    for start_pos, end_pos in windows:
        snippet = plain_text[start_pos:end_pos].strip(" ,.;:-")
        if snippet:
            scored.append((score_focus_snippet(snippet, query), start_pos, end_pos))
    scored.sort(key=lambda item: (item[0], item[2] - item[1]), reverse=True)
    parts: list[str] = []
    total = 0
    for _, start_pos, end_pos in scored:
        snippet = plain_text[start_pos:end_pos].strip(" ,.;:-")
        if not snippet:
            continue
        remaining = max_chars - total
        if remaining <= 0:
            break
        lower_snippet = snippet.lower()
        negative_hits = sum(1 for term in negative_subject_terms(query) if term in lower_snippet)
        positive_hits = sum(1 for term in exact_subject_terms(query) if term in lower_snippet)
        if negative_hits > positive_hits:
            continue
        snippet = snippet[:remaining]
        if snippet in parts:
            continue
        parts.append(snippet)
        total += len(snippet) + 5
        if len(parts) >= 3:
            break
    focused = " ... ".join(parts).strip()
    return focused if focused else plain_text[:max_chars]


def significant_tokens(query: str) -> list[str]:
    tokens = re.findall(r"[\wÀ-ỹ]+", normalize_query_text(query).lower())
    stop_words = {
        "là", "gì", "bao", "nhiêu", "cho", "tôi", "về", "của", "the", "a", "an", "and", "or",
        "như", "nào", "hiện", "nay", "mới", "nhất", "mức", "phí", "giúp", "mình", "xem", "lại",
    }
    return [token for token in tokens if len(token) >= 2 and token not in stop_words][:10]


def normalize_search_result_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        uddg = parse_qs(parsed.query).get("uddg", [""])[0]
        if uddg:
            return unquote(uddg)
    if url.startswith("//"):
        return f"https:{url}"
    return url


def now_app_time() -> datetime:
    return datetime.now(ZoneInfo(settings.APP_TIMEZONE))


def get_domain(url: str) -> str:
    return (urlparse(url).netloc or "").replace("www.", "").lower()


def is_allowed_legal_domain(domain: str) -> bool:
    domain = (domain or "").lower().replace("www.", "")
    return any(domain == allowed or domain.endswith(f".{allowed}") for allowed in PRIORITY_DOMAINS)


def is_candidate_url_allowed(url: str, query: str) -> bool:
    url_l = (url or "").lower()
    if not url_l.startswith(("http://", "https://")):
        return False
    if any(pattern in url_l for pattern in BLOCKED_URL_PATTERNS):
        return False
    domain = get_domain(url_l)
    if not domain:
        return False
    # Chặn cứng domain thương mại/ngân hàng không liên quan hành chính
    if any(domain == d or domain.endswith(f".{d}") for d in _BLOCKED_COMMERCIAL_DOMAINS):
        return False
    # Kiểm tra keyword pháp lý/hành chính trong query — dùng CẢ query gốc và normalized
    # vì normalize_query_text() strip dấu → "tạm trú" → "tam tru", check cả 2 để không bỏ sót
    query_normalized = normalize_query_text(query).lower()
    query_original = query.lower()
    all_keywords = LEGAL_OBJECT_KEYWORDS + LEGAL_FRESHNESS_KEYWORDS + DISPUTE_RECHECK_KEYWORDS
    if _contains_any(query_normalized, all_keywords) or _contains_any(query_original, all_keywords):
        return is_allowed_legal_domain(domain)
    return True


def extract_legal_status_excerpt(text: str) -> str:
    lines = [clean_text(line) for line in (text or "").splitlines() if clean_text(line)]
    matches: list[str] = []
    for line in lines:
        lower = line.lower()
        if any(token in lower for token in ["hiệu lực", "tình trạng", "ngày có hiệu lực", "còn hiệu lực", "hết hiệu lực", "bị thay thế", "sửa đổi, bổ sung", "sửa đổi bổ sung"]):
            matches.append(line)
        if len(matches) >= 2:
            break
    return " | ".join(matches)[:320]


def looks_like_generic_page(url: str, title: str, content: str) -> bool:
    haystack = clean_text(f"{title} {content}").lower()
    url_l = (url or "").lower()
    parsed = urlparse(url_l)
    if any(pattern in url_l for pattern in BLOCKED_URL_PATTERNS):
        return True
    if any(pattern in haystack for pattern in BLOCKED_PAGE_PATTERNS):
        return True
    if parsed.path in {"", "/", "/home", "/trang-chu"}:
        return True
    if get_domain(url_l) == "dichvucong.gov.vn" and ("dịch vụ công quốc gia" in haystack and len(content) < 500):
        return True
    return False


def priority_domains_for_query(query: str) -> list[str]:
    query_l = normalize_query_text(query).lower()
    if not _contains_any(query_l, LEGAL_OBJECT_KEYWORDS + LEGAL_FRESHNESS_KEYWORDS + DISPUTE_RECHECK_KEYWORDS):
        return []
    domains = PRIORITY_DOMAINS.copy()
    if any(token in query_l for token in ["thủ tục", "hồ sơ", "nộp", "đăng ký", "cấp", "dịch vụ công", "trực tuyến", "tạm trú", "căn cước"]):
        preferred = ["dichvucong.gov.vn", "csdl.dichvucong.gov.vn", "bocongan.gov.vn", "chinhphu.vn", "vbpl.vn", "thuvienphapluat.vn", "luatvietnam.vn"]
        domains = preferred + [d for d in domains if d not in preferred]
    if any(token in query_l for token in ["bảo hiểm", "bhyt", "bhxh"]):
        preferred = ["baohiemxahoi.gov.vn", "chinhphu.vn", "vbpl.vn", "luatvietnam.vn", "thuvienphapluat.vn", "dichvucong.gov.vn"]
        domains = preferred + [d for d in domains if d not in preferred]
    if any(token in query_l for token in ["thuế", "tncn", "thu nhập", "khai thuế", "quyết toán thuế", "tính thuế"]):
        preferred = ["gdt.gov.vn", "mof.gov.vn", "luatvietnam.vn", "vbpl.vn", "chinhphu.vn", "thuvienphapluat.vn"]
        domains = preferred + [d for d in domains if d not in preferred]
    return domains


def score_domain_reliability(domain: str, query: str, title: str = "") -> float:
    domain = (domain or "").lower()
    if not domain:
        return 0.40
    gov_domains = ["dichvucong.gov.vn", "bocongan.gov.vn", "chinhphu.vn", "baohiemxahoi.gov.vn", "vbpl.vn", "gdt.gov.vn", "mof.gov.vn"]
    if any(domain == d or domain.endswith(f".{d}") for d in gov_domains):
        score = 0.90
    elif "luatvietnam.vn" in domain:
        score = 0.75
    elif "thuvienphapluat.vn" in domain:
        score = 0.60
    elif domain.endswith(".gov.vn") or domain.endswith(".gov"):
        score = 0.82
    elif domain.endswith(".edu.vn") or domain.endswith(".edu"):
        score = 0.55
    else:
        score = 0.40
    priorities = priority_domains_for_query(query)
    if domain in priorities:
        score += max(0.0, 0.08 - priorities.index(domain) * 0.01)
    if any(token in title.lower() for token in ["chính thức", "official"]):
        score += 0.04
    return min(score, 0.98)


def date_recency_bonus(page_date: str | None) -> float:
    if not page_date:
        return 0.0
    try:
        dt = datetime.strptime(page_date, "%d/%m/%Y")
    except ValueError:
        return 0.0
    delta_days = abs((now_app_time().date() - dt.date()).days)
    if delta_days == 0:
        return 0.34
    if delta_days <= 1:
        return 0.26
    if delta_days <= 3:
        return 0.18
    if delta_days <= 7:
        return 0.12
    if delta_days <= 30:
        return 0.06
    return max(0.0, 0.04 - math.log10(delta_days + 1) * 0.02)