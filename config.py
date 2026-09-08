import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'sqlite:///stbm.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Paystack Config
    PAYSTACK_PUBLIC_KEY = os.environ.get('PAYSTACK_PUBLIC_KEY', 'pk_test_4caf27ba085782664d98466d38b07ec4d334426a')
    PAYSTACK_SECRET_KEY = os.environ.get('PAYSTACK_SECRET_KEY', 'sk_test_c45dabd81218e4fac598659fc1368c1dd76e3b26')
    PAYSTACK_CALLBACK_URL = os.environ.get('PAYSTACK_CALLBACK_URL', 'http://localhost:5000/payment/callback')
    
    # Email Config
    MAIL_SERVER = 'smtp.gmail.com'
    MAIL_PORT = 587
    MAIL_USE_TLS = True
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    
    # App Config
    FREE_SHIPPING_THRESHOLD = 1000
    CURRENCY = 'GHS'  
    
    # Admin credentials
    ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', 'admin@stbm.com')
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')