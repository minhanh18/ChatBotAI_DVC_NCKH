import os, re, unicodedata
from flask import current_app
from app import create_app, db
from app.models.location import Category, Location, LocationImage

def remove_accents(input_str):
    if not input_str:
        return ""
    # Normalize unicode characters
    nfkd_form = unicodedata.normalize('NFKD', input_str)
    # Filter out non-spacing mark characters (accents)
    res = "".join([c for c in nfkd_form if not unicodedata.combining(c)])
    # Custom replacements for Vietnamese specific characters not handled by NFKD
    replacements = {
        'đ': 'd', 'Đ': 'D',
    }
    for k, v in replacements.items():
        res = res.replace(k, v)
    return res

def clean_filename(filename):
    name, ext = os.path.splitext(filename)
    # Remove accents
    name = remove_accents(name)
    # Lowercase
    name = name.lower()
    # Replace non-alphanumeric with hyphen
    name = re.sub(r'[^a-z0-9]+', '-', name)
    # Strip leading/trailing hyphens
    name = name.strip('-')
    return f"{name}{ext}"

def get_category_by_name(name):
    food_keywords = ['banh', 'bun', 'nho', 'com', 'mon', 'am thuc', 'dac san']
    stay_keywords = ['khach san', 'resort', 'homestay', 'nha nghi']
    
    clean_name = remove_accents(name).lower()
    
    if any(k in clean_name for k in food_keywords):
        return 'FOOD'
    if any(k in clean_name for k in stay_keywords):
        return 'STAY'
    return 'ATTRACTION'

def seed_production():
    app = create_app()
    with app.app_context():
        # Ensure categories exist
        categories = {cat.type: cat.id for cat in Category.query.all()}
        if not categories:
            print("No categories found. Please run baseline seed first.")
            return

        image_dir = app.config.get('UPLOAD_FOLDER')
        if not image_dir or not os.path.exists(image_dir):
            print(f"Directory not found: {image_dir}")
            return

        for filename in os.listdir(image_dir):
            if filename.startswith('.') or not os.path.isfile(os.path.join(image_dir, filename)):
                continue

            original_path = os.path.join(image_dir, filename)
            new_filename = clean_filename(filename)
            new_path = os.path.join(image_dir, new_filename)

            # Rename file
            if original_path != new_path:
                os.rename(original_path, new_path)
                print(f"Renamed: {filename} -> {new_filename}")

            # Location name is the original filename without extension
            location_name = os.path.splitext(filename)[0]
            
            # Check if location already exists
            location = Location.query.filter_by(name=location_name).first()
            if not location:
                cat_type = get_category_by_name(location_name)
                cat_id = categories.get(cat_type)
                
                location = Location(
                    name=location_name,
                    category_id=cat_id,
                    status='ACTIVE',
                    description=f"Khám phá {location_name} tại Khánh Hòa."
                )
                db.session.add(location)
                db.session.flush() # Get the ID
                print(f"Created location: {location_name} ({cat_type})")

            # Add image to location
            image_url = f"/uploads/{new_filename}"
            exists = LocationImage.query.filter_by(location_id=location.id, image_url=image_url).first()
            if not exists:
                img = LocationImage(
                    location_id=location.id,
                    image_url=image_url,
                    is_primary=True
                )
                db.session.add(img)
                print(f"Added image for {location_name}: {image_url}")

        db.session.commit()
        print("Production seeding completed successfully.")

if __name__ == "__main__":
    seed_production()
