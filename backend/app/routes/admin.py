from flask import Blueprint, jsonify, request
from flask_jwt_extended import current_user

from app.services.admin_service import get_admin_service
from app.utils.auth import jwt_admin_required

bp = Blueprint('admin', __name__, url_prefix='/api/admin')
admin_service = get_admin_service()


@bp.route('/dashboard', methods=['GET'])
@jwt_admin_required
def get_dashboard():
    result, status_code = admin_service.get_dashboard_stats()
    return jsonify(result), status_code


@bp.route('/users', methods=['GET'])
@jwt_admin_required
def get_users():
    params = request.args.to_dict()
    if 'page' in params:
        params['page'] = int(params['page'])
    if 'per_page' in params:
        params['per_page'] = int(params['per_page'])

    result, status_code = admin_service.get_users(params)
    return jsonify(result), status_code


@bp.route('/users/<user_id>/toggle-active', methods=['POST'])
@jwt_admin_required
def toggle_user_active(user_id):
    result, status_code = admin_service.toggle_user_active(user_id, current_user)
    return jsonify(result), status_code


@bp.route('/users/<user_id>/make-admin', methods=['POST'])
@jwt_admin_required
def make_admin(user_id):
    result, status_code = admin_service.make_admin(user_id)
    return jsonify(result), status_code


@bp.route('/users/create-admin', methods=['POST'])
@jwt_admin_required
def create_admin():
    data = request.get_json() or {}
    result, status_code = admin_service.create_admin(data)
    return jsonify(result), status_code


@bp.route('/analytics', methods=['GET'])
@jwt_admin_required
def get_analytics():
    result, status_code = admin_service.get_analytics()
    return jsonify(result), status_code


# ─── Posts Management ─────────────────

@bp.route('/posts', methods=['GET'])
@jwt_admin_required
def get_posts():
    params = request.args.to_dict()
    if 'page' in params:
        params['page'] = int(params['page'])
    if 'per_page' in params:
        params['per_page'] = int(params['per_page'])
    result, status_code = admin_service.get_posts(params)
    return jsonify(result), status_code


@bp.route('/posts/<post_id>', methods=['DELETE'])
@jwt_admin_required
def delete_post(post_id):
    result, status_code = admin_service.delete_post(post_id)
    return jsonify(result), status_code


# ─── Comments Management ────────────

@bp.route('/comments', methods=['GET'])
@jwt_admin_required
def get_comments():
    params = request.args.to_dict()
    if 'page' in params:
        params['page'] = int(params['page'])
    if 'per_page' in params:
        params['per_page'] = int(params['per_page'])
    result, status_code = admin_service.get_comments(params)
    return jsonify(result), status_code


@bp.route('/comments/<comment_id>', methods=['DELETE'])
@jwt_admin_required
def delete_comment(comment_id):
    result, status_code = admin_service.delete_comment(comment_id)
    return jsonify(result), status_code


# ─── Reviews Management ─────────────

@bp.route('/reviews', methods=['GET'])
@jwt_admin_required
def get_reviews():
    params = request.args.to_dict()
    if 'page' in params:
        params['page'] = int(params['page'])
    if 'per_page' in params:
        params['per_page'] = int(params['per_page'])
    result, status_code = admin_service.get_reviews(params)
    return jsonify(result), status_code


@bp.route('/reviews/<int:review_id>', methods=['DELETE'])
@jwt_admin_required
def delete_review(review_id):
    result, status_code = admin_service.delete_review(review_id)
    return jsonify(result), status_code
