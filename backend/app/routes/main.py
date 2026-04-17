import json

from flask import Blueprint, current_app, jsonify, send_from_directory
from sqlalchemy import func

from app import cache, db

bp = Blueprint('main', __name__)


@bp.route('/api/stats')
def get_stats():
    try:
        cached_payload = cache.get('main:stats')
        if cached_payload:
            try:
                return jsonify(json.loads(cached_payload))
            except (TypeError, json.JSONDecodeError):
                pass

        from app.models.interaction import SavedItinerary
        from app.models.location import Location
        from app.models.user import User

        stats = {
            'total_places': Location.query.filter(Location.status == 'ACTIVE').count(),
            'total_users': User.query.count(),
            'total_itineraries': SavedItinerary.query.count(),
            'categories': {}
        }

        categories = db.session.query(
            Location.category_id,
            func.count(Location.id)
        ).filter(Location.status == 'ACTIVE').group_by(Location.category_id).all()

        for category_id, count in categories:
            stats['categories'][str(category_id)] = count

        cache.set('main:stats', json.dumps(stats, ensure_ascii=False), ex=120)
        return jsonify(stats)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/health')
def health_check():
    return jsonify({
        'status': 'healthy',
        'service': 'Tourism API',
        'version': '1.0.0'
    })


@bp.route('/uploads/<path:filename>')
def serve_upload(filename):
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)


@bp.route('/')
def index():
    return jsonify({
        'message': 'Tourism API running',
        'health': '/api/health'
    })


@bp.route('/api/send-mail', methods=['POST'])
def send_mail():
    pass
