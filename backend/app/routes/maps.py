from flask import Blueprint, request, jsonify
from app.services.maps_service import get_maps_service

bp = Blueprint('maps', __name__, url_prefix='/api/maps')


@bp.route('/urls', methods=['POST'])
def get_map_urls():
    try:
        data = request.get_json()
        query = data.get('query') or data.get('name')
        
        if not query:
            return jsonify({'error': 'Thiếu thông tin tìm kiếm'}), 400
        
        maps_service = get_maps_service()
        
        return jsonify({
            'success': True,
            'maps_url': maps_service.get_maps_url(query),
            'directions_url': maps_service.get_directions_url(query),
            'navigation_url': maps_service.get_navigation_url(query)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/directions-link', methods=['POST'])
def get_directions_link():
    try:
        data = request.get_json()
        
        destination = data.get('destination') or data.get('address')
        origin = data.get('origin')
        mode = data.get('mode', 'driving')
        
        if not destination:
            return jsonify({'error': 'Thiếu điểm đến'}), 400
        
        maps_service = get_maps_service()
        url = maps_service.get_directions_url(destination, origin, mode)
        
        return jsonify({
            'success': True,
            'url': url
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500