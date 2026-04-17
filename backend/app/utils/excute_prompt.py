import os
from app import create_app, db
from app.models import *


app = create_app(os.getenv('FLASK_ENV', 'development'))


def controller(filename, prompt_command) -> None:
    exec(f"{prompt_command}()")

    # if prompt_command == "run":
    #     run()


def execute_query():
    with app.app_context():
        print(Location.query.filter_by(name="Phố Cổ Hà Nội").first_or_404().to_dict())


def make_shell_context():
    """Make database models available in shell"""
    return {
        'db': db,
        'User': User,
        'UserPreference': UserPreference,
        'Category': Category,
        'Location': Location,
        'LocationImage': LocationImage,
        'OpeningHour': OpeningHour,
        'Review': Review,
        'Favorite': Favorite,
        'SavedItinerary': SavedItinerary,
        'ChatSession': ChatSession,
        'ChatMessage': ChatMessage,
        'CostReference': CostReference,
        'SystemStatistic': SystemStatistic,
        'Dish': Dish,
        'LocationDish': LocationDish,
        'Amenity': Amenity,
        'LocationAmenity': LocationAmenity
    }


def init_db():
    """Initialize the database"""
    with app.app_context():
        db.create_all()
    print("Database initialized successfully!")


def seed_db():
    print("Seeding database with Ninh Thuan tourism data...")
    
    with app.app_context():
        # 1. Create Categories
        categories_data = [
            {'name': 'Điểm đến tiêu biểu', 'type': 'ATTRACTION', 'icon': 'map-pin'},
            {'name': 'Bãi biển & Vịnh', 'type': 'ATTRACTION', 'icon': 'waves'},
            {'name': 'Văn hóa & Lịch sử', 'type': 'ATTRACTION', 'icon': 'landmark'},
            {'name': 'Thiên nhiên & Sinh thái', 'type': 'ATTRACTION', 'icon': 'trees'},
            {'name': 'Ẩm thực Ninh Thuận', 'type': 'FOOD', 'icon': 'utensils'},
            {'name': 'Lưu trú nghỉ dưỡng', 'type': 'STAY', 'icon': 'hotel'},
        ]

        categories = {}
        for cat_data in categories_data:
            cat = Category.query.filter_by(name=cat_data['name']).first()
            if not cat:
                cat = Category(**cat_data)
                db.session.add(cat)
                db.session.commit()
                print(f"✓ Created category: {cat_data['name']}")
            categories[cat_data['name']] = cat

        # 2. Map filenames to categories and descriptions
        image_mapping = {
            "Biển Cà Ná.jpg": ("Bãi biển & Vịnh", "Biển Cà Ná - Cung đường biển đẹp nhất Việt Nam."),
            "Bánh căn.webp": ("Ẩm thực Ninh Thuận", "Bánh căn Ninh Thuận - Món ăn dân dã đặc trưng."),
            "Bãi Hỏm - Nơi rùa biển về đẻ trứng.jpg": ("Bãi biển & Vịnh", "Bãi Hỏm - Vẻ đẹp hoang sơ, yên bình."),
            "Bãi Tràng - Thiên đường cắm trại.jpg": ("Bãi biển & Vịnh", "Bãi Tràng - Địa điểm cắm trại lý tưởng."),
            "Bãi biển Bình Tiên.jpg": ("Bãi biển & Vịnh", "Bãi biển Bình Tiên - Viên ngọc ẩn mình."),
            "Bún sứa.webp": ("Ẩm thực Ninh Thuận", "Bún sứa - Đặc sản biển tươi ngon."),
            "Bảo tàng Ninh Thuận - Dấu ấn kiến trúc độc đáo.jpg": ("Văn hóa & Lịch sử", "Bảo tàng Ninh Thuận với kiến trúc độc đáo."),
            "Cánh đồng điện gió Đầm Nại - Biểu tượng năng lượng sạch.jpg": ("Điểm đến tiêu biểu", "Cánh đồng điện gió Đầm Nại."),
            "Hang Rái.webp": ("Thiên nhiên & Sinh thái", "Hang Rái - Tuyệt tác thiên nhiên ven biển."),
            "Hòn Đỏ - Thiên đường san hô dưới lòng biển.jpg": ("Thiên nhiên & Sinh thái", "Hòn Đỏ - Khám phá san hô."),
            "Làng Gốm Bàu Trúc.jpg": ("Văn hóa & Lịch sử", "Làng Gốm Bàu Trúc - Làng gốm cổ nhất Đông Nam Á."),
            "Làng nho Thái An - Thủ phủ nho.webp": ("Ẩm thực Ninh Thuận", "Làng nho Thái An."),
            "Mũi Đá Vách.jpg": ("Thiên nhiên & Sinh thái", "Mũi Đá Vách - Hùng vĩ giữa biển khơi."),
            "Núi Đá Chồng (Núi Phụng Hoàng).webp": ("Thiên nhiên & Sinh thái", "Núi Đá Chồng - Tầm nhìn bao quát Phan Rang."),
            "Thác Chapơr.jpg": ("Thiên nhiên & Sinh thái", "Thác Chapơr - Dải lụa trắng giữa đại ngàn."),
            "Tháp Po Klong Garai.jpg": ("Văn hóa & Lịch sử", "Tháp Po Klong Garai - Di tích Chăm cổ kính."),
            "Trùng Sơn Cổ Tự - Ngôi chùa trên đỉnh núi Đá Chồng.webp": ("Văn hóa & Lịch sử", "Trùng Sơn Cổ Tự."),
            "Vườn nho Ba Mọi - Trải nghiệm văn hóa nho Ninh Thuận.jpg": ("Ẩm thực Ninh Thuận", "Vườn nho Ba Mọi."),
            "Vườn quốc gia Núi Chúa - Rừng khô hạn châu Phi của Việt Nam.jpg": ("Thiên nhiên & Sinh thái", "Vườn quốc gia Núi Chúa."),
            "Vườn quốc gia Phước Bình.jpg": ("Thiên nhiên & Sinh thái", "Vườn quốc gia Phước Bình."),
            "Vịnh Vĩnh Hy.jpg": ("Bãi biển & Vịnh", "Vịnh Vĩnh Hy - Một trong những vịnh đẹp nhất Việt Nam."),
            "Đèo Ngoạn Mục.jpg": ("Điểm đến tiêu biểu", "Đèo Ngoạn Mục - Cung đèo hiểm trở và tuyệt đẹp."),
            "Đầm Nại.jpg": ("Thiên nhiên & Sinh thái", "Đầm Nại."),
            "Đồi cát Nam Cương.jpg": ("Thiên nhiên & Sinh thái", "Đồi cát Nam Cương - Vẻ đẹp của gió và cát."),
            "Đồng Cừu Ysa Núi Hòn Vàng Krong Pha.png": ("Điểm đến tiêu biểu", "Đồng cừu Ysa."),
        }

        image_dir = "static/images/anh/"
        
        for filename, (cat_name, desc) in image_mapping.items():
            name = filename.split('.')[0]
            if " - " in name:
                name = name.split(" - ")[0]
            
            # Create Location
            loc = Location.query.filter_by(name=name).first()
            if not loc:
                loc = Location(
                    name=name,
                    category_id=categories[cat_name].id,
                    description=desc,
                    address="Ninh Thuận",
                    price_range_min=0,
                    price_range_max=0,
                    status='ACTIVE'
                )
                db.session.add(loc)
                db.session.commit()
                print(f"✓ Created location: {name}")
            
            # Create Image and link to location
            img_path = f"/{image_dir}{filename}"
            img = LocationImage.query.filter_by(image_url=img_path, location_id=loc.id).first()
            if not img:
                img = LocationImage(
                    location_id=loc.id,
                    image_url=img_path,
                    is_primary=True
                )
                db.session.add(img)
            
            # If it's food, also add to Dish table
            if categories[cat_name].type == 'FOOD':
                dish = Dish.query.filter_by(name=name).first()
                if not dish:
                    dish = Dish(
                        name=name,
                        description=desc,
                        image_url=img_path
                    )
                    db.session.add(dish)
                    print(f"✓ Added dish: {name}")
        
        db.session.commit()
    print("\n[INFO] Database seeded successfully!")


def create_admin():
    """Create a new admin user"""
    fullname = input("Full Name: ")
    email = input("Email: ")
    password = input("Password: ")
    
    if User.query.filter_by(email=email).first():
        print("Error: Email already exists!")
        return
    
    admin = User(fullname=fullname, email=email, role='ADMIN')
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()
    
    print(f"[INFO] Admin user '{fullname}' created successfully!")


def seed_sql():
    """Run dev_seed.sql using Python's sqlite3"""
    import sqlite3
    db_path = 'tourism.db'
    seed_path = 'dev_seed.sql'
    
    if not os.path.exists(seed_path):
        print(f"Lỗi: Không tìm thấy file {seed_path}")
        return

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        print(f"Đang đọc dữ liệu từ {seed_path}...")
        with open(seed_path, 'r', encoding='utf-8') as f:
            sql_script = f.read()
        print("Đang thêm dữ liệu vào database...")
        cursor.executescript(sql_script)
        conn.commit()
        print("Hoàn thành! Dữ liệu đã được thêm thành công.")
    except Exception as e:
        print(f"Lỗi: {e}")
    finally:
        if conn:
            conn.close()


def run():
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    app.run(host='0.0.0.0', port=port, debug=debug)

if __name__ == '__main__':
    pass