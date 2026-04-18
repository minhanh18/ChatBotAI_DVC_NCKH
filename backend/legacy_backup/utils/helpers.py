import os, uuid, json

from werkzeug.utils import secure_filename
from flask import current_app
from datetime import datetime


def allowed_file(filename, allowed_extensions=None):
    if allowed_extensions is None:
        allowed_extensions = current_app.config.get('ALLOWED_EXTENSIONS', set())
    
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in allowed_extensions


def save_uploaded_file(file, folder='uploads'):
    if not file or not allowed_file(file.filename):
        return None
    
    # Generate unique filename
    filename = secure_filename(file.filename)
    name, ext = os.path.splitext(filename)
    unique_filename = f"{name}_{uuid.uuid4().hex[:8]}{ext}"
    
    # Create folder if not exists
    upload_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], folder)
    os.makedirs(upload_folder, exist_ok=True)
    
    # Save file
    filepath = os.path.join(upload_folder, unique_filename)
    file.save(filepath)
    
    # Return relative path
    return f"/uploads/{folder}/{unique_filename}"


def format_currency(amount, currency='VND'):
    """Format currency amount"""
    if currency == 'VND':
        return f"{amount:,.0f} ₫"
    return f"{amount:,.2f} {currency}"




def parse_json_safe(json_string, default=None):
    """Safely parse JSON string"""
    if not json_string:
        return default
    
    try:
        return json.loads(json_string)
    except (json.JSONDecodeError, TypeError):
        return default


def create_slug(text):
    """Create URL-friendly slug from text"""
    try:
        from slugify import slugify
        return slugify(text)
    except ImportError:
        # Fallback if python-slugify not installed
        import re
        text = text.lower()
        text = re.sub(r'[^\w\s-]', '', text)
        text = re.sub(r'[-\s]+', '-', text)
        return text.strip('-')


def paginate_query(query, page=1, per_page=20):
    """Paginate SQLAlchemy query"""
    pagination = query.paginate(
        page=page,
        per_page=per_page,
        error_out=False
    )
    
    return {
        'items': pagination.items,
        'total': pagination.total,
        'pages': pagination.pages,
        'current_page': page,
        'per_page': per_page,
        'has_next': pagination.has_next,
        'has_prev': pagination.has_prev
    }


def format_datetime(dt, format='%Y-%m-%d %H:%M:%S'):
    """Format datetime object"""
    if not dt:
        return None
    
    if isinstance(dt, str):
        return dt
    
    return dt.strftime(format)


def get_date_range(days_ago=30):
    """Get date range from days ago to now"""
    from datetime import timedelta
    
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days_ago)
    
    return start_date, end_date


def generate_session_id():
    """Generate unique session ID"""
    return str(uuid.uuid4())




def chunk_list(lst, chunk_size):
    """Split list into chunks"""
    for i in range(0, len(lst), chunk_size):
        yield lst[i:i + chunk_size]


def get_client_ip(request):
    """Get client IP address from request"""
    if request.headers.get('X-Forwarded-For'):
        return request.headers['X-Forwarded-For'].split(',')[0]
    return request.remote_addr


def sanitize_filename(filename):
    """Sanitize filename for security"""
    filename = secure_filename(filename)
    # Additional sanitization
    filename = filename.replace('..', '')
    filename = filename.replace('/', '')
    filename = filename.replace('\\', '')
    return filename


class JSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


def to_json(data):
    """Convert data to JSON with custom encoder"""
    return json.dumps(data, cls=JSONEncoder, ensure_ascii=False)