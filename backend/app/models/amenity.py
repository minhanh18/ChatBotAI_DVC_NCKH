from app import db


class LocationAmenity(db.Model):
    """Association table between locations and amenities"""
    __tablename__ = 'location_amenities'
    
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'), primary_key=True)
    amenity_id = db.Column(db.Integer, db.ForeignKey('amenities.id'), primary_key=True)
    value = db.Column(db.String(100))  # Optional value (e.g., "Free", "24/7", "Premium")


class Amenity(db.Model):
    """Model for amenities (e.g., Wi-Fi, Pool, Parking)"""
    __tablename__ = 'amenities'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    icon = db.Column(db.String(50))  # Icon name or URL
    category = db.Column(db.String(50))  # e.g., "General", "Room", "Food"
    
    # Relationships
    locations = db.relationship('Location', secondary='location_amenities', backref=db.backref('amenities', lazy='dynamic'))
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'icon': self.icon,
            'category': self.category
        }
