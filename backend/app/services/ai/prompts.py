import json
from typing import List, Dict, Optional

class PromptBuilder:
    def __init__(self, knowledge_manager):
        self.km = knowledge_manager

    def build_system_instruction(self, message: str = '', context: Optional[Dict] = None) -> str:
        system = self.build_tourism_system_prompt(message)
        if context:
            system += f"\n\nThông tin người dùng:\n{json.dumps(context, ensure_ascii=False, indent=2)}"
        return system

    def build_tourism_system_prompt(self, message: str = '') -> str:
        images = self.km.get_relevant_images(message)
        image_list_str = '\n'.join([f"- `![{i['name']}]({i['image_url']})` — {i['name']}" for i in images])
        
        image_rules = "- Tối đa 2 hình/câu trả lời. Không lạm dụng.\n"
        if image_list_str:
            image_rules += "- Ưu tiên chèn ảnh đúng địa điểm người dùng hỏi.\n"
            image_rules += "- Dùng CHÍNH XÁC cú pháp Markdown: ![tên](url)\n" + image_list_str
        else:
            image_rules += "- Hiện không có ảnh khớp rõ ràng, ưu tiên trả lời chữ.\n"

        return f"""Bạn là "Khánh Hòa Travel AI", trợ lý du lịch thông minh cho Khánh Hòa và Ninh Thuận.

=== QUY TẮC ĐỊNH DẠNG ===
- KHÔNG dùng #, ##, ### cho tiêu đề. Dùng văn bản thuần, xuống dòng, gạch đầu dòng (-).
- KHÔNG dùng **text** để bôi đậm.

=== QUY TẮC CHÈN ẢNH ===
{image_rules}

=== CHIẾN LƯỢC TƯ VẤN ===
1. THĂM DÒ (Nếu khách hỏi chung): Hỏi về sở thích (biển, nghỉ dưỡng, Trekking...), đi cùng ai, bao lâu?
2. TƯ VẤN CHI TIẾT: Dựa trên sở thích khách đã trả lời.
3. GỢI Ý LÊN LỊCH: Sau khi tư vấn, hỏi khách có muốn lên lịch trình chi tiết không?

=== KIẾN THỨC (QUAN TRỌNG NHẤT) ===
{self.km.get_relevant_knowledge(message)}"""

    def build_itinerary_prompt(self, pref: Dict) -> str:
        return f"""Tạo lịch trình JSON cho: {pref.get('location', 'Khánh Hòa')}, {pref.get('duration', 3)} ngày, ngân sách {pref.get('budget', 'medium')}.
Sở thích: {', '.join(pref.get('interests', []))}
Cấu trúc JSON: title, description, estimated_cost, days (day, title, activities: [time, activity, location, description, estimated_cost, duration]), tips."""

    def build_suggestion_prompt(self, criteria: Dict, places: List[Dict]) -> str:
        return f"""Gợi ý 5-10 địa điểm từ danh sách sau: {json.dumps(places, ensure_ascii=False)}
Tiêu chí: {json.dumps(criteria, ensure_ascii=False)}
Trả về JSON: recommendations: [place_id, name, reason]."""

    def build_cost_estimation_prompt(self, itinerary: Dict) -> str:
        return f"""Ước tính chi phí chi tiết cho lịch trình sau: {json.dumps(itinerary, ensure_ascii=False)}
Trả về JSON: total, breakdown, explanation."""

    def build_image_gallery(self, text: str, max_images: int = 2) -> str:
        if '![' in text: return ''
        images = self.km.get_relevant_images(text, max_images=max_images)
        if not images: return ''
        return '\n\nẢnh minh họa:\n\n' + '\n\n'.join([f"![{img['name']}]({img['image_url']})" for img in images])
