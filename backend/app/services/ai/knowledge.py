import os
from flask import current_app
from typing import List, Dict, Optional
from app.utils.chatbot_images import (
    find_relevant_chatbot_images,
    normalize_chatbot_text,
)
from app.utils.db_knowledge import (
    build_db_knowledge_text,
    get_db_image_list_for_prompt,
    get_db_knowledge_stats,
)

class KnowledgeManager:
    def __init__(self):
        self.knowledge_base = ""
        self.knowledge_sections = []
        self._db_image_list: List[Dict] = []
        self.load_knowledge_base()

    def load_knowledge_base(self):
        """Load knowledge base from static files and database."""
        file_knowledge = ''
        candidates = [
            os.path.join(current_app.root_path, 'data', 'data_chat.txt'),
            os.path.join(os.path.dirname(current_app.root_path), 'app', 'data', 'data_chat.txt'),
            os.path.normpath(os.path.join(os.path.abspath(os.path.dirname(__file__)), '..', '..', 'data', 'data_chat.txt')),
        ]
        for data_path in candidates:
            if os.path.exists(data_path):
                try:
                    with open(data_path, 'r', encoding='utf-8') as f:
                        file_knowledge = f.read()
                    current_app.logger.info(f"Loaded static knowledge: {data_path}")
                    break
                except Exception as e:
                    current_app.logger.error(f"Error reading {data_path}: {e}")

        db_knowledge = ''
        try:
            db_knowledge = build_db_knowledge_text()
        except Exception as e:
            current_app.logger.warning(f"DB knowledge load failed: {e}")

        self.knowledge_base = '\n\n'.join(filter(None, [file_knowledge, db_knowledge]))
        self.knowledge_sections = [s.strip() for s in self.knowledge_base.split('\n\n') if s.strip()]

        try:
            self._db_image_list = get_db_image_list_for_prompt(message='', max_images=100)
        except Exception as e:
            current_app.logger.warning(f"DB image list load failed: {e}")
            self._db_image_list = []

    def refresh(self) -> Dict:
        self.load_knowledge_base()
        stats = get_db_knowledge_stats()
        return {'success': True, 'stats': stats}

    def extract_keywords(self, text: str) -> List[str]:
        normalized = normalize_chatbot_text(text)
        stopwords = {
            'toi', 'tu', 'van', 'cho', 'xin', 'hay', 'o', 'di', 'nhe', 'la', 'va', 'nhung', 'cac',
            'mot', 'ngay', 'dem', 'giup', 'minh', 'du', 'lich', 'dia', 'diem', 'nao', 'khong', 'duoc',
            'voi', 've', 'tai', 'den', 'tham', 'quan', 'goi', 'y', 'nhat', 'nhieu', 'it', 'gan', 'xa',
            'an', 'uong', 'luu', 'tru', 'chi', 'phi'
        }
        tokens = [t for t in normalized.split() if len(t) > 2 and t not in stopwords]
        seen = set()
        return [t for t in tokens if not (t in seen or seen.add(t))][:8]

    def get_relevant_knowledge(self, message: str, max_sections: int = 4, max_chars: int = 4500) -> str:
        if not self.knowledge_sections:
            return self.knowledge_base[:max_chars]

        keywords = self.extract_keywords(message)
        if not keywords:
            return '\n\n'.join(self.knowledge_sections[:max_sections])[:max_chars]

        scored = []
        for idx, section in enumerate(self.knowledge_sections):
            norm_sec = normalize_chatbot_text(section)
            score = sum(norm_sec.count(kw) for kw in keywords)
            if score > 0:
                scored.append((score, idx, section))

        if not scored:
            return '\n\n'.join(self.knowledge_sections[:max_sections])[:max_chars]

        scored.sort(key=lambda x: (-x[0], x[1]))
        selected, current_len = [], 0
        for _, _, section in scored[:max_sections * 2]:
            clean = section.strip()
            if not clean: continue
            if selected and current_len + len(clean) + 2 > max_chars: break
            selected.append(clean)
            current_len += len(clean) + 2
            if len(selected) >= max_sections: break

        return '\n\n'.join(selected)[:max_chars]

    def get_relevant_images(self, message: str, max_images: int = 4) -> List[Dict]:
        if self._db_image_list:
            keywords = self.extract_keywords(message)
            if keywords:
                scored = []
                for img in self._db_image_list:
                    score = sum(normalize_chatbot_text(img['name']).count(kw) for kw in keywords)
                    if score > 0: scored.append((score, img))
                scored.sort(key=lambda x: -x[0])
                if scored: return [img for _, img in scored[:max_images]]
            return self._db_image_list[:max_images]

        try:
            fs_images = find_relevant_chatbot_images(message, keywords=self.extract_keywords(message), max_images=max_images)
            return [{'name': i['display_name'], 'image_url': f"/api/ai/img/{i['slug']}"} for i in fs_images]
        except Exception as e:
            current_app.logger.error(f"FS image list error: {e}")
            return []
