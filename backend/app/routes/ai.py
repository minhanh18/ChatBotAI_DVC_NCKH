import hashlib
import json

from flask import Blueprint, request, jsonify, session, Response, stream_with_context, send_file, current_app
from flask_jwt_extended import jwt_required, current_user
from app.services.ai import get_ai_service
from app.services.itinerary_service import get_itinerary_service
from app.utils.chatbot_images import resolve_chatbot_image
from app.utils.db_knowledge import get_db_knowledge_stats
from app import db, cache

bp = Blueprint('ai', __name__, url_prefix='/api/ai')

@bp.route('/img/<slug>', methods=['GET'])
def serve_image(slug):
    try:
        image = resolve_chatbot_image(slug)
        if image:
            return send_file(str(image['path']))
        return jsonify({'error': 'Image not found'}), 404
    except Exception as e:
        current_app.logger.error(f'Error serving chatbot image {slug}: {str(e)}')
        return jsonify({'error': str(e)}), 500


@bp.route('/uploads/<path:filename>')
def serve_upload(filename):
    from flask import send_from_directory
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)


@bp.route('/chat', methods=['POST'])
@jwt_required(optional=True)
def chat():
    """Chat với AI (Streaming)"""
    try:
        data = request.get_json()
        message = data.get('message', '').strip()
        session_id = data.get('session_id')
        
        if not message:
            return jsonify({'error': 'Tin nhắn không được để trống'}), 400
        
        # 1. Rate Limiting & Guest Limit
        user_id = current_user.id if hasattr(current_user, 'id') else request.remote_addr
        
        # Guest Limit (3 messages)
        if not hasattr(current_user, 'id'):
            guest_limit_key = f"guest_chat_limit:{user_id}"
            guest_count = cache.get(guest_limit_key)
            if guest_count and int(guest_count) >= 3:
                return jsonify({
                    'error': 'GUEST_LIMIT_REACHED',
                    'message': 'Bạn đã hết lượt chat thử. Vui lòng đăng nhập để lưu lịch sử và tiếp tục trò chuyện!'
                }), 403
            
        rate_limit_key = f"rate_limit:{user_id}"
        count = cache.incr(rate_limit_key)
        if count == 1:
            cache.expire(rate_limit_key, 60)
        if count > 5:
            return jsonify({'error': 'Bạn đang chat quá nhanh. Vui lòng đợi 1 phút.'}), 429

        cache_key = f'ai_cache:v2_image_attach:{hashlib.md5(message.lower().encode()).hexdigest()}'
        cached_response = cache.get(cache_key)
        
        from app.models.ai import ChatSession, ChatMessage
        
        # Get chat history
        chat_session = None
        if session_id:
            chat_session = ChatSession.query.get(session_id)
            if chat_session:
                # Permission check: if session has an owner, must match current_user
                if chat_session.user_id:
                    if not current_user or not hasattr(current_user, 'id') or current_user.id != chat_session.user_id:
                        return jsonify({'error': 'Không có quyền truy cập đoạn chat này'}), 403
                # If guest session, and user is logged in, they shouldn't be using a guest session for history
                elif current_user and hasattr(current_user, 'id'):
                    # Optional: We could "claim" this session for the user here, 
                    # but for now let's just create a new one for safety.
                    chat_session = None

        if not chat_session:
            chat_session = ChatSession(
                user_id=current_user.id if hasattr(current_user, 'id') else None,
                title=message[:100]
            )
            db.session.add(chat_session)
            db.session.commit()
        
        # Get history (Optimized: only load the last 10 messages from DB)
        history_msgs = ChatMessage.query.filter_by(session_id=chat_session.id)\
            .order_by(ChatMessage.created_at.desc()).limit(10).all()
        history_msgs.reverse()
        
        chat_history = []
        for h in history_msgs:
            chat_history.append({
                'role': 'user' if h.sender_type == 'USER' else 'assistant',
                'content': h.message_content
            })
        
        context = {}
        if hasattr(current_user, 'id') and current_user.preferences:
            try:
                context['user_preferences'] = json.loads(current_user.preferences)
            except: pass

        ai_service = get_ai_service()

        def generate():
            full_response = ""
            # Yield session info first
            yield f"data: {json.dumps({'session_id': chat_session.id})}\n\n"

            if cached_response:
                yield f"data: {json.dumps({'text': cached_response})}\n\n"
                full_response = str(cached_response)
            else:
                for chunk in ai_service.chat_stream(message, context=context, chat_history=chat_history):
                    full_response += chunk
                    yield f"data: {json.dumps({'text': chunk})}\n\n"

            should_cache_response = full_response and not full_response.lstrip().startswith('Xin lỗi, đã có lỗi:')
            if should_cache_response:
                cache.set(cache_key, full_response, ex=3600)

            try:
                user_msg = ChatMessage(
                    session_id=chat_session.id,
                    sender_type='USER',
                    message_content=message
                )
                ai_msg = ChatMessage(
                    session_id=chat_session.id,
                    sender_type='AI',
                    message_content=full_response
                )
                db.session.add(user_msg)
                db.session.add(ai_msg)
                db.session.commit()
                
                # Increment guest count after successful message save
                if not hasattr(current_user, 'id'):
                    guest_limit_key = f"guest_chat_limit:{request.remote_addr}"
                    cache.incr(guest_limit_key)
                    # Keep guest limit records for 1 day
                    cache.expire(guest_limit_key, 86400)
                
                # Final signal
                yield f"data: {json.dumps({'done': True, 'ai_message': ai_msg.to_dict()})}\n\n"
            except Exception as e:
                current_app.logger.error(f"Error saving chat history: {str(e)}")
            
        resp = Response(stream_with_context(generate()), mimetype='text/event-stream')
        resp.headers['Cache-Control'] = 'no-cache'
        resp.headers['X-Accel-Buffering'] = 'no'
        return resp
        
    except Exception as e:
        if db.session.is_active:
            db.session.rollback()
        return jsonify({'error': str(e)}), 500


@bp.route('/generate-itinerary', methods=['POST'])
def generate_itinerary():
    """Tạo lịch trình tự động"""
    try:
        data = request.get_json()
        
        # Validate preferences
        preferences = {
            'duration': data.get('duration', 3),
            'budget': data.get('budget', 'medium'),
            'interests': data.get('interests', []),
            'location': data.get('location', 'Việt Nam'),
            'start_date': data.get('start_date')
        }
        
        # Get selected places if provided
        selected_places = data.get('place_ids', [])
        
        # Generate itinerary
        itinerary_service = get_itinerary_service()
        result = itinerary_service.generate_smart_itinerary(
            preferences,
            selected_places=selected_places
        )
        
        if not result['success']:
            return jsonify({'error': result.get('error')}), 500
        
        # Save to user's itineraries if authenticated
        if current_user.is_authenticated:
            save_result = itinerary_service.save_itinerary(
                current_user.id,
                result['itinerary']
            )
            result['itinerary']['saved'] = save_result['success']
            if save_result['success']:
                result['itinerary']['itinerary_id'] = save_result['itinerary_id']
        
        return jsonify(result['itinerary'])
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/suggest-places', methods=['POST'])
def suggest_places():
    """Gợi ý địa điểm phù hợp"""
    try:
        data = request.get_json()
        
        criteria = {
            'category': data.get('category', 'all'),
            'budget': data.get('budget', 'medium'),
            'interests': data.get('interests', []),
            'duration': data.get('duration')
        }
        
        from app.models.location import Location
        # Get available places
        query = Location.query.filter(Location.status == 'ACTIVE')
        
        if criteria['category'] != 'all':
            # This might need a join or check if category matches something in Location
            pass
        
        locations = query.limit(50).all()
        places_data = [l.to_dict() for l in locations]
        
        # Get AI suggestions
        ai_service = get_ai_service()
        result = ai_service.suggest_places(criteria, places_data)
        
        if not result['success']:
            return jsonify({'error': result.get('error')}), 500
        
        return jsonify(result['suggestions'])
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/estimate-cost', methods=['POST'])
def estimate_cost():
    """Ước tính chi phí"""
    try:
        data = request.get_json()
        
        # Get itinerary data
        itinerary_data = data.get('itinerary')
        if not itinerary_data:
            return jsonify({'error': 'Thiếu thông tin lịch trình'}), 400
        
        # Use AI to estimate cost
        ai_service = get_ai_service()
        result = ai_service.estimate_cost(itinerary_data)
        
        if not result['success']:
            return jsonify({'error': result.get('error')}), 500
        
        return jsonify(result['cost'])
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/sessions', methods=['POST'])
@jwt_required(optional=True)
def create_session():
    """Tạo cuộc hội thoại mới"""
    try:
        data = request.get_json()
        title = data.get('title', 'Cuộc hội thoại mới')
        
        from app.models.ai import ChatSession
        chat_session = ChatSession(
            user_id=current_user.id if hasattr(current_user, 'id') else None,
            title=title
        )
        db.session.add(chat_session)
        db.session.commit()
        
        return jsonify(chat_session.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@bp.route('/sessions', methods=['GET'])
@jwt_required()
def get_chat_sessions():
    """Lấy danh sách chat sessions"""
    try:
        from app.models.ai import ChatSession
        if current_user:
            sessions = ChatSession.query.filter_by(
                user_id=current_user.id
            ).order_by(ChatSession.started_at.desc()).limit(20).all()
        else:
            sessions = []
        
        return jsonify([session.to_dict() for session in sessions])
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/sessions/<int:session_id>/messages', methods=['GET'])
@jwt_required(optional=True)
def get_chat_session_messages(session_id):
    """Lấy danh sách tin nhắn của session"""
    try:
        from app.models.ai import ChatMessage, ChatSession
        chat_session = ChatSession.query.get_or_404(session_id)
        
        # Strict permission check
        if chat_session.user_id:
            # Session belongs to a user, check if it's the current user
            if not current_user or not hasattr(current_user, 'id') or current_user.id != chat_session.user_id:
                return jsonify({'error': 'Không có quyền truy cập'}), 403
        else:
            pass
                
        messages = ChatMessage.query.filter_by(session_id=session_id).order_by(ChatMessage.created_at.asc()).all()
        return jsonify([m.to_dict() for m in messages])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/chat-sessions/<session_id>', methods=['GET'])
@jwt_required()
def get_chat_session(session_id):
    """Lấy chi tiết chat session"""
    try:
        from app.models.ai import ChatSession
        chat_session = ChatSession.query.filter_by(id=session_id).first()
        
        if not chat_session:
            return jsonify({'error': 'Không tìm thấy đoạn chat'}), 404
        
        # Check permission
        if chat_session.user_id and (not current_user or 
                                     current_user.id != chat_session.user_id):
            return jsonify({'error': 'Không có quyền truy cập'}), 403
        
        return jsonify(chat_session.to_dict())
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/chat-sessions/<session_id>', methods=['DELETE'])
@jwt_required()
def delete_chat_session(session_id):
    """Xóa chat session"""
    if not current_user:
        return jsonify({'error': 'Vui lòng đăng nhập'}), 401
    
    try:
        from app.models.ai import ChatSession
        chat_session = ChatSession.query.filter_by(
            id=session_id,
            user_id=current_user.id
        ).first()
        
        if not chat_session:
            return jsonify({'error': 'Không tìm thấy đoạn chat hoặc bạn không có quyền xóa'}), 404
        
        db.session.delete(chat_session)
        db.session.commit()
        
        return jsonify({'message': 'Xóa thành công'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@bp.route('/admin/refresh-knowledge', methods=['POST'])
@jwt_required()
def refresh_ai_knowledge():
    """Admin: reload AI chatbot knowledge from database without restarting."""
    if not current_user or getattr(current_user, 'role', None) != 'ADMIN':
        return jsonify({'error': 'Chỉ admin mới có quyền thực hiện thao tác này'}), 403
    try:
        ai_service = get_ai_service()
        result = ai_service.refresh_knowledge()
        if result.get('success'):
            stats = result.get('stats', {})
            return jsonify({
                'message': 'Đã reload kiến thức AI từ database thành công',
                'stats': stats,
            }), 200
        return jsonify({'error': result.get('error', 'Unknown error')}), 500
    except Exception as e:
        current_app.logger.error(f'refresh_ai_knowledge error: {e}')
        return jsonify({'error': str(e)}), 500


@bp.route('/admin/knowledge-stats', methods=['GET'])
@jwt_required()
def get_knowledge_stats():
    """Admin: get current DB knowledge statistics."""
    if not current_user or getattr(current_user, 'role', None) != 'ADMIN':
        return jsonify({'error': 'Chỉ admin mới có quyền xem thông tin này'}), 403
    try:
        ai_service = get_ai_service()
        db_stats = get_db_knowledge_stats()
        return jsonify({
            'knowledge_base_chars': len(ai_service.km.knowledge_base),
            'knowledge_sections': len(ai_service.km.knowledge_sections),
            'db_images_loaded': len(ai_service.km._db_image_list),
            'db_stats': db_stats,
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500