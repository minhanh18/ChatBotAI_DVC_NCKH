from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.services.news_service import NewsService

bp = Blueprint('news', __name__, url_prefix='/api/news')


@bp.route('', methods=['GET'])
def get_posts():
    """Lấy danh sách bài viết/tin tức"""
    params = request.args.to_dict()
    if 'page' in params: params['page'] = int(params['page'])
    if 'per_page' in params: params['per_page'] = int(params['per_page'])
    
    result, status_code = NewsService.get_posts(params)
    return jsonify(result), status_code


@bp.route('/<string:post_id>', methods=['GET'])
@jwt_required(optional=True)
def get_post(post_id):
    """Lấy chi tiết bài viết"""
    user_id = get_jwt_identity()
    result, status_code = NewsService.get_post(post_id, user_id=user_id)
    return jsonify(result), status_code


@bp.route('', methods=['POST'])
@jwt_required()
def create_post():
    """Tạo bài viết mới"""
    data = request.form.to_dict() if not request.is_json else (request.get_json() or {})
    result, status_code = NewsService.create_post(get_jwt_identity(), data, request.files)
    return jsonify(result), status_code


@bp.route('/<string:post_id>/comment', methods=['POST'])
@jwt_required()
def add_comment(post_id):
    """Thêm bình luận vào bài viết"""
    data = request.get_json()
    result, status_code = NewsService.add_comment(post_id, get_jwt_identity(), data)
    return jsonify(result), status_code

@bp.route('/<string:post_id>/comment/<string:comment_id>/like', methods=['POST'])
@jwt_required()
def toggle_comment_like(post_id, comment_id):
    """Thích/Bỏ thích bình luận"""
    result, status_code = NewsService.toggle_comment_like(comment_id, get_jwt_identity())
    return jsonify(result), status_code

@bp.route('/<string:post_id>/like', methods=['POST'])
@jwt_required()
def toggle_like(post_id):
    """Thích/Bỏ thích bài viết"""
    result, status_code = NewsService.toggle_like(post_id, get_jwt_identity())
    return jsonify(result), status_code
