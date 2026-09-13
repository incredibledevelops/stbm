import secrets
import string
import os
from datetime import datetime
from werkzeug.utils import secure_filename
from flask import current_app


def generate_order_number():
    """Generate a unique order number"""
    prefix = 'ORD'
    timestamp = datetime.now().strftime('%y%m%d')
    random_part = ''.join(secrets.choice(string.digits) for _ in range(4))
    return f"{prefix}-{timestamp}-{random_part}"


def format_currency(amount):
    """Format amount in GH₵ currency"""
    return f"GH₵ {amount:,.2f}"


def get_status_color(status):
    """Get Bootstrap color class for status"""
    colors = {
        'Active': 'success',
        'Low Stock': 'warning',
        'Out of Stock': 'danger',
        'Processing': 'info',
        'Shipped': 'primary',
        'Delivered': 'success',
        'Cancelled': 'secondary',
        'Inactive': 'danger',
        'Admin': 'warning',
        'Customer': 'info',
        'Pending': 'warning'
    }
    return colors.get(status, 'secondary')


def calculate_cart_total(cart_items):
    """Calculate total price of cart items"""
    total = 0
    for item in cart_items:
        total += item.product.price * item.quantity
    return total


# ============================================================
# FILE UPLOAD HELPERS
# ============================================================

def allowed_file(filename):
    """Check if file has an allowed extension"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in current_app.config['ALLOWED_EXTENSIONS']


def save_product_image(file):
    """
    Save an uploaded product image and return the relative URL path.
    Returns None if no valid file was provided.
    """
    if not file or file.filename == '':
        return None
    
    if not allowed_file(file.filename):
        return None
    
    # Generate a unique filename
    original_filename = secure_filename(file.filename)
    ext = original_filename.rsplit('.', 1)[1].lower()
    unique_filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(4)}.{ext}"
    
    # Ensure upload directory exists
    upload_folder = current_app.config['UPLOAD_FOLDER']
    os.makedirs(upload_folder, exist_ok=True)
    
    # Save the file
    file_path = os.path.join(upload_folder, unique_filename)
    file.save(file_path)
    
    # Return the relative URL path (for use in templates and DB storage)
    return f"uploads/products/{unique_filename}"


def delete_product_image(image_url):
    """
    Delete a product image file from disk.
    `image_url` is expected to be the relative path stored in the DB
    (e.g. 'uploads/products/xyz.jpg').
    """
    if not image_url:
        return
    
    # Handle legacy external URLs (http/https) — we don't delete those
    if image_url.startswith('http://') or image_url.startswith('https://'):
        return
    
    # Handle both old format (full URL in templates) and new format (relative path)
    static_folder = os.path.join(current_app.root_path, 'static')
    file_path = os.path.join(static_folder, image_url)
    
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception as e:
        current_app.logger.error(f'Failed to delete image {image_url}: {e}')


def save_multiple_product_images(files):
    """
    Save multiple uploaded product images.
    Returns a list of relative paths.
    """
    saved_paths = []
    if not files:
        return saved_paths
    
    for file in files:
        if file and file.filename:
            path = save_product_image(file)
            if path:
                saved_paths.append(path)
    
    return saved_paths


def delete_product_images(image_paths):
    """Delete multiple product images from disk"""
    for path in image_paths:
        delete_product_image(path)


# ============================================================
# VARIANT HELPERS
# ============================================================

def group_variants_by_color(variants):
    """
    Group a list of ProductVariant objects by their color.
    Returns a dict: {color_name: [list of variants]}
    """
    grouped = {}
    for v in variants:
        color_key = v.color or 'Unspecified'
        if color_key not in grouped:
            grouped[color_key] = []
        grouped[color_key].append(v)
    return grouped


def get_unique_colors(variants):
    """
    Return a list of unique color dicts from a list of variants.
    Each dict: {'name': 'Black', 'hex': '#000000'}
    """
    seen = set()
    colors = []
    for v in variants:
        if v.color and v.color not in seen:
            seen.add(v.color)
            colors.append({
                'name': v.color,
                'hex': v.color_hex or '#000000'
            })
    return colors


def get_unique_sizes(variants):
    """Return a sorted list of unique sizes from a list of variants"""
    sizes = set()
    for v in variants:
        if v.size:
            sizes.add(v.size)
    # Sort sizes in a sensible order (S, M, L, XL, XXL, then numeric)
    size_order = {'XS': 1, 'S': 2, 'M': 3, 'L': 4, 'XL': 5, 'XXL': 6, 'XXXL': 7, 'One Size': 99}
    return sorted(sizes, key=lambda s: (size_order.get(s.upper(), 50), s))


def get_variant_label(variant):
    """
    Return a human-readable label for a variant.
    e.g. "Black / M" or "Black" or "Size M" or "Default"
    """
    if not variant:
        return ''
    parts = []
    if variant.color:
        parts.append(variant.color)
    if variant.size:
        parts.append(variant.size)
    return ' / '.join(parts) if parts else 'Default'


def find_variant(product, color, size):
    """
    Find a matching variant for the given product, color, and size.
    Returns the variant or None.
    """
    if not product or not product.variants:
        return None
    
    for v in product.variants:
        color_match = (v.color or '') == (color or '')
        size_match = (v.size or '') == (size or '')
        if color_match and size_match:
            return v
    return None


def get_total_variant_stock(product):
    """Return the sum of stock across all variants of a product"""
    if not product or not product.variants:
        return 0
    return sum(v.stock for v in product.variants)