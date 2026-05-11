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
│   /api/*     → Backend FastAPI (:8000)                          │
└──────────────────────┬──────────────────────────────────────────┘
                       │
         ┌─────────────▼─────────────┐
         │   FastAPI Backend (:8000)  │
         │  ┌─────────┐ ┌─────────┐  │
         │  │Chat API │ │Admin API│  │
         │  └────┬────┘ └─────────┘  │
         └───────┼────────────────────┘
                 │
    ┌────────────┼────────────────────────────┬──────────────────┐
    ▼            ▼                            ▼                  ▼
┌───────┐  ┌──────────┐              ┌──────────────┐  ┌──────────────┐
│Gemini │  │PostgreSQL│              │  Redis Cache │  │ Azure Blob   │
│  API  │  │+pgvector │              │  (Celery)    │  │  Storage     │
└───────┘  └──────────┘              └──────────────┘  └──────────────┘
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
[1] api/chat.py: nhận request
    ├─ Tạo/lấy Conversation
    ├─ Lưu user Message
    ├─ rag/query_rewriter.py: rewrite_query()  ← TRƯỚC TIÊN (chuẩn hóa không dấu/viết tắt)
    │   ├─ Bước 1: expand viết tắt offline (dk→đăng ký, bhyt→bảo hiểm y tế...)
    │   ├─ Bước 2: nếu >30% từ còn ascii-only → gọi Gemini restore dấu (timeout 10s)
    │   └─ Fail-safe: trả về bản đã expand viết tắt nếu LLM timeout/lỗi
    ├─ Kiểm tra out-of-domain → từ chối ngay nếu ngoài lĩnh vực (check trên query đã chuẩn hóa)
    │   └─ **Whitelist qua system prompt** (`_get_identity()`): LLM được lệnh từ chối mọi query
    │      ngoài hành chính/pháp luật/dịch vụ công, kể cả khi có web/RAG context.
    │      Khi từ chối: citations bị xoá, source lead bị bỏ qua (`_is_ood_refusal()`).
    ├─ Kiểm tra context clarification (check trên query đã chuẩn hóa)
    │   ├─ _needs_rewrite(): bỏ qua nếu query đã đủ dấu tiếng Việt
    │   ├─ Gemini xử lý: không dấu / viết tắt / sai chính tả / khẩu ngữ
    │   └─ fail-safe: dùng query gốc nếu lỗi hoặc timeout (3s)
    └─ Gọi agent/router.py → route()
        │
        ▼
[2] agent/router.py: phân loại câu hỏi
    ├─ Gọi HybridRetriever để lấy chunks từ pgvector
    ├─ assess_retrieval() → đánh giá chunk quality
    └─ Quyết định: RAG / AI / AI+RAG
        │
        ▼
[3] chat/engine.py: sinh câu trả lời (SSE streaming)
    │   ├─ _domain_instructions(query):
    │   │   ├─ is_procedure_query() → True: có thể inject hướng dẫn 8 mục
    │   │   ├─ is_focused_aspect_query() → True (hỏi hồ sơ/lệ phí/thời gian...):
    │   │   │   └─ KHÔNG inject 8 mục → Gemini chỉ trả lời đúng khía cạnh được hỏi
    │   │   └─ is_procedure_query() AND NOT is_focused_aspect_query(): inject đầy đủ 8 mục
    │   ├─ Lệ phí từ web: Gemini được hướng dẫn đọc đúng từng cột bảng trực tiếp/trực tuyến
    │
    ├─── [RAG path] ────────────────────────────────────────────
    │   ├─ Kiểm tra session_cache (overlap ≥ 25%)
    │   ├─ _build_rag_context() → xây system prompt với chunks
    │   ├─ _generate_rag_answer() → Gemini streaming
    │   ├─ _rag_evaluator: đánh giá [[RAG_NO_ANSWER]] hay đủ
    │   │   └─ `_RAG_NO_ANSWER_MARKERS` bao gồm: "không thể được trả lời dựa trên",
    │   │      "các tài liệu này chủ yếu liên quan đến" (và các biến thể) để đảm bảo
    │   │      fallback web khi Gemini báo tài liệu không phù hợp chủ đề
    │   └─ Nếu không đủ → fallback sang Web (force_web=True)
    │
    ├─── [AI + Web path] ───────────────────────────────────────
    │   ├─ web/live_search.py: Tavily search (song song)
    │   │   ├─ build_search_queries() → tạo nhiều query variants
    │   │   │   ├─ DVC + bocongan variants cho thủ tục/đăng ký/tạm trú
    │   │   │   └─ luatvietnam.vn + thuvienphapluat.vn variants cho "lệ phí" (tránh chỉ lấy DVC)
    │   │   ├─ asyncio.gather() → Tavily search song song
    │   │   ├─ score_web_result() → ranking + filtering
    │   │   └─ fetch_page_context() → lấy nội dung trang (song song)
    │   └─ _stream_ai() → Gemini streaming với web context
    │
    └─── [Post-processing] ─────────────────────────────────────
        ├─ _clean_response_text() → pipeline dọn markdown
        │   └─ _strip_inline_source_links(): strip [N] web khỏi UI; giữ (trang X) RAG
        ├─ _normalize_legal_answer_structure()
        ├─ chat/legal_enricher.py: trích link DVC, căn cứ pháp lý
        ├─ Lưu Message + UsageLog vào DB
        │   └─ `query_text`: lưu câu hỏi gốc (bỏ prefix "Chủ đề hiện tại: X. Câu hỏi:")
        │      để monitoring admin hiển thị đúng câu hỏi thực tế người dùng đặt
        └─ Gửi "done" SSE event
```

### 2.2 Context follow-up (câu hỏi tiếp nối)

```
"lệ phí như nào" (câu ngắn, không có topic riêng)
    │
    ▼
_is_context_light_query() → True
    │
    ▼
_collect_conversation_topics(history) → ["đăng ký tạm trú", ...]
    │
    ├─ 1 topic trong history → effective_query = "Chủ đề: đăng ký tạm trú. Câu hỏi: lệ phí như nào"
    └─ Nhiều topic → lấy topic từ user message GẦN NHẤT → ghép vào query
       → RAG/search tìm đúng tài liệu tạm trú, không bị kéo sang tài liệu thuế
```

### 2.3 Session Cache

```
Câu hỏi mới
    │
    ▼
chat/session_cache.py: get_cached_chunks(session_key, query)
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
Admin upload file (PDF/DOCX/TXT/MD/CSV/HTML)
        │
        ▼
[1] api/documents.py: upload_document()
    ├─ Validate extension + file size
    ├─ compute_file_hash() → kiểm tra duplicate
    ├─ Versioning: tìm tài liệu cùng tên → deprecated phiên bản cũ
    ├─ utils/storage.py: save_file()
    │   ├─ USE_AZURE_STORAGE=True → Azure Blob Storage
    │   └─ USE_AZURE_STORAGE=False → local disk (UPLOAD_DIR)
    ├─ Tạo record Document trong DB (status=pending)
    └─ asyncio.create_task(ingest_document(doc_id))
        │
        ▼
[2] rag/ingestor.py: _ingest_document_inner() [background task]
    ├─ Đọc doc info, set status=indexing, ĐÓNG connection
    ├─ Khôi phục file nếu cần:
    │   ├─ Có Azure Blob → restore_to_local() tải về disk tạm
    │   └─ Không có → dùng file_content bytes trong DB
    ├─ rag/extractor.py: extract_text() → văn bản thô
    ├─ rag/chunker.py: LegalAwareChunker.split() → chunks (~800 chars, overlap 120)
    ├─ rag/embedder.py: embedding_service.embed_texts() → vectors
    │   └─ Model: gemini-embedding-001 (fallback tự động)
    ├─ rag/legal_metadata.py: detect_legal_document_metadata()
    └─ Lưu DocumentSegment + vectors vào DB (status=ready)
```

---

## 4. Cơ chế lưu trữ file (Storage)

```
utils/storage.py — Storage Abstraction Layer
    │
    ├─ USE_AZURE_STORAGE = bool(AZURE_STORAGE_CONNECTION_STRING)
    │
    ├─── [Azure mode] ──────────────────────────────────────────
    │   ├─ save_file()   → BlobClient.upload_blob(overwrite=True)
    │   ├─ load_file()   → BlobClient.download_blob().readall()
    │   ├─ file_exists() → BlobClient.get_blob_properties()
    │   ├─ delete_file() → BlobClient.delete_blob()
    │   └─ restore_to_local() → tải blob → ghi disk tạm cho ingestor
    │
    └─── [Local mode] ──────────────────────────────────────────
        ├─ save_file()   → Path.write_bytes()
        ├─ load_file()   → Path.read_bytes() nếu tồn tại
        ├─ file_exists() → Path.exists()
        └─ delete_file() → Path.unlink()

Fallback hierarchy khi serve file:
    1. File trên disk (UPLOAD_DIR)
    2. Azure Blob (nếu USE_AZURE_STORAGE)
    3. file_content bytes trong DB (backup column)
    4. HTTP 404 với metadata để frontend hiển thị
```

---

## 5. Cơ chế bảo mật

| Thành phần | Cơ chế |
|-----------|--------|
| session_key | HMAC-SHA256 một chiều trước khi lưu DB (utils/data_crypto.py) |
| PII trong logs | Regex mask CCCD/SĐT/email trước khi ghi UsageLog |
| IP/user-agent | Không thu thập |
| Admin API | Basic auth (ADMIN_USERNAME / ADMIN_PASSWORD) |
| CORS | Chỉ cho phép origin trong CORS_ORIGINS |

---

## 6. Cơ chế xoay vòng Gemini API Key

```
chat/engine.py: _GeminiKeyPool
    ├─ Pool = [GEMINI_API_KEY] + GEMINI_API_KEYS (comma-separated)
    ├─ get_available_key() → key không trong cooldown
    ├─ rotate_on_quota(failed_key) → cooldown 65s, chuyển key kế
    ├─ parse_retry_after(exc) → đọc retry-after từ response Gemini
    └─ Nếu tất cả key cooling → await asyncio.sleep() đến key sớm nhất
```

---

## 7. Streaming (SSE Events)

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

## 8. Stack công nghệ

| Layer | Công nghệ |
|-------|-----------| 
| Frontend | React 18 + TypeScript + Vite + TailwindCSS |
| Backend | FastAPI + Python 3.11 + Uvicorn |
| AI Model | Google Gemini (gemini-2.5-flash) |
| Embedding | gemini-embedding-001 (dim: 768) |
| Web Search | Tavily API |
| Database | PostgreSQL 16 + pgvector |
| File Storage | Azure Blob Storage (deploy) / local disk (dev) |
| Cache/Queue | Redis + Celery |
| Proxy | Nginx |
| Deployment | Docker Compose / Render.com |
