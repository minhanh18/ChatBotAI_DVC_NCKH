from datetime import datetime

from app import db


class Category(db.Model):
    __tablename__ = 'categories'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    icon = db.Column(db.String(255))
    type = db.Column(db.Enum('ATTRACTION', 'FOOD', 'STAY', name='category_types'), nullable=False)

    locations = db.relationship('Location', backref='category', lazy='dynamic')
    user_preferences = db.relationship('UserPreference', backref='category', lazy='dynamic')

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'icon': self.icon,
            'type': self.type
        }


class Location(db.Model):
    __tablename__ = 'locations'

    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=False)
    name = db.Column(db.String(200), nullable=False, index=True)
    description = db.Column(db.Text)
    address = db.Column(db.String(300))
    price_range_min = db.Column(db.Float)
    price_range_max = db.Column(db.Float)
    price = db.Column(db.Float)
    rating_avg = db.Column(db.Float, default=0.0)
    status = db.Column(db.Enum('ACTIVE', 'INACTIVE', name='location_status'), default='ACTIVE')
    path = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    images = db.relationship('LocationImage', backref='location', lazy='dynamic', cascade='all, delete-orphan')
    opening_hours = db.relationship('OpeningHour', backref='location', lazy='dynamic', cascade='all, delete-orphan')
    favorites = db.relationship('Favorite', backref='location', lazy='dynamic', cascade='all, delete-orphan')

    @property
    def map_url(self):
        from urllib.parse import quote

        if self.path and isinstance(self.path, list):
            origin = quote(self.name)
            dest = quote(self.path[-1])
            waypoints = '|'.join([quote(p) for p in self.path[:-1]])

            url = f'https://www.google.com/maps/dir/?api=1&origin={origin}&destination={dest}'
            if waypoints:
                url += f'&waypoints={waypoints}'
            return url

        search_query = self.name
        if self.address:
            search_query += f' {self.address}'
        return f'https://www.google.com/maps/search/?api=1&query={quote(search_query)}'

    def _serialize_images(self, limit=None):
        query = self.images.order_by(LocationImage.is_primary.desc(), LocationImage.id.asc())
        if limit is not None:
            query = query.limit(limit)
        return [image.to_dict() for image in query.all()]

    def _serialize_opening_hours(self):
        return [
            opening_hour.to_dict()
            for opening_hour in self.opening_hours.order_by(OpeningHour.day_of_week.asc()).all()
        ]

    def to_summary_dict(self, category=None, images=None):
        category_data = category
        if category_data is None and self.category:
            category_data = self.category.to_dict()
        elif hasattr(category_data, 'to_dict'):
            category_data = category_data.to_dict()

        images_data = images
        if images_data is None:
            images_data = self._serialize_images(limit=1)
        else:
            images_data = [image.to_dict() if hasattr(image, 'to_dict') else image for image in images_data]

        return {
            'id': self.id,
            'category_id': self.category_id,
            'name': self.name,
            'description': self.description,
            'address': self.address,
            'price_range_min': self.price_range_min,
            'price_range_max': self.price_range_max,
            'price': self.price,
            'rating_avg': self.rating_avg,
            'status': self.status,
            'category': category_data,
            'images': images_data,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

    def to_dict(self):
        data = self.to_summary_dict()
        data['opening_hours'] = self._serialize_opening_hours()
        data['images'] = self._serialize_images() # Get all images for detail
        data['map_url'] = self.map_url
        data['path'] = self.path
        return data


class LocationImage(db.Model):
    __tablename__ = 'location_images'

    id = db.Column(db.Integer, primary_key=True)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'), nullable=False)
    image_url = db.Column(db.String(255), nullable=False)
    is_primary = db.Column(db.Boolean, default=False)

    def to_dict(self):
        return {
            'id': self.id,
            'location_id': self.location_id,
            'image_url': self.image_url,
            'is_primary': self.is_primary
        }


class OpeningHour(db.Model):
    __tablename__ = 'opening_hours'

    id = db.Column(db.Integer, primary_key=True)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'), nullable=False)
    day_of_week = db.Column(db.Integer)
    open_time = db.Column(db.Time)
    close_time = db.Column(db.Time)

    def to_dict(self):
        return {
            'id': self.id,
            'location_id': self.location_id,
            'day_of_week': self.day_of_week,
            'open_time': self.open_time.strftime('%H:%M') if self.open_time else None,
            'close_time': self.close_time.strftime('%H:%M') if self.close_time else None
        }
