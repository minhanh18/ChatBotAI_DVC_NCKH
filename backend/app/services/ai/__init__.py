from .service import GeminiAIService

_ai_service = None

def get_ai_service() -> GeminiAIService:
    """Get AI service instance (singleton)"""
    global _ai_service
    if _ai_service is None:
        try:
            _ai_service = GeminiAIService()
        except Exception:
            _ai_service = None
            raise
    return _ai_service

__all__ = ['GeminiAIService', 'get_ai_service']
