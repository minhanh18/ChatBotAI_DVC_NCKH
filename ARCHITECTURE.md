# Kiến trúc & Luồng hoạt động hệ thống

## 1. Tổng quan kiến trúc

```
┌─────────────────────────────────────────────────────────────────┐
│                        Client (Browser)                          │
│   Trang chủ DVC  ─►  Launcher iframe  ─►  React Chat App       │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTP / SSE
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Nginx Reverse Proxy                          │
│   /          → Frontend static (React build)                    │
│   /api/*     → Backend FastAPI                                  │
└──────────────────────┬──────────────────────────────────────────┘
                       │
         ┌─────────────▼─────────────┐
         │   FastAPI Backend (:8000)  │
         │  ┌─────────┐ ┌─────────┐  │
         │  │Chat API │ │Admin API│  │
         │  └────┬────┘ └─────────┘  │
         └───────┼────────────────────┘
                 │
    ┌────────────┼────────────────────────────┐
    ▼            ▼                            ▼
┌───────┐  ┌──────────┐              ┌──────────────┐
│Gemini │  │PostgreSQL│              │  Redis Cache │
│  API  │  │+pgvector │              │  (Celery)    │
└───────┘  └──────────┘              └──────────────┘
                                          │
                                     ┌────▼────┐
                                     │ Celery  │
                                     │ Worker  │
                                     └─────────┘
```

---

## 2. Luồng xử lý câu hỏi (Chat Flow)

### 2.1 Luồng đầy đủ (RAG → fallback Web → AI)

```
User gửi câu hỏi
        │
        ▼
[1] chat.py: nhận request
    ├─ Tạo/lấy Conversation
    ├─ Lưu user Message
    ├─ Kiểm tra out-of-domain → từ chối ngay nếu ngoài lĩnh vực
    ├─ Kiểm tra context clarification
    ├─ rewrite_query() — chuẩn hóa query (LLM, timeout 3s, cached)
    │   ├─ _needs_rewrite(): bỏ qua nếu query đã đủ dấu tiếng Việt
    │   ├─ Gemini xử lý: không dấu / viết tắt / sai chính tả / khẩu ngữ
    │   └─ fail-safe: dùng query gốc nếu lỗi hoặc timeout
    └─ Gọi agent_router.route()
        │
        ▼
[2] agent/router.py: phân loại câu hỏi
    ├─ Gọi HybridRetriever để lấy chunks từ pgvector
    ├─ assess_retrieval() → đánh giá chunk quality
    └─ Quyết định: RAG / AI / AI+RAG
        │
        ▼
[3] chat/engine.py: sinh câu trả lời (SSE streaming)
    │
    ├─── [RAG path] ────────────────────────────────────────────
    │   ├─ Kiểm tra session_cache (overlap ≥ 25%)
    │   ├─ _build_rag_context() → xây system prompt với chunks
    │   ├─ _generate_rag_answer() → Gemini streaming
    │   ├─ _rag_evaluator: đánh giá [[RAG_NO_ANSWER]] hay đủ
    │   └─ Nếu không đủ → fallback sang Web (force_web=True)
    │
    ├─── [AI + Web path] ───────────────────────────────────────
    │   ├─ web/live_search.py: Tavily search (song song)
    │   │   ├─ build_search_queries() → tạo nhiều query variants
    │   │   ├─ asyncio.gather() → Tavily search song song
    │   │   ├─ score_web_result() → ranking + filtering
    │   │   └─ fetch_page_context() → lấy nội dung trang (song song)
    │   │       └─ Context format "[N] Title" → LLM sinh ([N]) per câu để giữ response súc tích
    │   ├─ _stream_ai() → Gemini streaming với web context
    │   │   └─ System prompt hướng dẫn LLM dùng ([N]) internally (bị strip trước khi ra UI)
    │   └─ Web citations yield qua SSE → hiển thị trong panel Tham khảo thêm
    │
    └─── [Post-processing] ─────────────────────────────────────
        ├─ _clean_response_text() → pipeline dọn markdown
        │   └─ _strip_inline_source_links(): strip ([N]) web khỏi output UI; giữ ([N], trang X) RAG
        ├─ _normalize_legal_answer_structure()
        ├─ Legal enrichment: trích link DVC, căn cứ pháp lý
        ├─ Lưu Message + UsageLog vào DB
        └─ Gửi "done" SSE event
```

### 2.2 Session Cache

```
Câu hỏi mới
    │
    ▼
get_cached_chunks(session_key, query)
    ├─ Token overlap ≥ 25% với cached chunks → HIT
    │   └─ Append cached chunks vào fresh chunks
    └─ Cache MISS → dùng fresh chunks từ retriever
        │
        ▼
cache_chunks() → lưu chunks mới vào in-memory cache (TTL 2h)
maybe_update_summary() → Gemini nén lịch sử mỗi 3 lượt
```

---

## 3. Luồng Upload & Index tài liệu

```
Admin upload file (PDF/DOCX)
        │
        ▼
[1] api/documents.py
    ├─ Lưu file vào disk
    ├─ Tạo record Document trong DB
    └─ Đẩy task vào Celery queue
        │
        ▼
[2] tasks/ingest.py (Celery Worker)
    ├─ rag/extractor.py → trích text từ PDF/DOCX
    ├─ rag/chunker.py → chia thành chunks (~800 chars, overlap 120)
    ├─ rag/embedder.py → Gemini embedding (gemini-embedding-001)
    │   └─ fallback tự động nếu model cũ không khả dụng
    └─ Lưu chunks + vectors vào PostgreSQL + pgvector
```

---

## 4. Cơ chế bảo mật

| Thành phần | Cơ chế |
|-----------|--------|
| session_key | HMAC-SHA256 một chiều trước khi lưu DB |
| PII trong logs | Regex mask CCCD/SĐT/email trước khi ghi UsageLog |
| IP/user-agent | Không thu thập |
| Admin API | Basic auth (ADMIN_USERNAME / ADMIN_PASSWORD) |
| CORS | Chỉ cho phép origin trong CORS_ORIGINS |

---

## 5. Cơ chế xoay vòng Gemini API Key

```
_GeminiKeyPool
    ├─ Pool = [GEMINI_API_KEY] + GEMINI_API_KEYS.split(',')
    ├─ get_available_key() → key không trong cooldown
    ├─ rotate_on_quota(failed_key) → đặt cooldown 65s, chuyển sang key kế
    ├─ parse_retry_after(exc) → đọc retry-after từ response Gemini
    └─ Nếu tất cả key cooling → await asyncio.sleep() đến key sớm nhất
```

---

## 6. Streaming (SSE Events)

| Event type | Dữ liệu | Mô tả |
|-----------|---------|-------|
| `conversation_id` | UUID | ID cuộc hội thoại |
| `mode` | `rag`/`ai`/`ai_rag` | Chế độ sinh câu trả lời |
| `token` | string | Chunk text streaming |
| `citations` | Citation[] | Nguồn tài liệu/web |
| `legal_refs` | LegalRef[] | Căn cứ pháp lý |
| `service_links` | ServiceLink[] | Link dịch vụ công |
| `usage` | {prompt, candidates, total} | Token usage từ Gemini |
| `done` | {tokens, latency_ms} | Hoàn tất |
| `error` | string | Lỗi xử lý |

---

## 7. Stack công nghệ

| Layer | Công nghệ |
|-------|-----------|
| Frontend | React 18 + TypeScript + Vite + TailwindCSS |
| Backend | FastAPI + Python 3.11 + Uvicorn |
| AI Model | Google Gemini Flash (gemini-2.0-flash / gemini-1.5-flash) |
| Embedding | gemini-embedding-001 |
| Web Search | Tavily API |
| Database | PostgreSQL 16 + pgvector |
| Cache/Queue | Redis + Celery |
| Proxy | Nginx |
| Deployment | Docker Compose / Render.com |
