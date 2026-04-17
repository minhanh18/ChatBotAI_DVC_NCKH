# README Chatbot

## 1. Mục tiêu

Chatbot trong dự án này là một trợ lý du lịch dùng Google Gemini để:

- trả lời câu hỏi tự nhiên về du lịch địa phương, đặc biệt là Khánh Hòa và Ninh Thuận
- gợi ý địa điểm theo ngữ cảnh hội thoại
- chèn hình minh họa khi prompt và tên ảnh khớp từ khóa
- lưu lịch sử chat theo `ChatSession` và `ChatMessage`
- hỗ trợ thêm các API AI phụ trợ như tạo lịch trình, gợi ý địa điểm, ước tính chi phí

Luồng chat chính hiện được triển khai bởi:

- backend route: `backend/app/routes/ai.py`
- AI service: `backend/app/services/ai_service.py`
- model lưu lịch sử: `backend/app/models/ai.py`
- giao diện chat: `frontend/src/pages/guest/Chatbot.tsx`

## 2. Kiến trúc tổng thể

Luồng end-to-end hiện tại:

1. Người dùng nhập câu hỏi tại trang `frontend/src/pages/guest/Chatbot.tsx`.
2. Frontend thêm ngay một tin nhắn `USER` vào state để tạo cảm giác phản hồi tức thì.
3. Nếu chưa có `sessionId`, frontend gọi `POST /api/ai/sessions` để tạo session trước.
4. Frontend gửi `POST /api/ai/chat` bằng `fetch()` và đọc phản hồi theo kiểu SSE stream.
5. Backend kiểm tra:
   - tin nhắn rỗng
   - giới hạn guest
   - rate limit theo user hoặc IP
   - quyền truy cập `ChatSession`
6. Backend lấy tối đa 10 tin nhắn gần nhất trong session để làm `chat_history`.
7. Backend dựng `system_instruction` từ:
   - prompt hướng dẫn vai trò trợ lý du lịch
   - tri thức lấy từ `data_chat.txt`
   - ảnh phù hợp với từ khóa
   - `user_preferences` nếu đã đăng nhập
8. `GeminiAIService` gọi Gemini ở chế độ chat nhiều lượt với history. (Hiện được tổ chức theo package `app/services/ai/`)
9. Backend stream dần từng chunk về frontend.
10. Khi kết thúc, backend lưu:
   - 1 bản ghi `ChatMessage` của người dùng
   - 1 bản ghi `ChatMessage` của AI
   - cập nhật `updated_at` của session

## 3. Thành phần chính

### 3.1. Route chat

File: `backend/app/routes/ai.py`

Route chính:

- `POST /api/ai/chat`: chat streaming qua `text/event-stream`
- `POST /api/ai/sessions`: tạo session mới
- `GET /api/ai/sessions`: lấy danh sách session của user đã đăng nhập
- `GET /api/ai/sessions/<id>/messages`: lấy lịch sử tin nhắn của một session
- `GET /api/ai/img/<slug>`: trả ảnh theo `slug` tên file, có hỗ trợ tương thích ngược với ID cũ

Ngoài ra còn có các API AI liên quan:

- `POST /api/ai/generate-itinerary`
- `POST /api/ai/suggest-places`
- `POST /api/ai/estimate-cost`

### 3.2. AI service

File: `backend/app/services/ai_service.py`

Service `GeminiAIService` chịu trách nhiệm:

- nạp knowledge base từ `backend/app/data/data_chat.txt`
- tách knowledge base thành từng đoạn nhỏ bằng dòng trống
- chuẩn hóa tiếng Việt có dấu về dạng không dấu để so khớp từ khóa
- chọn các đoạn kiến thức liên quan nhất cho câu hỏi hiện tại
- chọn tối đa 2 ảnh có tên file khớp từ khóa
- dựng `system_instruction`
- khởi tạo Gemini chat với history nhiều lượt
- stream kết quả trả về từng chunk
- hỗ trợ xoay vòng nhiều Gemini API key qua biến môi trường `GEMINI_API_KEYS`
- dùng round-robin theo request và chỉ fallback sang key kế tiếp khi lỗi có vẻ liên quan quota, rate limit, auth hoặc lỗi transient

### 3.3. Model dữ liệu

File: `backend/app/models/ai.py`

`ChatSession`:

- `id`
- `user_id` có thể null với guest
- `title`
- `started_at`
- `updated_at`

`ChatMessage`:

- `id`
- `session_id`
- `sender_type`: `USER` hoặc `AI`
- `message_content`
- `created_at`

## 4. Logic chat chi tiết

### 4.1. Xác định người dùng

Trong `POST /api/ai/chat`:

- nếu có JWT hợp lệ thì dùng `current_user.id`
- nếu không có JWT thì dùng `request.remote_addr` để:
  - rate limit
  - giới hạn số lượt chat thử của guest

Điều này có nghĩa là:

- guest không có lịch sử gắn với tài khoản
- tài khoản đăng nhập có session riêng theo `user_id`

### 4.2. Giới hạn guest

Guest hiện chỉ được chat thử tối đa 3 lần trong 24 giờ.

Cơ chế:

- key cache: `guest_chat_limit:{ip}`
- chỉ tăng bộ đếm sau khi backend lưu được lịch sử chat

Khi vượt quá giới hạn:

- backend trả `403`
- payload có `error = GUEST_LIMIT_REACHED`
- frontend mở modal mời đăng nhập

### 4.3. Rate limit

Mỗi user hoặc IP chỉ được gửi tối đa 5 request chat trong 60 giây.

Cơ chế:

- key cache: `rate_limit:{user_id_or_ip}`
- dùng `cache.incr()` và `cache.expire()`

`cache` ở đây là `ResilientCache`:

- ưu tiên Redis nếu có
- tự fallback sang bộ nhớ cục bộ nếu Redis không sẵn sàng

### 4.4. Session chat

Logic route `/chat` xử lý như sau:

- nếu client gửi `session_id`:
  - backend tìm session đó
  - nếu session thuộc user khác thì chặn
  - nếu session là session guest nhưng request hiện tại là user đã đăng nhập thì backend bỏ session cũ và tạo session mới
- nếu không có session hợp lệ:
  - backend tự tạo `ChatSession`
  - `title` lấy từ 100 ký tự đầu của câu hỏi đầu tiên

Lưu ý:

- frontend cũng đang có logic tự gọi `POST /api/ai/sessions` trước khi gửi câu hỏi đầu tiên
- backend vẫn giữ cơ chế auto-create session như một lớp dự phòng

### 4.5. Lấy lịch sử hội thoại

Backend luôn lấy toàn bộ tin nhắn của session theo thời gian tăng dần, sau đó chỉ dùng 10 tin cuối:

- `USER` được map thành role `user`
- `AI` được map thành role `assistant`, rồi đổi sang `model` theo format Gemini

Điểm quan trọng:

- chatbot có trí nhớ ngắn hạn theo session
- chỉ nhớ 10 lượt gần nhất
- chưa có tóm tắt history dài hạn

### 4.6. Ngữ cảnh người dùng

Nếu user đã đăng nhập, backend cố gắng lấy:

- `user.preferences`

Rồi nhúng vào `system_instruction` dưới dạng JSON, ví dụ:

- category user thích
- mức độ ưu tiên

Nếu không lấy được preferences thì chatbot vẫn trả lời bình thường.

## 5. Cách knowledge base được dùng

Knowledge base nằm trong:

- `backend/app/data/data_chat.txt`

Quy trình dùng tri thức:

1. Chuẩn hóa câu hỏi thành lowercase, bỏ dấu tiếng Việt, bỏ ký tự đặc biệt.
2. Tách câu hỏi thành keyword, loại stopwords cơ bản.
3. Chấm điểm từng đoạn trong `data_chat.txt` theo số lần chứa keyword.
4. Lấy tối đa 4 đoạn tốt nhất, giới hạn khoảng 4500 ký tự.
5. Chèn phần tri thức này vào `system_instruction`.

Đặc điểm của cách làm hiện tại:

- đơn giản, dễ hiểu, không cần vector database
- nhanh với tập dữ liệu nhỏ
- phụ thuộc khá nhiều vào độ khớp từ khóa
- chưa có semantic retrieval thật sự

## 6. Cách chatbot chèn ảnh

Ảnh được lấy động từ thư mục:

- ưu tiên `backend/static/images/anh`
- fallback sang `backend/app/static/images/anh`
- tương thích tạm với `backend/static/uploads` nếu môi trường cũ chưa tách riêng thư mục `anh`

Luồng chọn ảnh:

1. Backend quét toàn bộ file ảnh trong thư mục và dựng catalog động trong RAM.
2. Tên file được chuẩn hóa thành `slug` không dấu, ví dụ `Hang Rái.webp` thành `hang-rai`.
3. Khi người dùng hỏi, hệ thống so khớp keyword với tên file đã chuẩn hóa.
4. Chọn tối đa 2 ảnh điểm cao nhất.
5. Nhúng vào `system_instruction` danh sách Markdown được phép dùng.
6. Sau khi AI trả lời xong, backend còn có một bước hậu xử lý:
   - ưu tiên dò đúng tên địa điểm xuất hiện trong câu hỏi hoặc câu trả lời
   - map trực tiếp địa điểm đó sang ảnh tương ứng trong catalog
   - nếu chưa có match rõ ràng mới fallback về so khớp keyword
   - nếu phản hồi chưa có ảnh, hệ thống sẽ tự chèn 1 đến 2 ảnh minh họa ở cuối câu trả lời

Ví dụ format AI được phép xuất:

```md
![Hang Rai](/api/ai/img/hang-rai)
```

Frontend sau đó:

- detect Markdown ảnh bằng regex
- render ra thẻ `<img>`
- nếu lỗi tải ảnh thì tự ẩn ảnh đó

Lưu ý:

- cơ chế mới không cần giữ một danh sách ảnh cố định trong code
- thêm hoặc xóa ảnh trong thư mục sẽ được backend tự nhận diện lại
- route vẫn hỗ trợ URL kiểu `/api/ai/img/3` để không làm hỏng các tin nhắn cũ đã lưu

## 7. Prompt điều khiển hành vi AI

Prompt hệ thống đang ép chatbot theo hướng:

- trả lời ngắn gọn, dễ đọc
- nếu là câu hỏi gợi ý địa điểm thì chỉ chọn 3 đến 5 nơi phù hợp nhất
- mỗi địa điểm chỉ mô tả 1 đến 2 ý ngắn
- không dump toàn bộ dữ liệu trong một lần
- ưu tiên bullet ngắn
- hạn chế lạm dụng Markdown heading
- nếu câu trả lời dài thì nên dừng gọn và mời người dùng hỏi tiếp

Về bản chất, chatbot hiện là:

- prompt-driven assistant
- knowledge-augmented bằng file text cục bộ
- multi-turn chat với history ngắn

Nó chưa phải một hệ RAG đầy đủ theo nghĩa có embedding, vector search, reranking hoặc tool calling nhiều bước.

## 8. Streaming từ backend sang frontend

Backend trả dữ liệu bằng SSE-like stream:

```text
data: {"session_id": 12}

data: {"text": "chunk 1"}

data: {"text": "chunk 2"}

data: {"done": true, "ai_message": {...}}
```

Frontend đọc stream thủ công bằng:

- `response.body.getReader()`
- `TextDecoder`
- tách từng dòng bắt đầu bằng `data: `

Mỗi khi có `data.text`:

- frontend nối thêm vào nội dung tin nhắn AI đang hiển thị

Khi có `data.done`:

- frontend thay tin nhắn tạm bằng `ai_message` thật từ backend

Nếu stream kết thúc mà không có `done`:

- frontend tự thêm câu nhắc kiểu "Bạn muốn mình tiếp tục phần còn lại không?"

## 9. Các API AI phụ trợ

### 9.1. `POST /api/ai/generate-itinerary`

Mục đích:

- tạo lịch trình theo số ngày, ngân sách, sở thích, địa điểm

Luồng:

- route lấy `preferences`
- gọi `ItineraryService.generate_smart_itinerary()`
- service có thể nhúng thêm danh sách `selected_places`
- gọi Gemini để tạo JSON lịch trình
- hậu xử lý để:
  - bổ sung `preferences`
  - thêm `created_at`
  - gắn `place_id`, `map_url` nếu match địa điểm
  - tự cộng `estimated_cost` nếu AI không trả

Nếu user đã đăng nhập:

- lịch trình còn được lưu vào `SavedItinerary`

### 9.2. `POST /api/ai/suggest-places`

Mục đích:

- gợi ý địa điểm từ danh sách `Location` đang active trong database

Luồng:

- lấy tối đa 50 địa điểm active
- serialize thành JSON
- đưa toàn bộ vào prompt
- yêu cầu Gemini trả JSON recommendation

### 9.3. `POST /api/ai/estimate-cost`

Mục đích:

- ước tính chi phí cho một itinerary đã có

Luồng:

- route nhận JSON `itinerary`
- AI service tạo prompt yêu cầu Gemini trả breakdown chi phí

## 10. Hành vi frontend của trang chatbot

File: `frontend/src/pages/guest/Chatbot.tsx`

Trang chat hiện có các hành vi chính:

- nếu user đã đăng nhập:
  - tải danh sách session
  - tự mở session gần nhất nếu có
- nếu chưa đăng nhập:
  - không hiển thị lịch sử
  - hiển thị lời mời đăng nhập hoặc đăng ký
- khi gửi tin nhắn:
  - optimistic update tin nhắn user
  - hiện trạng thái `isTyping`
  - tạo session nếu cần
  - mở stream tới backend
- frontend hỗ trợ render:
  - text nhiều đoạn
  - `**bold**`
  - Markdown ảnh

## 11. Điểm mạnh của thiết kế hiện tại

- khá dễ hiểu và dễ debug
- không cần hạ tầng RAG phức tạp
- có hỗ trợ multi-turn chat thật sự
- có streaming nên UX ổn hơn kiểu đợi toàn bộ response
- có giới hạn guest và rate limit cơ bản
- có lưu lịch sử cho user đăng nhập
- có fallback cache cục bộ nếu Redis lỗi

## 12. Known issues và rủi ro logic hiện tại

### 12.1. Cache câu trả lời đang bỏ qua history và user context

Key cache chat hiện chỉ dựa trên:

- `md5(message.lower())`

Điều này có nghĩa là cùng một câu hỏi nhưng:

- khác session
- khác lịch sử chat
- khác preferences người dùng

vẫn có thể dùng chung một câu trả lời cache.

Hệ quả:

- sai ngữ cảnh
- làm mất tác dụng của hội thoại nhiều lượt
- user A có thể nhận response vốn được sinh cho user B nếu cùng câu hỏi

### 12.2. Guest session có thể bị đọc bởi client khác nếu đoán được `session_id`

Route `GET /api/ai/sessions/<id>/messages` chỉ chặn khi session có `user_id` và user hiện tại không sở hữu nó.

Nếu session là guest:

- `user_id = null`
- route vẫn trả toàn bộ message

Vì `session_id` là số tăng dần nên có rủi ro lộ nội dung chat guest.

### 12.3. Frontend tạo session trước cả khi gửi chat thành công

Hiện tại frontend gọi `POST /api/ai/sessions` trước, rồi mới gọi `POST /api/ai/chat`.

Nếu request chat thất bại vì:

- guest hết lượt
- rate limit
- lỗi mạng
- lỗi backend

thì vẫn có thể sinh ra session rỗng trong database.

### 12.4. Danh sách session trên frontend có thể stale

`chatService.getSessions()` đang dùng cache GET ở frontend.

Hệ quả:

- sau khi chat xong, `updated_at` hoặc thứ tự session có thể chưa phản ánh ngay
- title session cũng không được refresh lại từ backend nếu có thay đổi

### 12.5. Nút calculator trên giao diện chat chưa nối vào API chi phí

Trang chat có icon calculator ở header, nhưng hiện chưa có logic gọi `estimate-cost`.

Tức là:

- API có tồn tại
- UI chính của chatbot chưa dùng đến nó

## 13. Gợi ý cải thiện nếu muốn nâng cấp

- đổi cache key để bao gồm `session_id`, một phần history hoặc hash của context
- chặn hoàn toàn việc đọc session guest từ client khác
- bỏ bước create session ở frontend, để backend tự tạo session trong `/chat`
- hoặc chỉ tạo session sau khi chat thành công
- cân nhắc lưu summary history thay vì chỉ giữ 10 message cuối
- nâng retrieval từ keyword matching sang embeddings hoặc hybrid search
- nối UI với các API `estimate-cost` và `generate-itinerary`
- thêm logging có cấu trúc cho các lần chat lỗi, rate limit, cache hit

## 14. Kết luận

Chatbot hiện tại là một kiến trúc tương đối gọn:

- frontend stream thủ công
- backend quản lý session và lịch sử
- Gemini được điều khiển bằng `system_instruction`
- dữ liệu tri thức được lấy từ file text cục bộ

Nó phù hợp cho bài toán demo hoặc MVP tư vấn du lịch địa phương. Tuy nhiên, nếu muốn dùng ổn định hơn trong môi trường production, cần ưu tiên xử lý ba điểm trước:

- sửa cache theo ngữ cảnh hội thoại
- khóa quyền truy cập session guest
- tránh tạo session rỗng trước khi chat thành công
