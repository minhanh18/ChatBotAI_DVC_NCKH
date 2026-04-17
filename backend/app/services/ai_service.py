import google.generativeai as genai
from flask import current_app
import json, os, re
import threading
from typing import List, Dict, Optional

from app.utils.chatbot_images import (
    find_explicit_chatbot_images,
    find_relevant_chatbot_images,
    get_chatbot_image_dir,
    normalize_chatbot_text,
)
from app.utils.db_knowledge import (
    build_db_knowledge_text,
    get_db_image_list_for_prompt,
    get_db_knowledge_stats,
)

class GeminiAIService:
    def __init__(self):
        self.model = None
        self.knowledge_base = ""
        self.knowledge_sections = []
        self.api_keys: List[str] = []
        self._rotation_lock = threading.Lock()
        self._genai_lock = threading.RLock()
        self._next_api_key_index = 0
        # db_image_list is refreshed in _load_knowledge_base; populated after app context ready
        self._db_image_list: List[Dict] = []
        self._load_knowledge_base()
        self._configure()
        self._load_knowledge_base()
    
    def _load_knowledge_base(self):
        """Load knowledge base: data_chat.txt (static) + DB locations/dishes (dynamic)."""
        file_knowledge = ''
        candidates = [
            os.path.join(current_app.root_path, 'data', 'data_chat.txt'),
            os.path.join(os.path.dirname(current_app.root_path), 'app', 'data', 'data_chat.txt'),
            os.path.join(os.path.dirname(current_app.root_path), 'data', 'data_chat.txt'),
            os.path.normpath(os.path.join(os.path.abspath(os.path.dirname(__file__)), '..', 'data', 'data_chat.txt')),
        ]
        for data_path in candidates:
            data_path = os.path.normpath(data_path)
            if os.path.exists(data_path):
                try:
                    with open(data_path, 'r', encoding='utf-8') as f:
                        file_knowledge = f.read()
                    current_app.logger.info(f"Loaded data_chat.txt: {data_path} ({len(file_knowledge)} chars)")
                    break
                except Exception as e:
                    current_app.logger.error(f"Error reading {data_path}: {str(e)}")
        if not file_knowledge:
            current_app.logger.warning("data_chat.txt not found — using DB knowledge only.")

        # ── DB knowledge ────────────────────────────────────────────────────
        db_knowledge = ''
        try:
            db_knowledge = build_db_knowledge_text()
        except Exception as e:
            current_app.logger.warning(f"Could not load DB knowledge: {e}")

        # Combine: file first (broad tourism content), then DB (specific venue data)
        parts = [p for p in [file_knowledge, db_knowledge] if p]
        self.knowledge_base = '\n\n'.join(parts)
        self.knowledge_sections = [
            s.strip() for s in self.knowledge_base.split('\n\n') if s.strip()
        ]

        # ── DB image list ────────────────────────────────────────────────────
        try:
            self._db_image_list = get_db_image_list_for_prompt(message='', max_images=100)
            current_app.logger.info(
                f"db_knowledge: {len(self._db_image_list)} location images loaded"
            )
        except Exception as e:
            current_app.logger.warning(f"Could not load DB image list: {e}")
            self._db_image_list = []

        current_app.logger.info(
            f"Knowledge base ready: {len(self.knowledge_base)} chars, "
            f"{len(self.knowledge_sections)} sections, "
            f"{len(self._db_image_list)} DB images"
        )

    def refresh_knowledge(self) -> Dict:
        """Hot-reload knowledge from DB without restarting the server."""
        try:
            self._load_knowledge_base()
            stats = get_db_knowledge_stats()
            current_app.logger.info(f"AI knowledge refreshed: {stats}")
            return {'success': True, 'stats': stats}
        except Exception as e:
            current_app.logger.error(f"refresh_knowledge error: {e}")
            return {'success': False, 'error': str(e)}
    
    def _normalize_text(self, text: str) -> str:
        return normalize_chatbot_text(text)

    def _extract_keywords(self, text: str) -> List[str]:
        normalized = self._normalize_text(text)
        stopwords = {
            'toi', 'tu', 'van', 'cho', 'xin', 'hay', 'o', 'di', 'nhe', 'la', 'va', 'nhung', 'cac',
            'nhung', 'mot', 'ngay', 'dem', 'giup', 'minh', 'du', 'lich', 'dia', 'diem', 'tu', 'toi',
            'co', 'the', 'nao', 'khong', 'duoc', 'voi', 've', 'tai', 'den', 'tham', 'quan', 'goi', 'y',
            'nhat', 'nhieu', 'it', 'gan', 'xa', 'an', 'uong', 'luu', 'tru', 'chi', 'phi'
        }
        tokens = [token for token in normalized.split() if len(token) > 2 and token not in stopwords]
        ordered = []
        seen = set()
        for token in tokens:
            if token not in seen:
                seen.add(token)
                ordered.append(token)
        return ordered[:8]

    def _get_relevant_knowledge(self, message: str, max_sections: int = 4, max_chars: int = 4500) -> str:
        if not getattr(self, 'knowledge_sections', None):
            return self.knowledge_base[:max_chars]

        keywords = self._extract_keywords(message)
        if not keywords:
            return '\n\n'.join(self.knowledge_sections[:max_sections])[:max_chars]

        scored_sections = []
        for index, section in enumerate(self.knowledge_sections):
            normalized_section = self._normalize_text(section)
            score = sum(normalized_section.count(keyword) for keyword in keywords)
            if score > 0:
                scored_sections.append((score, index, section))

        if not scored_sections:
            return '\n\n'.join(self.knowledge_sections[:max_sections])[:max_chars]

        scored_sections.sort(key=lambda item: (-item[0], item[1]))
        selected = []
        current_length = 0
        for _, _, section in scored_sections[: max_sections * 2]:
            clean_section = section.strip()
            if not clean_section:
                continue
            projected = current_length + len(clean_section) + 2
            if selected and projected > max_chars:
                break
            selected.append(clean_section)
            current_length = projected
            if len(selected) >= max_sections:
                break

        return '\n\n'.join(selected)[:max_chars]

    def _get_relevant_image_list_for_prompt(self, message: str, max_images: int = 4) -> List[Dict]:
        """
        Return DB images relevant to *message*.
        Falls back to filesystem images if DB has none.
        """
        # ── DB images (primary source) ───────────────────────────────────
        if self._db_image_list:
            keywords = self._extract_keywords(message)
            if keywords:
                norm_message = normalize_chatbot_text(message)
                scored: List[tuple] = []
                for img in self._db_image_list:
                    norm_name = normalize_chatbot_text(img['name'])
                    score = sum(norm_name.count(kw) for kw in keywords)
                    if score > 0:
                        scored.append((score, img))
                scored.sort(key=lambda x: -x[0])
                if scored:
                    return [img for _, img in scored[:max_images]]
            # No specific match — return first N
            return self._db_image_list[:max_images]

        # ── Filesystem fallback ──────────────────────────────────────────
        try:
            keywords = self._extract_keywords(message)
            fs_images = find_relevant_chatbot_images(message, keywords=keywords, max_images=max_images)
            # Convert filesystem format to unified format
            return [
                {'name': img['display_name'], 'image_url': f"/api/ai/img/{img['slug']}"}
                for img in fs_images
            ]
        except Exception as e:
            current_app.logger.error(f"Error listing filesystem images: {e}")
            return []

    def _get_relevant_image_markdown(self, message: str, max_images: int = 4) -> str:
        """
        Build markdown lines telling the AI which images it may embed.
        Format: `![Name](url)` — Name
        """
        try:
            images = self._get_relevant_image_list_for_prompt(message, max_images=max_images)
            if not images:
                return ''
            lines = []
            for img in images:
                name = str(img['name'])
                url = str(img['image_url'])
                lines.append(f"- `![{name}]({url})` — {name}")
            return '\n'.join(lines)
        except Exception as e:
            current_app.logger.error(f"Error building image markdown: {e}")
            return ''

    def _build_image_gallery_markdown(self, source_text: str, max_images: int = 2) -> str:
        """
        After AI response is generated: if it doesn't already contain images,
        try to append relevant ones from DB (or filesystem fallback).
        """
        try:
            if not source_text or '![' in source_text:
                return ''

            images = self._get_relevant_image_list_for_prompt(source_text, max_images=max_images)
            if not images:
                return ''

            lines = [f"![{img['name']}]({img['image_url']})" for img in images]
            return '\n\nẢnh minh họa:\n\n' + '\n\n'.join(lines)
        except Exception as e:
            current_app.logger.error(f"Error building image gallery: {e}")
            return ''

    def append_relevant_images(self, response_text: str, source_text: str, max_images: int = 2) -> str:
        if not response_text:
            return response_text
        image_gallery = self._build_image_gallery_markdown(source_text, max_images=max_images)
        if not image_gallery:
            return response_text
        return response_text + image_gallery

    def _parse_api_keys(self) -> List[str]:
        raw_values = []
        keys_value = current_app.config.get('GEMINI_API_KEYS')
        single_value = current_app.config.get('GEMINI_API_KEY')
        if keys_value:
            raw_values.append(keys_value)
        if single_value:
            raw_values.append(single_value)

        parsed = []
        seen = set()
        for raw_value in raw_values:
            for item in re.split(r'[\r\n,;]+', str(raw_value)):
                key = item.strip()
                if not key or key in seen:
                    continue
                seen.add(key)
                parsed.append(key)
        return parsed

    def _mask_api_key(self, api_key: str) -> str:
        if len(api_key) <= 8:
            return '*' * len(api_key)
        return f"{api_key[:4]}...{api_key[-4:]}"

    def _reserve_api_key_order(self) -> List[str]:
        if not self.api_keys:
            return []
        with self._rotation_lock:
            start = self._next_api_key_index % len(self.api_keys)
            self._next_api_key_index = (start + 1) % len(self.api_keys)
            return self.api_keys[start:] + self.api_keys[:start]

    def _api_key_label(self, api_key: str) -> str:
        try:
            index = self.api_keys.index(api_key) + 1
        except ValueError:
            index = -1
        if index > 0:
            return f'key#{index}/{len(self.api_keys)} ({self._mask_api_key(api_key)})'
        return self._mask_api_key(api_key)

    def _should_try_next_api_key(self, error: Exception) -> bool:
        message = str(error).lower()
        retryable_markers = (
            'quota',
            'rate limit',
            'resource_exhausted',
            '429',
            '503',
            '500',
            'deadline exceeded',
            'timed out',
            'timeout',
            'temporarily unavailable',
            'service unavailable',
            'internal error',
            'api key',
            'permission denied',
            'unauthenticated',
            'invalid argument: api key',
            'invalid api key',
            'authentication',
            'connection reset',
            'connection aborted',
            'unavailable',
        )
        return any(marker in message for marker in retryable_markers)

    def _build_model(
        self,
        generation_config: Optional[Dict] = None,
        system_instruction: Optional[str] = None,
    ):
        return genai.GenerativeModel(
            model_name=self.model_name,
            generation_config=generation_config or self.generation_config,
            safety_settings=self.safety_settings,
            system_instruction=system_instruction,
        )

    def _run_with_api_keys(self, action_name: str, operation):
        ordered_keys = self._reserve_api_key_order()
        last_error = None

        for api_key in ordered_keys:
            key_label = self._api_key_label(api_key)
            try:
                with self._genai_lock:
                    genai.configure(api_key=api_key)
                    result = operation(api_key)
                current_app.logger.info(
                    f"Gemini {action_name} succeeded with {key_label}"
                )
                return result
            except Exception as e:
                last_error = e
                should_try_next = self._should_try_next_api_key(e)
                current_app.logger.warning(
                    f"Gemini {action_name} failed with {key_label}: {str(e)}"
                )
                if not should_try_next:
                    raise e

        if last_error:
            raise last_error
        raise ValueError("No Gemini API key available")

    def _configure(self):
        """Configure Gemini API"""
        try:
            self.api_keys = self._parse_api_keys()
            if not self.api_keys:
                raise ValueError("GEMINI_API_KEY or GEMINI_API_KEYS not configured")

            configured_model = current_app.config.get('GEMINI_MODEL') or 'models/gemini-2.5-flash'
            self.model_name = configured_model if configured_model.startswith('models/') else f'models/{configured_model}'
            
            generation_config = {
                "temperature": current_app.config.get('AI_TEMPERATURE', 0.8),
                "top_p": 0.95,
                "top_k": 40,
                "max_output_tokens": current_app.config.get('AI_MAX_TOKENS', 8192),
            }
            
            safety_settings = [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_ONLY_HIGH"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ]
            
            self.generation_config = generation_config
            self.safety_settings = safety_settings
            
            with self._genai_lock:
                genai.configure(api_key=self.api_keys[0])
                self.model = self._build_model()

            
        except Exception as e:
            current_app.logger.error(f"Error configuring Gemini: {str(e)}")
            raise
    
    def chat(self, message: str, context: Optional[Dict] = None, 
             chat_history: Optional[List[Dict]] = None) -> Dict:
        """Chat with Gemini AI"""
        try:
            response_text, _ = self._do_chat(message, context, chat_history, stream=False)
            return {
                'success': True,
                'response': response_text,
                'model': self.model_name,
                'finish_reason': 'stop'
            }
        except Exception as e:
            current_app.logger.error(f"Gemini chat error: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'response': 'Xin lỗi, tôi đang gặp sự cố kỹ thuật. Vui lòng thử lại sau.'
            }
    
    def _get_system_instruction(self, message: str = '', context: Optional[Dict] = None) -> str:
        """Build system instruction"""
        system = self._build_tourism_system_prompt(message)
        if context:
            system += f"\n\nThông tin bổ sung về người dùng:\n{json.dumps(context, ensure_ascii=False, indent=2)}"
        return system

    def _do_chat(self, message: str, context: Optional[Dict] = None,
                 chat_history: Optional[List[Dict]] = None, stream: bool = False):
        """Core chat logic using proper system_instruction and multi-turn history."""
        system_instruction = self._get_system_instruction(message, context)

        chat_generation_config = dict(self.generation_config)
        chat_generation_config['max_output_tokens'] = min(chat_generation_config.get('max_output_tokens', 8192), 1200)

        # Build proper Gemini chat history format
        gemini_history = []
        if chat_history:
            for msg in chat_history[-10:]:
                role = 'user' if msg.get('role') == 'user' else 'model'
                gemini_history.append({'role': role, 'parts': [msg.get('content', '')]})

        if stream:
            raise NotImplementedError("_do_chat(stream=True) is not used; call chat_stream instead.")

        def operation(_api_key):
            model_with_system = self._build_model(
                generation_config=chat_generation_config,
                system_instruction=system_instruction,
            )
            chat = model_with_system.start_chat(history=gemini_history)
            response = chat.send_message(message)
            return response.text, chat

        return self._run_with_api_keys('chat', operation)

    def _build_chat_prompt(self, message: str, context: Optional[Dict] = None, 
                          chat_history: Optional[List[Dict]] = None) -> str:
        """Legacy method - kept for compatibility."""
        return message

    def chat_stream(self, message: str, context: Dict = None, chat_history: List[Dict] = None):
        """Chat với AI mode streaming"""
        system_instruction = self._get_system_instruction(message, context)
        chat_generation_config = dict(self.generation_config)
        chat_generation_config['max_output_tokens'] = min(chat_generation_config.get('max_output_tokens', 8192), 1200)

        gemini_history = []
        if chat_history:
            for msg in chat_history[-10:]:
                role = 'user' if msg.get('role') == 'user' else 'model'
                gemini_history.append({'role': role, 'parts': [msg.get('content', '')]})

        ordered_keys = self._reserve_api_key_order()
        last_error = None

        for api_key in ordered_keys:
            key_label = self._api_key_label(api_key)
            emitted_length = 0
            collected_chunks = []
            truncated = False
            yielded_any = False
            try:
                with self._genai_lock:
                    genai.configure(api_key=api_key)
                    model_with_system = self._build_model(
                        generation_config=chat_generation_config,
                        system_instruction=system_instruction,
                    )
                    chat = model_with_system.start_chat(history=gemini_history)
                    response_stream = chat.send_message(message, stream=True)

                    for chunk in response_stream:
                        try:
                            if not chunk.text:
                                continue
                            yielded_any = True
                            collected_chunks.append(chunk.text)
                            emitted_length += len(chunk.text)
                            yield chunk.text
                            if emitted_length >= 3500:
                                truncated = True
                                yield "\n\nBạn muốn mình tiếp tục gợi ý thêm không? Mình có thể chia nhỏ theo từng nhóm địa điểm."
                                break
                        except Exception:
                            pass

                if not truncated:
                    full_text = ''.join(collected_chunks)
                    image_gallery = self._build_image_gallery_markdown(f"{message}\n{full_text}")
                    if image_gallery:
                        yield image_gallery
                current_app.logger.info(
                    f"Gemini chat_stream succeeded with {key_label}"
                )
                return
            except Exception as e:
                last_error = e
                should_try_next = self._should_try_next_api_key(e)
                current_app.logger.warning(
                    f"Gemini chat_stream failed with {key_label}: {str(e)}"
                )
                if yielded_any:
                    yield "\n\nXin lỗi, kết nối tới AI bị gián đoạn. Bạn hãy gửi lại câu hỏi giúp mình nhé."
                    return
                if not should_try_next:
                    break

        try:
            if last_error:
                raise last_error
            raise ValueError("No Gemini API key available")
        except Exception as e:
            current_app.logger.error(f"Gemini chat stream error: {str(e)}")
            yield f"Xin lỗi, đã có lỗi: {str(e)}"

    def generate_itinerary(self, preferences: Dict) -> Dict:
        """Generate travel itinerary based on preferences"""
        try:
            prompt = self._build_itinerary_prompt(preferences)

            response = self._run_with_api_keys(
                'generate_itinerary',
                lambda _api_key: self._build_model().generate_content(prompt)
            )
            
            try:
                itinerary_data = self._parse_json_response(response.text)
            except:
                itinerary_data = {
                    'title': 'Lịch trình du lịch',
                    'description': response.text,
                    'days': []
                }
            
            return {
                'success': True,
                'itinerary': itinerary_data,
                'model': self.model_name
            }
            
        except Exception as e:
            current_app.logger.error(f"Gemini itinerary generation error: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def suggest_places(self, criteria: dict, available_places: list[dict]) -> dict:
        try:
            prompt = self._build_suggestion_prompt(criteria, available_places)

            response = self._run_with_api_keys(
                'suggest_places',
                lambda _api_key: self._build_model().generate_content(prompt)
            )
            
            try:
                suggestions = self._parse_json_response(response.text)
            except:
                suggestions = {
                    'places': [],
                    'explanation': response.text
                }
            
            return {
                'success': True,
                'suggestions': suggestions,
                'model': self.model_name
            }
            
        except Exception as e:
            current_app.logger.error(f"Gemini suggestion error: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def estimate_cost(self, itinerary_data: dict) -> dict:
        try:
            prompt = self._build_cost_estimation_prompt(itinerary_data)

            response = self._run_with_api_keys(
                'estimate_cost',
                lambda _api_key: self._build_model().generate_content(prompt)
            )
            
            try:
                cost_data = self._parse_json_response(response.text)
            except:
                cost_data = {
                    'total': 0,
                    'breakdown': {},
                    'explanation': response.text
                }
            
            return {
                'success': True,
                'cost': cost_data,
                'model': self.model_name
            }
            
        except Exception as e:
            current_app.logger.error(f"Gemini cost estimation error: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def _build_tourism_system_prompt(self, message: str = '') -> str:
        """Build system prompt for tourism assistant (all image data loaded from DB)."""
        # ── Dynamic image list from DB ────────────────────────────────────
        image_list_str = self._get_relevant_image_markdown(message)
        image_rules = "- Không chèn hình ảnh nếu không thực sự cần.\n- Tối đa 2 hình trong một câu trả lời.\n"
        if image_list_str:
            image_rules += "- Khi người dùng hỏi về một địa điểm cụ thể và có ảnh khớp, hãy luôn chèn ít nhất 1 ảnh minh họa đúng địa điểm đó ở cuối câu trả lời.\n"
            image_rules += "- Nếu bạn gợi ý nhiều địa điểm, ưu tiên ảnh của địa điểm quan trọng nhất hoặc địa điểm người dùng hỏi trực tiếp.\n"
            image_rules += "- Nếu dùng ảnh, chỉ được dùng CHÍNH XÁC cú pháp Markdown sau (KHÔNG tự bịa URL):\n" + image_list_str
        else:
            image_rules += "- Hiện tại không có ảnh phù hợp rõ ràng với câu hỏi, nên ưu tiên trả lời bằng chữ.\n"

        return f"""Bạn là trợ lý du lịch thông minh tên là "Khánh Hòa Travel AI", chuyên tư vấn du lịch tại tỉnh Khánh Hòa và Ninh Thuận, Việt Nam.

=== QUY TẮC ĐỊNH DẠNG BẮT BUỘC ===
- TUYỆT ĐỐI KHÔNG dùng dấu thăng (#, ##, ###) làm tiêu đề.
- TUYỆT ĐỐI KHÔNG dùng dấu sao đôi (**text**) để bôi đậm.
- CHỈ dùng: văn bản thuần, xuống dòng, dấu gạch đầu dòng (-), và số thứ tự (1. 2. 3.).
- Khi liệt kê địa điểm, mỗi địa điểm viết trên một dòng riêng, có gạch đầu dòng.

=== QUY TẮC CHÈN ẢNH BẮT BUỘC ===
- Sau khi giới thiệu một địa điểm, BẮT BUỘC chèn ảnh minh họa ngay bên dưới nếu có trong danh sách.
- Dùng ĐÚNG cú pháp Markdown: ![tên địa điểm](/api/ai/img/ID)
- Chỉ dùng ID có trong danh sách ảnh bên dưới, TUYỆT ĐỐI KHÔNG tự bịa ID.
- Mỗi địa điểm chèn 1 ảnh, đặt trên một dòng riêng ngay sau phần mô tả.
- Ví dụ định dạng đúng:

- Vịnh Vĩnh Hy: vịnh biển đẹp hoang sơ, nước trong xanh, lý tưởng cho nghỉ dưỡng yên tĩnh.
![Vịnh Vĩnh Hy](/api/ai/img/38)

=== CHIẾN LƯỢC TƯ VẤN THÔNG MINH ===

BƯỚC 1 - THĂM DÒ SỞ THÍCH (quan trọng nhất):
Khi khách hỏi chung chung về du lịch một địa điểm (ví dụ: "tư vấn địa điểm ở Khánh Hòa", "muốn đi Nha Trang", "du lịch Khánh Hòa"), bạn PHẢI hỏi thăm dò trước khi tư vấn. Hỏi theo mẫu sau:

"Tuyệt vời! Khánh Hòa có rất nhiều loại hình du lịch thú vị. Để tư vấn phù hợp nhất cho bạn, mình cần hỏi thêm một chút nhé:

Bạn muốn loại hình du lịch nào?
- Du lịch biển (tắm biển, lặn san hô, thể thao nước)
- Nghỉ dưỡng (resort cao cấp, spa, thư giãn)
- Sinh thái / Thiên nhiên (rừng, thác, núi)
- Phượt / Khám phá (đèo, làng chài, vùng xa)
- Cắm trại / Glamping
- Ẩm thực và văn hóa địa phương
- Kết hợp nhiều loại hình

Ngoài ra, bạn đi mấy ngày và đi cùng ai (gia đình, bạn bè, cặp đôi, hay đi một mình)?"

BƯỚC 2 - TƯ VẤN CHI TIẾT SAU KHI BIẾT SỞ THÍCH:
Sau khi khách trả lời, hãy tư vấn địa điểm phù hợp theo từng loại hình:

Nếu khách chọn NGHỈ DƯỠNG:
- Liệt kê 5-8 resort/khách sạn cao cấp tại Nha Trang, Cam Ranh
- Mỗi địa điểm ghi rõ: tên, vị trí, mức giá ước tính/đêm, điểm nổi bật
- Chèn ảnh minh họa nếu có trong danh sách ảnh
- Gợi ý thêm: spa nào ngon, nhà hàng view đẹp, hoạt động tại resort

Nếu khách chọn DU LỊCH BIỂN:
- Liệt kê các bãi biển đẹp: Bãi Dài, Bãi Trũ, Dốc Lết, Bãi Tiên, Vân Phong...
- Gợi ý hoạt động: lặn ngắm san hô, chèo kayak, jet-ski, câu cá
- Đảo nào đáng đi: Hòn Mun, Hòn Tằm, Hòn Miễu...
- Chèn ảnh minh họa phù hợp

Nếu khách chọn SINH THÁI / THIÊN NHIÊN:
- Thác Yangbay, Hồ Suối Dầu, rừng quốc gia Hòn Bà
- Các tour sinh thái cộng đồng
- Chèn ảnh minh họa phù hợp

Nếu khách chọn PHƯỢT / KHÁM PHÁ:
- Đèo Cả, Đèo Rọ Tượng, Vạn Ninh, làng chài Đầm Môn
- Cung đường ven biển đẹp
- Chèn ảnh minh họa phù hợp

Nếu khách chọn CẮM TRẠI / GLAMPING:
- Bãi Dài Cam Ranh, Vân Phong, Dốc Lết
- Các điểm glamping đang hot
- Chèn ảnh minh họa phù hợp

Nếu khách chọn ẨM THỰC:
- Bún sứa, bánh canh chả cá, nem Ninh Hòa, yến sào
- Chợ đêm, phố ẩm thực tại Nha Trang
- Nhà hàng hải sản tươi sống nên thử

BƯỚC 3 - GỢI Ý THÊM SAU KHI TƯ VẤN:
Sau khi đã tư vấn địa điểm, hỏi thêm:
"Bạn có muốn mình lên lịch trình chi tiết theo ngày không? Chỉ cần cho mình biết bạn có bao nhiêu ngày và ngân sách dự kiến là mình sẽ lên kế hoạch cụ thể cho bạn nhé!"

=== XỬ LÝ CÁC TÌNH HUỐNG ĐẶC BIỆT ===

Khi khách hỏi về CHI PHÍ:
- Luôn đưa ra khoảng giá (thấp - trung bình - cao cấp)
- Ước tính tổng chi phí cho chuyến đi theo số ngày
- Gợi ý cách tiết kiệm

Khi khách hỏi về THỜI ĐIỂM ĐI:
- Khánh Hòa đẹp nhất tháng 1-8 (mùa khô)
- Tránh tháng 9-12 (mùa mưa bão)
- Tháng 6-8 đông khách, nên đặt trước

Khi khách hỏi về DI CHUYỂN:
- Từ TP.HCM: máy bay 1 tiếng, tàu 8-10 tiếng, xe khách 10-12 tiếng
- Tại Nha Trang: thuê xe máy 100-150k/ngày, taxi, Grab

Khi khách hỏi không liên quan đến du lịch Khánh Hòa / Ninh Thuận:
- Lịch sự từ chối và nhắc lại chuyên môn của bạn
- Gợi ý câu hỏi liên quan đến du lịch

=== DANH SÁCH ẢNH (dùng đúng ID, không tự bịa) ===
{image_list_str if image_list_str else "- (Chưa có ảnh nào trong thư mục)"}

=== KIẾN THỨC CHUYÊN MÔN (ƯU TIÊN CAO NHẤT) ===
Dữ liệu bên dưới là thông tin thực tế về địa điểm, khách sạn, ẩm thực tại Khánh Hòa và Ninh Thuận. Bạn PHẢI ưu tiên dùng thông tin này khi tư vấn:

{self.knowledge_base}"""

    def _build_itinerary_prompt(self, preferences: dict) -> str:
        """Build prompt for itinerary generation"""
        duration = preferences.get('duration', 3)
        budget = preferences.get('budget', 'medium')
        interests = preferences.get('interests', [])
        location = preferences.get('location', 'Khánh Hòa')
        
        budget_map = {
            'low': 'tiết kiệm (dưới 500k/ngày)',
            'medium': 'trung bình (500k - 1.5 triệu/ngày)',
            'high': 'cao cấp (trên 1.5 triệu/ngày)'
        }
        budget_label = budget_map.get(budget, budget)

        prompt = f"""Hãy tạo một lịch trình du lịch chi tiết với các thông tin sau:

Thông tin chuyến đi:
- Địa điểm: {location}
- Thời gian: {duration} ngày
- Ngân sách: {budget_label}
- Sở thích: {', '.join(interests) if interests else 'Tổng hợp'}

Yêu cầu:
1. Lịch trình theo từng ngày với thời gian cụ thể (sáng/trưa/chiều/tối)
2. Gợi ý địa điểm tham quan, ăn uống, nghỉ ngơi phù hợp ngân sách
3. Ước tính chi phí từng hoạt động (đơn vị: VNĐ)
4. Lời khuyên về di chuyển giữa các điểm
5. Tips và lưu ý quan trọng

Trả về kết quả dưới dạng JSON với cấu trúc:
{{
  "title": "Tên lịch trình",
  "description": "Mô tả tổng quan",
  "duration_days": {duration},
  "estimated_cost": 0,
  "days": [
    {{
      "day": 1,
      "title": "Tiêu đề ngày 1",
      "activities": [
        {{
          "time": "08:00",
          "activity": "Tên hoạt động",
          "location": "Địa điểm",
          "description": "Mô tả chi tiết",
          "estimated_cost": 0,
          "duration": "2 giờ"
        }}
      ]
    }}
  ],
  "tips": ["Lời khuyên 1", "Lời khuyên 2"]
}}"""
        
        return prompt

    def _build_suggestion_prompt(self, criteria: dict, places: list[dict]) -> str:
        """Build prompt for place suggestions"""
        category = criteria.get('category', 'all')
        budget = criteria.get('budget', 'medium')
        interests = criteria.get('interests', [])
        
        places_json = json.dumps(places, ensure_ascii=False, indent=2)
        
        prompt = f"""Dựa trên danh sách địa điểm sau và tiêu chí của khách, hãy gợi ý 5-10 địa điểm phù hợp nhất:

Tiêu chí:
- Loại hình: {category}
- Ngân sách: {budget}
- Sở thích: {', '.join(interests) if interests else 'Tổng hợp'}

Danh sách địa điểm:
{places_json}

Trả về JSON với cấu trúc:
{{
  "recommendations": [
    {{
      "place_id": 1,
      "name": "Tên địa điểm",
      "reason": "Lý do gợi ý cụ thể",
      "rating": 4.5,
      "estimated_cost": 0
    }}
  ],
  "explanation": "Giải thích tổng quan về các gợi ý"
}}"""
        
        return prompt
    
    def _build_cost_estimation_prompt(self, itinerary: dict) -> str:
        """Build prompt for cost estimation"""
        itinerary_json = json.dumps(itinerary, ensure_ascii=False, indent=2)
        
        prompt = f"""Ước tính chi phí chi tiết cho lịch trình du lịch sau tại Khánh Hòa / Ninh Thuận, Việt Nam:

{itinerary_json}

Trả về JSON với cấu trúc:
{{
  "total": 0,
  "breakdown": {{
    "accommodation": 0,
    "food": 0,
    "transportation": 0,
    "activities": 0,
    "shopping": 0,
    "other": 0
  }},
  "daily_average": 0,
  "currency": "VND",
  "notes": ["Ghi chú về chi phí"],
  "tips": ["Tips tiết kiệm chi phí"]
}}

Lưu ý: Tính toán dựa trên giá cả thực tế tại Khánh Hòa, Việt Nam năm 2024-2025."""
        
        return prompt
    
    def _parse_json_response(self, text: str) -> dict:
        """Parse JSON from AI response"""
        if '```json' in text:
            start = text.find('```json') + 7
            end = text.find('```', start)
            text = text[start:end].strip()
        elif '```' in text:
            start = text.find('```') + 3
            end = text.find('```', start)
            text = text[start:end].strip()
        
        return json.loads(text)


# Singleton instance
_ai_service = None

def get_ai_service() -> GeminiAIService:
    """Get AI service instance"""
    global _ai_service
    if _ai_service is None:
        try:
            _ai_service = GeminiAIService()
        except Exception as e:
            _ai_service = None  # Không cache instance lỗi
            raise
    return _ai_service


if __name__ == "__main__": 
    get_ai_service()
