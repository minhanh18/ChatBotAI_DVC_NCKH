from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required
from app.services.business_service import get_business_service
from app.utils.auth import jwt_admin_required

bp = Blueprint('admin_business', __name__, url_prefix='/api/admin/business')
business_service = get_business_service()

@bp.route('/registrations', methods=['GET'])
@jwt_admin_required
def get_registrations():
    params = request.args.to_dict()
    result, status_code = business_service.get_registrations(params)
    return jsonify(result), status_code

@bp.route('/registrations/<registration_id>', methods=['GET'])
@jwt_admin_required
def get_registration_detail(registration_id):
    result, status_code = business_service.get_registration_detail(registration_id)
    return jsonify(result), status_code

@bp.route('/registrations/<registration_id>/process', methods=['POST'])
@jwt_admin_required
def process_registration(registration_id):
    data = request.get_json() or {}
    status = data.get('status')
    admin_notes = data.get('admin_notes')
    
    result, status_code = business_service.process_registration(registration_id, status, admin_notes)
    return jsonify(result), status_code
