from datetime import datetime
from typing import Dict, List, Optional

from flask import current_app

from app import db
from app.models.interaction import SavedItinerary
from app.models.location import Location
from app.services.ai import get_ai_service


class ItineraryService:
    def __init__(self):
        self.ai_service = None

    def _get_ai_service(self):
        if self.ai_service is None:
            self.ai_service = get_ai_service()
        return self.ai_service

    def generate_smart_itinerary(self, preferences: Dict, selected_places: Optional[List[int]] = None) -> Dict:
        try:
            places_data = []
            if selected_places:
                locations = Location.query.filter(Location.id.in_(selected_places)).all()
                places_data = [self._place_to_dict(location) for location in locations]

            enhanced_preferences = preferences.copy()
            if places_data:
                enhanced_preferences['selected_places'] = places_data

            ai_service = self._get_ai_service()
            result = ai_service.generate_itinerary(enhanced_preferences)
            if not result['success']:
                return {'success': False, 'error': result.get('error', 'Không thể tạo lịch trình')}

            itinerary = self._enhance_itinerary(result['itinerary'], preferences, places_data)
            return {'success': True, 'itinerary': itinerary}
        except Exception as e:
            current_app.logger.error(f'Error generating itinerary: {str(e)}')
            return {'success': False, 'error': str(e)}

    def save_itinerary(self, user_id: str, itinerary_data: Dict) -> Dict:
        try:
            itinerary = SavedItinerary(
                user_id=user_id,
                title=itinerary_data.get('title', 'Lịch trình du lịch'),
                total_budget=itinerary_data.get('estimated_cost', 0),
                nodes=itinerary_data.get('days', []),
            )
            db.session.add(itinerary)
            db.session.commit()
            return {'success': True, 'itinerary_id': itinerary.id, 'message': 'Lưu lịch trình thành công'}
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f'Error saving itinerary: {str(e)}')
            return {'success': False, 'error': str(e)}

    def get_user_itineraries(self, user_id: str, limit: int = 20) -> List[Dict]:
        try:
            itineraries = SavedItinerary.query.filter_by(user_id=user_id).order_by(SavedItinerary.created_at.desc()).limit(limit).all()
            return [itinerary.to_dict() for itinerary in itineraries]
        except Exception as e:
            current_app.logger.error(f'Error getting user itineraries: {str(e)}')
            return []

    def get_itinerary(self, itinerary_id: int, user_id: Optional[str] = None) -> Optional[Dict]:
        try:
            query = SavedItinerary.query.filter_by(id=itinerary_id)
            if user_id:
                query = query.filter_by(user_id=user_id)
            itinerary = query.first()
            return itinerary.to_dict() if itinerary else None
        except Exception as e:
            current_app.logger.error(f'Error getting itinerary: {str(e)}')
            return None

    def update_itinerary(self, itinerary_id: int, user_id: str, updates: Dict) -> Dict:
        try:
            itinerary = SavedItinerary.query.filter_by(id=itinerary_id, user_id=user_id).first()
            if not itinerary:
                return {'success': False, 'error': 'Không tìm thấy lịch trình'}

            if 'title' in updates:
                itinerary.title = updates['title']
            if 'itinerary_data' in updates:
                itinerary.nodes = updates['itinerary_data'].get('days', [])
            if 'nodes' in updates:
                itinerary.nodes = updates['nodes']
            if 'total_budget' in updates:
                itinerary.total_budget = updates['total_budget']
            itinerary.updated_at = datetime.utcnow()
            db.session.commit()
            return {'success': True, 'message': 'Cập nhật lịch trình thành công'}
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f'Error updating itinerary: {str(e)}')
            return {'success': False, 'error': str(e)}

    def delete_itinerary(self, itinerary_id: int, user_id: str) -> Dict:
        try:
            itinerary = SavedItinerary.query.filter_by(id=itinerary_id, user_id=user_id).first()
            if not itinerary:
                return {'success': False, 'error': 'Không tìm thấy lịch trình'}
            db.session.delete(itinerary)
            db.session.commit()
            return {'success': True, 'message': 'Xóa lịch trình thành công'}
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f'Error deleting itinerary: {str(e)}')
            return {'success': False, 'error': str(e)}

    def _enhance_itinerary(self, itinerary: Dict, preferences: Dict, places: List[Dict]) -> Dict:
        itinerary['preferences'] = preferences
        itinerary['created_at'] = datetime.utcnow().isoformat()

        if places and 'days' in itinerary:
            place_map = {place['name']: place for place in places}
            for day in itinerary['days']:
                for activity in day.get('activities', []):
                    location = activity.get('location', '')
                    for place_name, place_data in place_map.items():
                        if place_name in location:
                            activity['place_id'] = place_data.get('id')
                            activity['place_category'] = place_data.get('category')
                            if 'map_url' in place_data:
                                activity['map_url'] = place_data['map_url']
                            break
                    if 'map_url' not in activity and activity.get('location'):
                        from urllib.parse import quote
                        activity['map_url'] = f'https://www.google.com/maps/search/?api=1&query={quote(activity["location"])}'

        if 'estimated_cost' not in itinerary or itinerary['estimated_cost'] == 0:
            total_cost = 0
            for day in itinerary.get('days', []):
                for activity in day.get('activities', []):
                    total_cost += activity.get('estimated_cost', 0)
            itinerary['estimated_cost'] = total_cost
        return itinerary

    def _place_to_dict(self, loc: Location) -> Dict:
        return {
            'id': loc.id,
            'name': loc.name,
            'description': loc.description,
            'category_id': loc.category_id,
            'category': loc.category.to_dict() if loc.category else None,
            'address': loc.address,
            'map_url': loc.map_url,
        }


_itinerary_service = None


def get_itinerary_service() -> ItineraryService:
    global _itinerary_service
    if _itinerary_service is None:
        _itinerary_service = ItineraryService()
    return _itinerary_service
