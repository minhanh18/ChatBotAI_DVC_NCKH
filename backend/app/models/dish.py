from app import db


class LocationDish(db.Model):
    """Association table between locations and dishes"""
    __tablename__ = 'location_dishes'
    
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'), primary_key=True)
    dish_id = db.Column(db.Integer, db.ForeignKey('dishes.id'), primary_key=True)
    price = db.Column(db.Float)  # Price of this specific dish at this location
    is_specialty = db.Column(db.Boolean, default=False)


class Dish(db.Model):
    """Model for local cuisine/dishes"""
    __tablename__ = 'dishes'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    image_url = db.Column(db.String(255))
    
    # Relationships
    locations = db.relationship('Location', secondary='location_dishes', backref=db.backref('dishes', lazy='dynamic'))
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'image_url': self.image_url
        }
