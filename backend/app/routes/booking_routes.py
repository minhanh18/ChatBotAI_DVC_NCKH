from datetime import date
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from app import db
from app.models.booking import Booking
from app.models.business_registration import BusinessRegistration
from app.utils.auth import jwt_business_required

bp = Blueprint('booking', __name__, url_prefix='/api/bookings')


# ────────────────────────────────────────────────
# CUSTOMER ENDPOINTS  (any logged-in user)
# ────────────────────────────────────────────────

@bp.route('', methods=['POST'])
@jwt_required()
def create_booking():
    """Customer creates a booking at an approved business."""
    customer_user_id = get_jwt_identity()
    data = request.get_json() or {}

    business_registration_id = data.get('business_registration_id')
    service_type = data.get('service_type', 'TABLE')
    booking_date_str = data.get('booking_date')
    time_slot = data.get('time_slot', '')
    guest_count = int(data.get('guest_count', 1))
    notes = data.get('notes', '')
    customer_name = data.get('customer_name', '').strip()
    customer_phone = data.get('customer_phone', '').strip()

    if not business_registration_id or not booking_date_str or not customer_name or not customer_phone:
        return jsonify({'error': 'Vui lòng cung cấp đầy đủ thông tin đặt chỗ và người liên hệ'}), 400

    if service_type not in ('ROOM', 'TABLE', 'SEAT'):
        return jsonify({'error': 'Loại dịch vụ không hợp lệ'}), 400

    # Validate the business exists and is approved
    business = BusinessRegistration.query.get(business_registration_id)
    if not business or business.status != 'APPROVED':
        return jsonify({'error': 'Doanh nghiệp không tồn tại hoặc chưa được phê duyệt'}), 404

    try:
        booking_date = date.fromisoformat(booking_date_str)
    except ValueError:
        return jsonify({'error': 'Định dạng ngày không hợp lệ (YYYY-MM-DD)'}), 400

    try:
        booking = Booking(
            business_registration_id=business_registration_id,
            customer_user_id=customer_user_id,
            customer_name=customer_name,
            customer_phone=customer_phone,
            service_type=service_type,
            booking_date=booking_date,
            time_slot=time_slot,
            guest_count=guest_count,
            notes=notes,
            status='PENDING'
        )
        db.session.add(booking)
        db.session.commit()
        return jsonify({'message': 'Đặt chỗ thành công!', 'booking': booking.to_dict()}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@bp.route('/my', methods=['GET'])
@jwt_required()
def my_bookings():
    """List all bookings made by the current user."""
    customer_user_id = get_jwt_identity()
    bookings = (
        Booking.query
        .filter_by(customer_user_id=customer_user_id)
        .order_by(Booking.created_at.desc())
        .all()
    )
    return jsonify([b.to_dict() for b in bookings]), 200


@bp.route('/<booking_id>', methods=['DELETE'])
@jwt_required()
def cancel_my_booking(booking_id):
    """Customer cancels their own PENDING booking."""
    customer_user_id = get_jwt_identity()
    booking = Booking.query.get_or_404(booking_id)

    if booking.customer_user_id != customer_user_id:
        return jsonify({'error': 'Không có quyền thực hiện'}), 403

    if booking.status != 'PENDING':
        return jsonify({'error': 'Chỉ có thể hủy đặt chỗ đang chờ xác nhận'}), 400

    try:
        booking.status = 'CANCELLED'
        db.session.commit()
        return jsonify({'message': 'Đã hủy đặt chỗ', 'booking': booking.to_dict()}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# ────────────────────────────────────────────────
# BUSINESS OWNER ENDPOINTS  (/api/bookings/manage/*)
# ────────────────────────────────────────────────

@bp.route('/manage', methods=['GET'])
@jwt_business_required
def manage_bookings():
    """Business owner views all bookings for their approved businesses."""
    from flask_jwt_extended import get_jwt_identity
    owner_id = get_jwt_identity()

    # Find all approved registrations owned by this user
    registrations = BusinessRegistration.query.filter_by(
        user_id=owner_id, status='APPROVED'
    ).all()
    reg_ids = [r.id for r in registrations]

    status_filter = request.args.get('status')
    query = Booking.query.filter(Booking.business_registration_id.in_(reg_ids))
    if status_filter and status_filter in ('PENDING', 'CONFIRMED', 'CANCELLED'):
        query = query.filter(Booking.status == status_filter)

    bookings = query.order_by(Booking.created_at.desc()).all()
    return jsonify([b.to_dict() for b in bookings]), 200


@bp.route('/manage/<booking_id>/confirm', methods=['POST'])
@jwt_business_required
def confirm_booking(booking_id):
    """Business owner confirms a PENDING booking."""
    from flask_jwt_extended import get_jwt_identity
    owner_id = get_jwt_identity()
    booking = Booking.query.get_or_404(booking_id)

    # Verify ownership
    if booking.business.user_id != owner_id:
        return jsonify({'error': 'Không có quyền thực hiện'}), 403

    if booking.status != 'PENDING':
        return jsonify({'error': 'Chỉ có thể xác nhận đặt chỗ đang chờ'}), 400

    try:
        booking.status = 'CONFIRMED'
        db.session.commit()
        return jsonify({'message': 'Đã xác nhận đặt chỗ', 'booking': booking.to_dict()}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@bp.route('/manage/<booking_id>/cancel', methods=['POST'])
@jwt_business_required
def business_cancel_booking(booking_id):
    """Business owner cancels a booking."""
    from flask_jwt_extended import get_jwt_identity
    owner_id = get_jwt_identity()
    booking = Booking.query.get_or_404(booking_id)

    if booking.business.user_id != owner_id:
        return jsonify({'error': 'Không có quyền thực hiện'}), 403

    if booking.status == 'CANCELLED':
        return jsonify({'error': 'Đặt chỗ này đã bị hủy'}), 400

    try:
        booking.status = 'CANCELLED'
        db.session.commit()
        return jsonify({'message': 'Đã hủy đặt chỗ', 'booking': booking.to_dict()}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@bp.route('/manage/businesses', methods=['GET'])
@jwt_business_required
def my_businesses():
    """Return all approved business registrations owned by the current user."""
    from flask_jwt_extended import get_jwt_identity
    owner_id = get_jwt_identity()
    registrations = BusinessRegistration.query.filter_by(
        user_id=owner_id, status='APPROVED'
    ).all()
    return jsonify([r.to_dict() for r in registrations]), 200
@bp.route('/manage/junk', methods=['DELETE'])
@jwt_business_required
def clear_junk_bookings():
    """Permanently delete CANCELLED bookings for the owner's businesses."""
    from flask_jwt_extended import get_jwt_identity
    owner_id = get_jwt_identity()

    # Find all approved registrations owned by this user
    registrations = BusinessRegistration.query.filter_by(
        user_id=owner_id, status='APPROVED'
    ).all()
    reg_ids = [r.id for r in registrations]

    if not reg_ids:
        return jsonify({'message': 'Không tìm thấy doanh nghiệp nào', 'deleted_count': 0}), 200

    try:
        # Delete CANCELLED bookings for these businesses
        deleted_count = Booking.query.filter(
            Booking.business_registration_id.in_(reg_ids),
            Booking.status == 'CANCELLED'
        ).delete(synchronize_session=False)

        db.session.commit()
        return jsonify({
            'message': f'Đã xóa sạch {deleted_count} yêu cầu đặt chỗ rác',
            'deleted_count': deleted_count
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500
