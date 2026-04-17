import json, re
from flask import current_app
from typing import List, Dict, Optional
from .config import AIConfig
from .knowledge import KnowledgeManager
from .prompts import PromptBuilder

class GeminiAIService:
    def __init__(self):
        self.config = AIConfig()
        self.km = KnowledgeManager()
        self.pb = PromptBuilder(self.km)
        self.config.configure()

    def refresh_knowledge(self) -> Dict:
        return self.km.refresh()

    def chat(self, message: str, context: Optional[Dict] = None, 
             chat_history: Optional[List[Dict]] = None) -> Dict:
        try:
            sys_inst = self.pb.build_system_instruction(message, context)
            gemini_history = self._format_history(chat_history)
            
            def op(_api_key):
                model = self.config.build_model(system_instruction=sys_inst)
                chat = model.start_chat(history=gemini_history)
                return chat.send_message(message).text

            response_text = self.config.run_with_retry('chat', op)
            response_text += self.pb.build_image_gallery(f"{message}\n{response_text}")
            
            return {
                'success': True,
                'response': response_text,
                'model': self.config.model_name,
                'finish_reason': 'stop'
            }
        except Exception as e:
            current_app.logger.error(f"Chat error: {e}")
            return {'success': False, 'error': str(e), 'response': 'Xin lỗi, tôi đang gặp sự cố.'}

    def chat_stream(self, message: str, context: Dict = None, chat_history: List[Dict] = None):
        sys_inst = self.pb.build_system_instruction(message, context)
        gemini_history = self._format_history(chat_history)
        
        ordered_keys = self.config.get_api_key_order()
        for api_key in ordered_keys:
            label = self.config.get_key_label(api_key)
            emitted, chunks, yielded_any = 0, [], False
            try:
                import google.generativeai as genai
                with self.config._genai_lock:
                    genai.configure(api_key=api_key)
                    model = self.config.build_model(system_instruction=sys_inst)
                    chat = model.start_chat(history=gemini_history)
                    stream = chat.send_message(message, stream=True)
                    for chunk in stream:
                        if not chunk.text: continue
                        yielded_any = True
                        chunks.append(chunk.text)
                        emitted += len(chunk.text)
                        yield chunk.text
                        if emitted >= 3500:
                            yield "\n\nTiếp tục gợi ý khác nhé?"
                            break
                if emitted < 3500:
                    gallery = self.pb.build_image_gallery(f"{message}\n{''.join(chunks)}")
                    if gallery: yield gallery
                current_app.logger.info(f"Stream success: {label}")
                return
            except Exception as e:
                if yielded_any: yield "\n\nKết nối gián đoạn. Thử lại sau nhé."; return
                if not self.config.should_retry(e): break
        yield "Lỗi hệ thống. Vui lòng thử lại."

    def generate_itinerary(self, pref: Dict) -> Dict:
        return self._json_op('itinerary', self.pb.build_itinerary_prompt(pref))

    def suggest_places(self, criteria: Dict, places: List[Dict]) -> Dict:
        return self._json_op('suggestions', self.pb.build_suggestion_prompt(criteria, places))

    def estimate_cost(self, itinerary: Dict) -> Dict:
        return self._json_op('cost', self.pb.build_cost_estimation_prompt(itinerary))

    def _json_op(self, name: str, prompt: str) -> Dict:
        try:
            resp = self.config.run_with_retry(name, lambda _: self.config.build_model().generate_content(prompt))
            data = self._parse_json(resp.text)
            return {'success': True, name: data, 'model': self.config.model_name}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def _format_history(self, history: Optional[List[Dict]]) -> List[Dict]:
        if not history: return []
        return [{'role': 'user' if m['role'] == 'user' else 'model', 'parts': [m['content']]} for m in history[-10:]]

    def _parse_json(self, text: str) -> Dict:
        """Parse JSON from AI response, handling markdown blocks."""
        if '```json' in text:
            match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
            if match: return json.loads(match.group(1).strip())
        elif '```' in text:
            match = re.search(r'```\s*(.*?)\s*```', text, re.DOTALL)
            if match: return json.loads(match.group(1).strip())
        return json.loads(text.strip())
