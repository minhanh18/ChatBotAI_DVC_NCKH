# Chatbot DVC + AI Agent

Dự án này là một chatbot nhiều thành phần gồm:

- **Trang chủ public** mô phỏng Cổng Dịch vụ công Quốc gia.
- **Floating chatbot launcher** có thể kéo thả, hover hiện nhãn, mở khung chat thu gọn.
- **Giao diện user** tone đỏ/cam, bỏ lịch sử hội thoại, giữ cơ chế chat streaming và backend hiện có.
- **Giao diện admin** giữ kiến trúc và chức năng quản trị tài liệu / giám sát / hội thoại.
- **Backend FastAPI + PostgreSQL + pgvector + Redis + Celery worker** cho RAG, lưu hội thoại, index tài liệu.

---

## 1. Kiến trúc tổng thể

```text
frontend/index.html
   └─ Trang chủ public + chatbot launcher + iframe chat

frontend/src (React + Vite)
   ├─ /assistant      -> giao diện user full màn hình
   └─ /admin          -> giao diện admin

backend/app (FastAPI)
   ├─ /api/chat/*
   ├─ /api/documents/*
   └─ /api/admin/*

PostgreSQL + pgvector
   ├─ conversations
   ├─ messages
   ├─ documents
   └─ embeddings / segments

Redis + Celery worker
   └─ hàng đợi index tài liệu
```

---

## 2. Những gì đã được chỉnh theo yêu cầu

### User UI

- Giữ **icon chatbot đỏ** ở trang chủ public.
- Khi click icon:
  - mở **khung chat thu gọn** bằng `iframe`
  - bấm **phóng to** để mở giao diện full màn hình.
- Giao diện user đã đổi sang **tone đỏ / nâu / cam**.
- **Bỏ lịch sử hội thoại bên trái** ở user.
- **Bỏ cơ chế bật web-search realtime trên user UI**.
- Giữ khả năng đọc nội dung hội thoại, markdown, feedback, citations.
- Giữ upload ảnh, gửi câu hỏi và ghi âm bằng trình duyệt (nếu trình duyệt hỗ trợ).

### Admin UI

- Không đổi kiến trúc tổng thể.
- Chỉ giữ mức chỉnh sửa tối thiểu để tránh xung đột route / build.
- Vẫn có:
  - chat admin
  - quản lý tài liệu
  - dashboard giám sát

### Backend

- Không thay đổi luồng xử lý chính.
- Chỉ bổ sung khả năng đọc cấu hình qua:
  - `DATABASE_URL_RAW`
  - `REDIS_URL_RAW`
- Mục đích: dễ deploy local lẫn Render mà không phải viết lại logic backend.

---

## 3. Cấu trúc thư mục quan trọng

```text
chatbot/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── agent/
│   │   ├── chat/
│   │   ├── models/
│   │   ├── rag/
│   │   ├── tasks/
│   │   ├── config.py
│   │   └── main.py
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── assets/
│   ├── src/
│   │   ├── api/client.ts
│   │   ├── components/
│   │   │   ├── ChatWindow.tsx
│   │   │   ├── MessageBubble.tsx
│   │   │   ├── DocumentsPanel.tsx
│   │   │   └── AdminDashboard.tsx
│   │   ├── App.tsx
│   │   └── index.css
│   ├── index.html
│   ├── chatbot-inline.js
│   ├── nginx.frontend.conf.template
│   ├── Dockerfile
│   └── vite.config.ts
├── docker-compose.yml
├── render.yaml
├── .env.example
└── README.md
```

---

## 4. Route sử dụng

### Public homepage

- `http://localhost/`

Hiển thị trang chủ public và floating chatbot icon.

### User chat full screen

- `http://localhost/assistant`

### User chat embedded

- `http://localhost/assistant?embed=1`

Route này được `index.html` dùng cho popup thu gọn qua `iframe`.

### Admin

- `http://localhost/admin`

---

## 5. Chạy local bằng Docker Compose

### Bước 1: tạo file môi trường

```bash
cp .env.example .env
```

Sau đó điền ít nhất:

```env
GEMINI_API_KEY=your_key
SECRET_KEY=your_secret
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your_password
```

### Bước 2: chạy hệ thống

```bash
docker compose up --build -d
```

### Bước 3: truy cập

- Homepage: `http://localhost`
- API docs: `http://localhost:8000/api/docs`
- Admin: `http://localhost/admin`

---

## 6. Chạy frontend / backend riêng khi phát triển

### Backend

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Worker

```bash
cd backend
celery -A app.tasks.ingest.celery_app worker --loglevel=info --concurrency=2 -Q celery
```

### Frontend

```bash
cd frontend
npm ci --legacy-peer-deps
npm run build
```

Hoặc chạy dev server:

```bash
npm run dev
```

---

## 7. Deploy lên Render

Repository đã có sẵn `render.yaml` để triển khai theo mô hình:

- 1 **frontend web service**
- 1 **backend private service**
- 1 **worker**
- 1 **Render Postgres**
- 1 **Render Key Value**

### Biến môi trường cần điền trên Render

Ít nhất cần điền:

- `GEMINI_API_KEY`
- `ADMIN_PASSWORD`

Các biến còn lại đã được chuẩn bị trong `render.yaml` hoặc có default phù hợp.

### Lưu ý sau khi tạo service

- Cập nhật lại `CORS_ORIGINS` nếu domain frontend thực tế khác.
- Nếu muốn đổi user admin, chỉnh `ADMIN_USERNAME` và `ADMIN_PASSWORD`.
- Backend sẽ tự chạy `CREATE EXTENSION IF NOT EXISTS vector` ở startup.

---

## 8. Điểm kỹ thuật quan trọng

### Frontend build path

Vite được cấu hình với:

```ts
base: '/app/'
```

Mục đích:

- homepage public vẫn nằm ở `/`
- React app nằm riêng dưới `/app/`
- nginx route `/assistant` và `/admin` sẽ trả về `app/index.html`

### Launcher script

`frontend/chatbot-inline.js` chỉ còn phụ trách:

- kéo thả chatbot icon
- mở / đóng panel
- chuyển giữa popup và fullscreen
- lazy-load `iframe` chat

Không xử lý chat logic trực tiếp nữa.

### Backend config

`backend/app/config.py` hỗ trợ đồng thời:

- local compose qua `DB_HOST`, `DB_PORT`, `REDIS_HOST`...
- managed service qua `DATABASE_URL_RAW`, `REDIS_URL_RAW`

---

## 9. Troubleshooting nhanh

### Frontend build lỗi

```bash
cd frontend
rm -rf node_modules
npm ci --legacy-peer-deps
npm run build
```

### Worker không index tài liệu

```bash
docker compose logs -f worker
```

### Backend không kết nối database

```bash
docker compose logs -f backend
```

### Không thấy homepage public khi deploy

Kiểm tra lại:

- `frontend/Dockerfile`
- `frontend/nginx.frontend.conf.template`
- file `index.html` đã được copy vào root nginx chưa

---

## 10. Mục tiêu của bản này

Bản này ưu tiên:

- đúng mong muốn giao diện user theo tone đỏ
- giữ homepage public + chatbot launcher
- không phá kiến trúc backend hiện có
- sẵn sàng đóng gói để deploy lên Render

Nếu cần làm tiếp vòng sau, nên xử lý theo thứ tự:

1. rà toàn bộ admin UI
2. tinh chỉnh icon / spacing / responsive nhỏ
3. kiểm tra toàn bộ flow upload + RAG + feedback trên môi trường Render thật
