from flask import Blueprint, jsonify

from app.models.dish import Dish

bp = Blueprint('dishes', __name__, url_prefix='/api/dishes')


@bp.route('', methods=['GET'])
def get_dishes():
    dishes = Dish.query.order_by(Dish.name.asc()).all()
    return jsonify([dish.to_dict() for dish in dishes])


@bp.route('/<int:dish_id>', methods=['GET'])
def get_dish(dish_id):
    dish = Dish.query.get_or_404(dish_id)
    return jsonify(dish.to_dict())
