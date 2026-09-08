import secrets
import string
from datetime import datetime

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