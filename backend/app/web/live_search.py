from __future__ import annotations

import asyncio
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, unquote, urljoin, urlparse
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
    "vbpl.vn",
    "dichvucong.gov.vn",
    "luatvietnam.vn",
    "thuvienphapluat.vn",
    "baohiemxahoi.gov.vn",
    "chinhphu.vn",
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


def normalize_query_text(query: str) -> str:
    normalized = clean_text(query)
    if not normalized:
        return ""
    for short, expanded in QUERY_ABBREVIATIONS.items():
        normalized = re.sub(rf"(?<!\w){re.escape(short)}(?!\w)", expanded, normalized, flags=re.IGNORECASE)
    return clean_text(normalized)


def sanitize_web_query(query: str) -> str:
    normalized = normalize_query_text(query)
    normalized = re.sub(r"câu hỏi tiếp theo cùng ngữ cảnh\s*:\s*", " ", normalized, flags=re.IGNORECASE)
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


def is_time_sensitive_query(query: str) -> bool:
    return is_legal_update_query(query)


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
                    "content": result.snippet or result.content[:260],
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


async def search_and_fetch(query: str) -> list[WebResult]:
    explicit_urls = extract_urls(query)
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(settings.WEB_SEARCH_TIMEOUT_SEC),
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
        for variant in build_search_queries(query):
            try:
                ddg_results = await duckduckgo_search(client, variant)
                if ddg_results:
                    raw_results.extend(ddg_results)
            except Exception as exc:
                logger.warning("DuckDuckGo query failed for %s: %s", variant, exc)

            if not raw_results:
                try:
                    bing_results = await bing_search(client, variant)
                    if bing_results:
                        raw_results.extend(bing_results)
                except Exception as exc:
                    logger.warning("Bing query failed for %s: %s", variant, exc)

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

        ranked_candidates = sorted(deduped, key=lambda item: score_search_result(query, item), reverse=True)
        fetch_limit = max(settings.WEB_SEARCH_FETCH_PAGES + 1, 5 if should_prioritize_fresh_web_context(query) else settings.WEB_SEARCH_FETCH_PAGES)
        tasks = [
            fetch_page_context(client, item["url"], item.get("title"), item.get("snippet", ""), query=query)
            for item in ranked_candidates[: fetch_limit * 2]
        ]
        fetched = await asyncio.gather(*tasks, return_exceptions=True)
        results = [item for item in fetched if isinstance(item, WebResult)]
        return rank_web_results(query, results)[:fetch_limit]


async def duckduckgo_search(client: httpx.AsyncClient, query: str) -> list[dict]:
    response = await client.get("https://html.duckduckgo.com/html/", params={"q": query, "kl": "vn-vi"})
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    results: list[dict] = []
    seen: set[str] = set()
    for block in soup.select(".result"):
        link = block.select_one("a.result__a") or block.select_one(".result__title a")
        if not link:
            continue

        raw_href = link.get("href", "").strip()
        url = normalize_search_result_url(raw_href)
        if not url or url in seen:
            continue

        if not is_candidate_url_allowed(url, query):
            continue

        snippet_node = block.select_one(".result__snippet")
        snippet = clean_text(snippet_node.get_text(" ", strip=True) if snippet_node else "")
        title = clean_text(link.get_text(" ", strip=True)) or url

        seen.add(url)
        results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= settings.WEB_SEARCH_RESULTS_LIMIT:
            break

    return results


async def bing_search(client: httpx.AsyncClient, query: str) -> list[dict]:
    response = await client.get("https://www.bing.com/search", params={"q": query, "setlang": "vi-VN"})
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    results: list[dict] = []
    seen: set[str] = set()
    for block in soup.select("li.b_algo"):
        link = block.select_one("h2 a")
        if not link:
            continue

        url = (link.get("href") or "").strip()
        if not url or url in seen:
            continue

        if not is_candidate_url_allowed(url, query):
            continue

        title = clean_text(link.get_text(" ", strip=True)) or url
        snippet_node = block.select_one(".b_caption p")
        snippet = clean_text(snippet_node.get_text(" ", strip=True) if snippet_node else "")

        seen.add(url)
        results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= settings.WEB_SEARCH_RESULTS_LIMIT:
            break

    return results


async def fetch_page_context(
    client: httpx.AsyncClient,
    url: str,
    title_hint: str | None = None,
    snippet_hint: str = "",
    query: str = "",
) -> WebResult | None:
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
    content = extract_focus_content(raw_content, query, settings.WEB_SEARCH_MAX_CONTEXT_CHARS)
    if looks_like_generic_page(final_url, title, raw_content):
        return None

    page_date = extract_page_date(soup, response.headers, title, snippet_hint, raw_content)
    legal_status_excerpt = extract_legal_status_excerpt(raw_content)
    snippet = legal_status_excerpt or clean_text(snippet_hint) or content[:260]
    fetched_at = now_app_time().strftime("%H:%M:%S %d/%m/%Y")
    domain = get_domain(final_url)

    result = WebResult(
        title=title,
        url=final_url,
        snippet=snippet,
        content=content,
        domain=domain,
        page_date=page_date,
        fetched_at=fetched_at,
    )
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

    variants = [base]
    if should_prioritize_fresh_web_context(base):
        variants.extend([
            f"{base} mới nhất",
            f"{base} hiện hành",
            f"{base} còn hiệu lực không",
            f"{base} cập nhật {date_vi}",
            f"{base} {year}",
        ])

    for domain in priority_domains_for_query(base)[:4]:
        variants.append(f"site:{domain} {base}")
        if should_prioritize_fresh_web_context(base):
            variants.append(f"site:{domain} {base} {year}")

    ordered: list[str] = []
    seen: set[str] = set()
    for item in variants:
        key = item.lower().strip()
        if key and key not in seen:
            seen.add(key)
            ordered.append(item)
    return ordered


def rank_web_results(query: str, results: list[WebResult]) -> list[WebResult]:
    for result in results:
        result.reliability_score = score_domain_reliability(result.domain, query, result.title)
        result.freshness_score = score_web_result(query, result)
    return sorted(results, key=lambda item: (item.freshness_score, item.reliability_score), reverse=True)


def score_search_result(query: str, item: dict) -> float:
    title = clean_text(str(item.get("title", "")))
    snippet = clean_text(str(item.get("snippet", "")))
    url = str(item.get("url", ""))
    haystack = f"{title} {snippet} {url}".lower()
    domain = get_domain(url)
    if not is_candidate_url_allowed(url, query):
        return -999.0

    score = score_domain_reliability(domain, query, title) * 4.0
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

    for token in significant_tokens(query):
        if token in haystack:
            score += 0.08
        if token in result.title.lower():
            score += 0.11

    if should_prioritize_fresh_web_context(query):
        score += date_recency_bonus(result.page_date)
        if any(keyword in haystack for keyword in ["hiện hành", "mới nhất", "hiệu lực", "sửa đổi", "bổ sung", "thay thế"]):
            score += 0.14

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
    filtered: list[str] = []
    for token in tokens:
        if len(token) < 2 or token in stop_words:
            continue
        filtered.append(token)
    return filtered[:10]


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
    if url.startswith("/"):
        return urljoin("https://duckduckgo.com", url)
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
    if _contains_any(normalize_query_text(query).lower(), LEGAL_OBJECT_KEYWORDS + LEGAL_FRESHNESS_KEYWORDS + DISPUTE_RECHECK_KEYWORDS):
        return is_allowed_legal_domain(domain)
    return True




def extract_legal_status_excerpt(text: str) -> str:
    lines = [clean_text(line) for line in (text or '').splitlines() if clean_text(line)]
    matches: list[str] = []
    for line in lines:
        lower = line.lower()
        if any(token in lower for token in ['hiệu lực', 'tình trạng', 'ngày có hiệu lực', 'còn hiệu lực', 'hết hiệu lực', 'bị thay thế', 'sửa đổi, bổ sung', 'sửa đổi bổ sung']):
            matches.append(line)
        if len(matches) >= 2:
            break
    return ' | '.join(matches)[:320]


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
    if any(token in query_l for token in ["bảo hiểm", "bhyt", "bhxh"]):
        preferred = ["baohiemxahoi.gov.vn", "vbpl.vn", "luatvietnam.vn", "thuvienphapluat.vn", "chinhphu.vn", "dichvucong.gov.vn"]
        domains = preferred + [d for d in domains if d not in preferred]
    return domains


def score_domain_reliability(domain: str, query: str, title: str = "") -> float:
    domain = (domain or "").lower()
    score = 0.45
    if not domain:
        return score

    if domain.endswith(".gov.vn") or domain.endswith(".gov"):
        score += 0.38
    elif domain.endswith(".edu.vn") or domain.endswith(".edu"):
        score += 0.24
    elif domain.endswith(".org"):
        score += 0.12
    elif domain.endswith(".com.vn"):
        score += 0.08

    if domain in priority_domains_for_query(query):
        score += 0.28
    if "thuvienphapluat.vn" in domain and _contains_any(query.lower(), ["luật", "nghị định", "thông tư", "cư trú", "tạm trú", "thường trú", "xử phạt", "lệ phí"]):
        score += 0.16
    if any(token in title.lower() for token in ["chính thức", "official"]):
        score += 0.08
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
