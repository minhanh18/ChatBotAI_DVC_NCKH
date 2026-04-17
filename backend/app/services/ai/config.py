import google.generativeai as genai
from flask import current_app
import re, threading
from typing import List, Dict, Optional

class AIConfig:
    def __init__(self):
        self.api_keys: List[str] = []
        self._rotation_lock = threading.Lock()
        self._genai_lock = threading.RLock()
        self._next_api_key_index = 0
        self.model_name = ""
        self.generation_config = {}
        self.safety_settings = []
    
    def configure(self):
        """Configure Gemini API using Flask current_app config."""
        try:
            self.api_keys = self._parse_api_keys()
            if not self.api_keys:
                raise ValueError("GEMINI_API_KEY or GEMINI_API_KEYS not configured")

            configured_model = current_app.config.get('GEMINI_MODEL') or 'models/gemini-2.5-flash'
            self.model_name = configured_model if configured_model.startswith('models/') else f'models/{configured_model}'
            
            self.generation_config = {
                "temperature": current_app.config.get('AI_TEMPERATURE', 0.8),
                "top_p": 0.95,
                "top_k": 40,
                "max_output_tokens": current_app.config.get('AI_MAX_TOKENS', 8192),
            }
            
            self.safety_settings = [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_ONLY_HIGH"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ]
            
            with self._genai_lock:
                genai.configure(api_key=self.api_keys[0])
                
        except Exception as e:
            current_app.logger.error(f"Error configuring Gemini: {str(e)}")
            raise

    def _parse_api_keys(self) -> List[str]:
        raw_values = []
        keys_value = current_app.config.get('GEMINI_API_KEYS')
        single_value = current_app.config.get('GEMINI_API_KEY')
        if keys_value: raw_values.append(keys_value)
        if single_value: raw_values.append(single_value)

        parsed = []
        seen = set()
        for raw_value in raw_values:
            for item in re.split(r'[\r\n,;]+', str(raw_value)):
                key = item.strip()
                if not key or key in seen: continue
                seen.add(key)
                parsed.append(key)
        return parsed

    def get_api_key_order(self) -> List[str]:
        if not self.api_keys: return []
        with self._rotation_lock:
            start = self._next_api_key_index % len(self.api_keys)
            self._next_api_key_index = (start + 1) % len(self.api_keys)
            return self.api_keys[start:] + self.api_keys[:start]

    def get_key_label(self, api_key: str) -> str:
        try:
            index = self.api_keys.index(api_key) + 1
            mask = f"{api_key[:4]}...{api_key[-4:]}" if len(api_key) > 8 else "*" * len(api_key)
            return f'key#{index}/{len(self.api_keys)} ({mask})'
        except ValueError:
            return "unknown_key"

    def should_retry(self, error: Exception) -> bool:
        message = str(error).lower()
        retryable_markers = (
            'quota', 'rate limit', 'resource_exhausted', '429', '503', '500',
            'deadline exceeded', 'timed out', 'timeout', 'temporarily unavailable',
            'service unavailable', 'internal error', 'api key', 'permission denied',
            'unauthenticated', 'invalid api key', 'authentication', 'connection reset',
            'connection aborted', 'unavailable',
        )
        return any(marker in message for marker in retryable_markers)

    def build_model(self, gen_config: Optional[Dict] = None, system_instruction: Optional[str] = None):
        return genai.GenerativeModel(
            model_name=self.model_name,
            generation_config=gen_config or self.generation_config,
            safety_settings=self.safety_settings,
            system_instruction=system_instruction,
        )

    def run_with_retry(self, action_name: str, operation):
        ordered_keys = self.get_api_key_order()
        last_error = None

        for api_key in ordered_keys:
            label = self.get_key_label(api_key)
            try:
                with self._genai_lock:
                    genai.configure(api_key=api_key)
                    result = operation(api_key)
                current_app.logger.info(f"Gemini {action_name} succeeded with {label}")
                return result
            except Exception as e:
                last_error = e
                if not self.should_retry(e):
                    current_app.logger.error(f"Gemini {action_name} fatal error: {str(e)}")
                    raise e
                current_app.logger.warning(f"Gemini {action_name} transient failure with {label}, retrying: {str(e)}")

        if last_error: raise last_error
        raise ValueError("No Gemini API key available")
