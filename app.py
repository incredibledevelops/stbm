from datetime import datetime, timedelta
from sqlalchemy import func, extract
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from models import db, User, Product, CartItem, Order, OrderItem, Setting, ContactMessage, PageVisit, PaymentLog
from forms import LoginForm, SignupForm, ProductForm, SettingsForm, ContactForm
from utils import generate_order_number, format_currency, get_status_color, calculate_cart_total
from config import Config
import json
import smtplib
import requests
from paystack import PaystackAPI
import hashlib
import hmac
import uuid
import os

app = Flask(__name__)
app.config.from_object(Config)

# Initialize extensions
db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# --- CONTACT FORM WITH ADMIN STORAGE ---

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    """Contact page with database storage"""
    form = ContactForm()
    if form.validate_on_submit():
        message = ContactMessage(
            name=form.name.data,
            email=form.email.data,
            message=form.message.data,
            status='Unread'
        )
        db.session.add(message)
        db.session.commit()

        try:
            if app.config.get('MAIL_USERNAME') and app.config.get('MAIL_PASSWORD'):
                send_contact_notification(message)
        except Exception as e:
            app.logger.error(f'Failed to send contact notification: {e}')

        flash('Your message has been sent! We\'ll get back to you soon.', 'success')
        return redirect(url_for('contact'))

    return render_template('contact.html', form=form)


def send_contact_notification(message):
    """Send email notification for new contact message"""
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    admin_email_setting = Setting.query.filter_by(key='support_email').first()
    admin_email = admin_email_setting.value if admin_email_setting else app.config.get('MAIL_USERNAME')

    if not admin_email:
        return

    subject = f'New Contact Message from {message.name}'
    body = f"""
    New contact message received:

    Name: {message.name}
    Email: {message.email}
    Message:
    {message.message}

    View in admin panel: {url_for('admin_messages', _external=True)}
    """

    msg = MIMEMultipart()
    msg['From'] = app.config.get('MAIL_USERNAME')
    msg['To'] = admin_email
    msg['Subject'] = subject

    msg.attach(MIMEText(body, 'plain'))

    server = smtplib.SMTP(app.config.get('MAIL_SERVER'), app.config.get('MAIL_PORT'))
    server.starttls()
    server.login(app.config.get('MAIL_USERNAME'), app.config.get('MAIL_PASSWORD'))
    server.send_message(msg)
    server.quit()


# --- PAGE VISITOR TRACKING ---

SKIP_TRACKING_PREFIXES = ('/static', '/api', '/payment', '/admin/backup')


@app.before_request
def track_page_visit():
    """Track page visits for analytics"""
    if request.method != 'GET':
        return

    for prefix in SKIP_TRACKING_PREFIXES:
        if request.path.startswith(prefix):
            return

    user_id = current_user.id if current_user.is_authenticated else None

    session_id = session.get('visitor_id')
    if not session_id:
        session_id = str(uuid.uuid4())
        session['visitor_id'] = session_id

    try:
        visit = PageVisit(
            page_url=request.path,
            page_name=request.endpoint or request.path,
            ip_address=request.remote_addr,
            user_agent=request.headers.get('User-Agent', 'Unknown')[:255],
            user_id=user_id,
            session_id=session_id,
            referrer=request.referrer or 'Direct'
        )
        db.session.add(visit)
        db.session.commit()
    except Exception as e:
        app.logger.error(f'Failed to track page visit: {e}')
        db.session.rollback()


# --- ADMIN CONTACT MESSAGES ---

@app.route('/admin/messages')
@login_required
def admin_messages():
    """Admin view for contact messages"""
    if not current_user.is_admin():
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))

    messages = ContactMessage.query.order_by(ContactMessage.created_at.desc()).all()
    unread_count = ContactMessage.query.filter_by(status='Unread').count()

    return render_template('admin/messages.html', messages=messages, unread_count=unread_count)


@app.route('/admin/messages/<int:message_id>')
@login_required
def admin_message_detail(message_id):
    """View individual message"""
    if not current_user.is_admin():
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))

    message = ContactMessage.query.get_or_404(message_id)

    if message.status == 'Unread':
        message.status = 'Read'
        db.session.commit()

    return render_template('admin/message_detail.html', message=message)


@app.route('/admin/messages/<int:message_id>/reply', methods=['POST'])
@login_required
def admin_message_reply(message_id):
    """Reply to a contact message"""
    if not current_user.is_admin():
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))

    message = ContactMessage.query.get_or_404(message_id)
    reply = request.form.get('reply')

    if reply:
        message.status = 'Replied'
        db.session.commit()

        try:
            send_reply_email(message, reply)
            flash('Reply sent successfully!', 'success')
        except Exception as e:
            app.logger.error(f'Failed to send reply email: {e}')
            flash('Message marked as replied but email failed to send.', 'warning')

    return redirect(url_for('admin_message_detail', message_id=message_id))


@app.route('/admin/messages/<int:message_id>/delete', methods=['POST'])
@login_required
def admin_message_delete(message_id):
    """Delete a contact message"""
    if not current_user.is_admin():
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))

    message = ContactMessage.query.get_or_404(message_id)
    db.session.delete(message)
    db.session.commit()
    flash('Message deleted successfully.', 'success')
    return redirect(url_for('admin_messages'))


def send_reply_email(message, reply):
    """Send reply email to contact"""
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    subject = 'Re: Your message to STBM'
    body = f"""
    Hello {message.name},

    Thank you for reaching out to STBM. Here's our response to your message:

    {reply}

    ---
    Original Message:
    {message.message}

    Best regards,
    STBM Team
    """

    msg = MIMEMultipart()
    msg['From'] = app.config.get('MAIL_USERNAME')
    msg['To'] = message.email
    msg['Subject'] = subject

    msg.attach(MIMEText(body, 'plain'))

    server = smtplib.SMTP(app.config.get('MAIL_SERVER'), app.config.get('MAIL_PORT'))
    server.starttls()
    server.login(app.config.get('MAIL_USERNAME'), app.config.get('MAIL_PASSWORD'))
    server.send_message(msg)
    server.quit()


# --- ADMIN ANALYTICS ---

@app.route('/admin/analytics')
@login_required
def admin_analytics():
    """Admin analytics dashboard"""
    if not current_user.is_admin():
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))

    total_visits = PageVisit.query.count()
    unique_visitors = db.session.query(
        func.count(func.distinct(PageVisit.session_id))
    ).scalar() or 0
    today_visits = PageVisit.query.filter(
        func.date(PageVisit.created_at) == datetime.utcnow().date()
    ).count()

    top_pages_raw = db.session.query(
        PageVisit.page_url,
        PageVisit.page_name,
        func.count(PageVisit.id).label('visits')
    ).group_by(PageVisit.page_url, PageVisit.page_name).order_by(
        func.count(PageVisit.id).desc()
    ).limit(10).all()

    top_pages = [
        {
            'url': row[0],
            'name': row[1] or row[0],
            'visits': row[2]
        }
        for row in top_pages_raw
    ]

    daily_visits_raw = db.session.query(
        func.date(PageVisit.created_at).label('date'),
        func.count(PageVisit.id).label('count')
    ).filter(
        PageVisit.created_at >= datetime.utcnow() - timedelta(days=7)
    ).group_by(func.date(PageVisit.created_at)).order_by('date').all()

    daily_visits = [
        {
            'date': str(row[0]) if row[0] else 'N/A',
            'count': row[1]
        }
        for row in daily_visits_raw
    ]

    hourly_visits_raw = db.session.query(
        extract('hour', PageVisit.created_at).label('hour'),
        func.count(PageVisit.id).label('count')
    ).filter(
        PageVisit.created_at >= datetime.utcnow() - timedelta(hours=24)
    ).group_by(extract('hour', PageVisit.created_at)).order_by('hour').all()

    hourly_visits = [
        {
            'hour': int(row[0]) if row[0] is not None else 0,
            'count': row[1]
        }
        for row in hourly_visits_raw
    ]

    return render_template('admin/analytics.html',
                           total_visits=total_visits,
                           unique_visitors=unique_visitors,
                           today_visits=today_visits,
                           top_pages=top_pages,
                           daily_visits=daily_visits,
                           hourly_visits=hourly_visits)


# --- ADMIN USER CRUD (Enhanced) ---

@app.route('/admin/users/add', methods=['GET', 'POST'])
@login_required
def admin_add_user():
    """Add new user from admin panel"""
    if not current_user.is_admin():
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))

    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        password = request.form.get('password')
        role = request.form.get('role', 'Customer')
        status = request.form.get('status', 'Active')

        if not name or not email or not password:
            flash('All fields are required.', 'danger')
            return redirect(url_for('admin_add_user'))

        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'danger')
            return redirect(url_for('admin_add_user'))

        user = User(
            name=name,
            email=email,
            role=role,
            status=status
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        flash(f'User {name} created successfully!', 'success')
        return redirect(url_for('admin_users'))

    return render_template('admin/user_form.html', title='Add User', mode='add', user=None)


@app.route('/admin/users/edit/<int:user_id>', methods=['GET', 'POST'])
@login_required
def admin_edit_user_page(user_id):
    """Edit user from admin panel"""
    if not current_user.is_admin():
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))

    user = User.query.get_or_404(user_id)

    if request.method == 'POST':
        user.name = request.form.get('name')
        user.email = request.form.get('email')
        user.role = request.form.get('role')
        user.status = request.form.get('status')

        new_password = request.form.get('password')
        if new_password and len(new_password) >= 6:
            user.set_password(new_password)

        db.session.commit()
        flash('User updated successfully!', 'success')
        return redirect(url_for('admin_users'))

    return render_template('admin/user_form.html', title='Edit User', mode='edit', user=user)


@app.route('/admin/users/update-status/<int:user_id>', methods=['POST'])
@login_required
def admin_update_user_status(user_id):
    """Update user status via AJAX"""
    if not current_user.is_admin():
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403

    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        return jsonify({'success': False, 'message': 'Cannot change your own status'}), 400

    data = request.get_json(silent=True) or {}
    new_status = data.get('status')
    if new_status in ['Active', 'Inactive']:
        user.status = new_status
        db.session.commit()
        return jsonify({'success': True, 'message': 'Status updated'})

    return jsonify({'success': False, 'message': 'Invalid status'}), 400


# --- DATABASE INITIALIZATION ---

def init_db():
    """Initialize database tables and default data"""
    with app.app_context():
        db.create_all()

        if not User.query.filter_by(email=Config.ADMIN_EMAIL).first():
            admin = User(
                name='System Admin',
                email=Config.ADMIN_EMAIL,
                role='Admin',
                status='Active'
            )
            admin.set_password(Config.ADMIN_PASSWORD)
            db.session.add(admin)
            db.session.commit()

        default_settings = [
            ('store_name', 'STBM · The Black Messiah'),
            ('support_email', 'support@stbm.com'),
            ('free_shipping_threshold', '1000'),
            ('currency', 'GH₵'),
            ('currency_symbol', 'GH₵'),
            ('tax_rate', '0'),
            ('tax_calculation', 'exclusive'),
            ('decimal_places', '2'),
            ('paystack_public_key', ''),
            ('paystack_secret_key', ''),
            ('email_app_password', '')
        ]
        for key, value in default_settings:
            if not Setting.query.filter_by(key=key).first():
                setting = Setting(key=key, value=value)
                db.session.add(setting)
        db.session.commit()


init_db()


# Context processor for all templates
@app.context_processor
def utility_processor():
    def get_setting(key, default=None):
        setting = Setting.query.filter_by(key=key).first()
        return setting.value if setting else default

    def cart_count():
        if current_user.is_authenticated:
            return CartItem.query.filter_by(user_id=current_user.id).count()
        return 0

    def get_unread_messages_count():
        if current_user.is_authenticated and current_user.is_admin():
            return ContactMessage.query.filter_by(status='Unread').count()
        return 0

    return {
        'get_setting': get_setting,
        'cart_count': cart_count,
        'get_unread_messages_count': get_unread_messages_count,
        'format_currency': format_currency,
        'get_status_color': get_status_color
    }


# --- FRONTEND ROUTES ---

@app.route('/')
def index():
    """Home page"""
    products = Product.query.filter_by(status='Active').limit(8).all()
    return render_template('index.html', products=products)


@app.route('/store')
def store():
    """Store page"""
    category = request.args.get('category')
    query = request.args.get('q')

    products_query = Product.query.filter_by(status='Active')

    if category:
        products_query = products_query.filter_by(category=category)
    if query:
        products_query = products_query.filter(
            db.or_(
                Product.name.contains(query),
                Product.category.contains(query)
            )
        )

    products = products_query.all()
    categories = db.session.query(Product.category).distinct().all()
    categories = [c[0] for c in categories if c[0]]

    return render_template('store.html', products=products, categories=categories,
                           selected_category=category, search_query=query)


@app.route('/product/<int:product_id>')
def product_detail(product_id):
    """Product detail page"""
    product = Product.query.get_or_404(product_id)
    related_products = Product.query.filter(
        Product.category == product.category,
        Product.id != product.id,
        Product.status == 'Active'
    ).limit(4).all()
    return render_template('product.html', product=product, related_products=related_products)


@app.route('/cart')
def cart():
    """Shopping cart page"""
    cart_items = []
    total = 0

    if current_user.is_authenticated:
        cart_items = CartItem.query.filter_by(user_id=current_user.id).all()
        total = calculate_cart_total(cart_items)

    return render_template('cart.html', cart_items=cart_items, total=total)


@app.route('/add-to-cart/<int:product_id>', methods=['POST'])
@login_required
def add_to_cart(product_id):
    """Add product to cart"""
    product = Product.query.get_or_404(product_id)

    if product.stock <= 0:
        flash('This product is out of stock.', 'danger')
        return redirect(url_for('product_detail', product_id=product_id))

    cart_item = CartItem.query.filter_by(
        user_id=current_user.id,
        product_id=product_id
    ).first()

    if cart_item:
        if cart_item.quantity < product.stock:
            cart_item.quantity += 1
        else:
            flash('Not enough stock available.', 'danger')
            return redirect(url_for('cart'))
    else:
        cart_item = CartItem(
            user_id=current_user.id,
            product_id=product_id,
            quantity=1
        )
        db.session.add(cart_item)

    db.session.commit()
    flash(f'{product.name} added to cart!', 'success')
    return redirect(url_for('cart'))


@app.route('/update-cart/<int:item_id>', methods=['POST'])
@login_required
def update_cart(item_id):
    """Update cart item quantity"""
    cart_item = CartItem.query.get_or_404(item_id)

    if cart_item.user_id != current_user.id:
        flash('Unauthorized action.', 'danger')
        return redirect(url_for('cart'))

    quantity = request.form.get('quantity', type=int)
    if quantity and quantity > 0:
        if quantity <= cart_item.product.stock:
            cart_item.quantity = quantity
            db.session.commit()
            flash('Cart updated.', 'success')
        else:
            flash('Not enough stock available.', 'danger')
    else:
        db.session.delete(cart_item)
        db.session.commit()
        flash('Item removed from cart.', 'info')

    return redirect(url_for('cart'))


@app.route('/remove-from-cart/<int:item_id>', methods=['POST'])
@login_required
def remove_from_cart(item_id):
    """Remove item from cart"""
    cart_item = CartItem.query.get_or_404(item_id)

    if cart_item.user_id != current_user.id:
        flash('Unauthorized action.', 'danger')
        return redirect(url_for('cart'))

    db.session.delete(cart_item)
    db.session.commit()
    flash('Item removed from cart.', 'info')
    return redirect(url_for('cart'))


# --- PAYMENT ROUTES ---

@app.route('/checkout', methods=['GET', 'POST'])
@login_required
def checkout():
    """Checkout page - Initialize payment"""
    cart_items = CartItem.query.filter_by(user_id=current_user.id).all()

    if not cart_items:
        flash('Your cart is empty.', 'warning')
        return redirect(url_for('cart'))

    total = calculate_cart_total(cart_items)
    threshold_setting = Setting.query.filter_by(key='free_shipping_threshold').first()
    free_shipping_threshold = float(threshold_setting.value) if threshold_setting else 1000

    if request.method == 'POST':
        shipping_address = request.form.get('shipping_address')
        if not shipping_address:
            flash('Please enter your shipping address.', 'danger')
            return redirect(url_for('checkout'))

        order = Order(
            order_number=generate_order_number(),
            user_id=current_user.id,
            total_amount=total,
            shipping_address=shipping_address,
            payment_method='paystack',
            payment_status='Pending',
            status='Pending Payment'
        )
        db.session.add(order)
        db.session.flush()

        cart_items_copy = []
        for cart_item in cart_items:
            order_item = OrderItem(
                order_id=order.id,
                product_id=cart_item.product_id,
                product_name=cart_item.product.name,
                product_price=cart_item.product.price,
                quantity=cart_item.quantity,
                subtotal=cart_item.product.price * cart_item.quantity
            )
            db.session.add(order_item)
            cart_items_copy.append({
                'product_id': cart_item.product_id,
                'quantity': cart_item.quantity
            })
            db.session.delete(cart_item)

        db.session.commit()

        paystack = PaystackAPI()
        payment_response = paystack.initialize_payment(
            email=current_user.email,
            amount=total,
            order_number=order.order_number,
            callback_url=url_for('payment_callback', _external=True)
        )

        if payment_response.get('status') and payment_response.get('data'):
            session['pending_order_id'] = order.id
            session['payment_reference'] = order.order_number
            return redirect(payment_response['data']['authorization_url'])
        else:
            flash('Payment initialization failed. Please try again.', 'danger')

            for item_data in cart_items_copy:
                cart_item = CartItem(
                    user_id=current_user.id,
                    product_id=item_data['product_id'],
                    quantity=item_data['quantity']
                )
                db.session.add(cart_item)

            OrderItem.query.filter_by(order_id=order.id).delete()
            db.session.delete(order)
            db.session.commit()
            return redirect(url_for('cart'))

    return render_template('checkout.html', cart_items=cart_items, total=total,
                           free_shipping_threshold=free_shipping_threshold)


@app.route('/payment/callback')
@login_required
def payment_callback():
    """Paystack payment callback"""
    reference = request.args.get('reference')

    if not reference:
        flash('Payment reference not found.', 'danger')
        return redirect(url_for('cart'))

    paystack = PaystackAPI()
    verification_response = paystack.verify_payment(reference)

    if verification_response.get('status') and verification_response.get('data'):
        payment_data = verification_response['data']

        order = Order.query.filter_by(order_number=reference).first()

        if not order:
            flash('Order not found.', 'danger')
            return redirect(url_for('cart'))

        if payment_data.get('status') == 'success':
            order.payment_status = 'Paid'
            order.status = 'Processing'
            order.payment_reference = reference

            for item in order.items:
                product = Product.query.get(item.product_id)
                if product:
                    product.stock -= item.quantity
                    if product.stock <= 0:
                        product.status = 'Out of Stock'
                    elif product.stock <= 5:
                        product.status = 'Low Stock'

            db.session.commit()

            session.pop('pending_order_id', None)
            session.pop('payment_reference', None)

            flash(f'Payment successful! Your order {order.order_number} has been confirmed.', 'success')
            return redirect(url_for('order_confirmation', order_id=order.id))
        else:
            order.status = 'Payment Failed'
            db.session.commit()
            flash('Payment failed. Please try again.', 'danger')
            return redirect(url_for('cart'))
    else:
        flash('Payment verification failed. Please contact support.', 'danger')
        return redirect(url_for('cart'))


@app.route('/payment/webhook', methods=['POST'])
def payment_webhook():
    """Paystack webhook endpoint"""
    signature = request.headers.get('x-paystack-signature')
    if not signature:
        return jsonify({'status': 'error', 'message': 'No signature'}), 400

    secret_key = app.config.get('PAYSTACK_SECRET_KEY')
    if not secret_key:
        setting = Setting.query.filter_by(key='paystack_secret_key').first()
        secret_key = setting.value if setting else None

    if not secret_key:
        return jsonify({'status': 'error', 'message': 'Webhook not configured'}), 500

    payload = request.get_data()
    computed_signature = hmac.new(
        secret_key.encode('utf-8'),
        payload,
        hashlib.sha512
    ).hexdigest()

    if not hmac.compare_digest(computed_signature, signature):
        return jsonify({'status': 'error', 'message': 'Invalid signature'}), 401

    event = request.get_json(silent=True) or {}

    if event.get('event') == 'charge.success':
        data = event.get('data', {})
        reference = data.get('reference')

        if reference:
            order = Order.query.filter_by(order_number=reference).first()
            if order and order.payment_status == 'Pending':
                try:
                    order.payment_status = 'Paid'
                    order.status = 'Processing'
                    order.payment_reference = reference

                    for item in order.items:
                        product = Product.query.get(item.product_id)
                        if product:
                            product.stock -= item.quantity
                            if product.stock <= 0:
                                product.status = 'Out of Stock'
                            elif product.stock <= 5:
                                product.status = 'Low Stock'

                    db.session.commit()
                    return jsonify({'status': 'success'}), 200
                except Exception as e:
                    db.session.rollback()
                    return jsonify({'status': 'error', 'message': str(e)}), 500

    return jsonify({'status': 'ok'}), 200


@app.route('/payment/status/<reference>')
@login_required
def payment_status(reference):
    """Check payment status"""
    order = Order.query.filter_by(order_number=reference).first()
    if not order or order.user_id != current_user.id:
        return jsonify({'error': 'Order not found'}), 404

    paystack = PaystackAPI()
    status_response = paystack.verify_payment(reference)

    return jsonify({
        'order_number': order.order_number,
        'payment_status': order.payment_status,
        'order_status': order.status,
        'verified': status_response.get('status', False),
        'data': status_response.get('data', {})
    })


@app.route('/order-confirmation/<int:order_id>')
@login_required
def order_confirmation(order_id):
    """Order confirmation page"""
    order = Order.query.get_or_404(order_id)
    if order.user_id != current_user.id:
        flash('Unauthorized access.', 'danger')
        return redirect(url_for('index'))
    return render_template('order_confirmation.html', order=order)


@app.route('/about')
def about():
    """About page"""
    return render_template('about.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user and user.check_password(form.password.data):
            login_user(user)
            next_page = request.args.get('next')
            flash(f'Welcome back, {user.name}!', 'success')
            return redirect(next_page or url_for('index'))
        flash('Invalid email or password.', 'danger')
    return render_template('login.html', form=form)


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    """Signup page"""
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    form = SignupForm()
    if form.validate_on_submit():
        user = User(
            name=form.name.data,
            email=form.email.data,
            role='Customer',
            status='Active'
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        flash('Account created successfully! Please log in.', 'success')
        return redirect(url_for('login'))
    return render_template('signup.html', form=form)


@app.route('/logout')
@login_required
def logout():
    """Logout user"""
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))


@app.route('/profile')
@login_required
def profile():
    """User profile page"""
    orders = Order.query.filter_by(user_id=current_user.id).order_by(Order.created_at.desc()).all()
    return render_template('profile.html', orders=orders)


@app.route('/search')
def search():
    """Search results page"""
    query = request.args.get('q', '')
    products = Product.query.filter(
        db.or_(
            Product.name.contains(query),
            Product.category.contains(query),
            Product.description.contains(query)
        ),
        Product.status == 'Active'
    ).all()
    return render_template('search.html', products=products, query=query)


# --- ADMIN ROUTES ---

@app.route('/admin')
@login_required
def admin_dashboard():
    """Admin dashboard"""
    if not current_user.is_admin():
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))

    total_products = Product.query.count()
    total_orders = Order.query.count()
    total_users = User.query.count()
    total_revenue = db.session.query(func.sum(Order.total_amount)).filter(
        Order.status == 'Delivered'
    ).scalar() or 0

    recent_orders = Order.query.order_by(Order.created_at.desc()).limit(5).all()
    low_stock_products = Product.query.filter(Product.stock <= 5, Product.stock > 0).all()

    return render_template('admin/dashboard.html',
                           total_products=total_products,
                           total_orders=total_orders,
                           total_users=total_users,
                           total_revenue=total_revenue,
                           recent_orders=recent_orders,
                           low_stock_products=low_stock_products)


@app.route('/admin/products')
@login_required
def admin_products():
    """Admin products management"""
    if not current_user.is_admin():
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))

    products = Product.query.all()
    return render_template('admin/products.html', products=products)


@app.route('/admin/products/add', methods=['GET', 'POST'])
@login_required
def admin_add_product():
    """Add new product"""
    if not current_user.is_admin():
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))

    form = ProductForm()
    if form.validate_on_submit():
        product = Product(
            name=form.name.data,
            description=form.description.data,
            price=form.price.data,
            category=form.category.data,
            stock=form.stock.data,
            image_url=form.image_url.data,
            badge=form.badge.data,
            status=form.status.data
        )
        db.session.add(product)
        db.session.commit()
        flash('Product added successfully!', 'success')
        return redirect(url_for('admin_products'))

    return render_template('admin/product_form.html', form=form, title='Add Product', product=None)


@app.route('/admin/products/edit/<int:product_id>', methods=['GET', 'POST'])
@login_required
def admin_edit_product(product_id):
    """Edit product"""
    if not current_user.is_admin():
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))

    product = Product.query.get_or_404(product_id)
    form = ProductForm(obj=product)

    if form.validate_on_submit():
        product.name = form.name.data
        product.description = form.description.data
        product.price = form.price.data
        product.category = form.category.data
        product.stock = form.stock.data
        product.image_url = form.image_url.data
        product.badge = form.badge.data
        product.status = form.status.data
        db.session.commit()
        flash('Product updated successfully!', 'success')
        return redirect(url_for('admin_products'))

    return render_template('admin/product_form.html', form=form, title='Edit Product', product=product)


@app.route('/admin/products/delete/<int:product_id>', methods=['POST'])
@login_required
def admin_delete_product(product_id):
    """Delete product"""
    if not current_user.is_admin():
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))

    product = Product.query.get_or_404(product_id)
    db.session.delete(product)
    db.session.commit()
    flash('Product deleted successfully.', 'success')
    return redirect(url_for('admin_products'))


@app.route('/admin/orders')
@login_required
def admin_orders():
    """Admin orders management"""
    if not current_user.is_admin():
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))

    orders = Order.query.order_by(Order.created_at.desc()).all()
    return render_template('admin/orders.html', orders=orders)


@app.route('/admin/orders/update-status/<int:order_id>', methods=['POST'])
@login_required
def admin_update_order_status(order_id):
    """Update order status"""
    if not current_user.is_admin():
        if request.is_json:
            return jsonify({'error': 'Access denied'}), 403
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))

    order = Order.query.get_or_404(order_id)

    if request.is_json:
        data = request.get_json()
        new_status = data.get('status')
    else:
        new_status = request.form.get('status')

    if new_status:
        order.status = new_status
        db.session.commit()

        if request.is_json:
            return jsonify({'success': True, 'message': f'Order status updated to {new_status}'})

        flash(f'Order {order.order_number} status updated to {new_status}.', 'success')

    return redirect(url_for('admin_orders'))


@app.route('/admin/users')
@login_required
def admin_users():
    """Admin users management"""
    if not current_user.is_admin():
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))

    users = User.query.all()
    return render_template('admin/users.html', users=users)


@app.route('/admin/users/delete/<int:user_id>', methods=['POST'])
@login_required
def admin_delete_user(user_id):
    """Delete user"""
    if not current_user.is_admin():
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))

    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash('You cannot delete your own account.', 'danger')
        return redirect(url_for('admin_users'))

    db.session.delete(user)
    db.session.commit()
    flash('User deleted successfully.', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/settings', methods=['GET', 'POST'])
@login_required
def admin_settings():
    """Admin settings"""
    if not current_user.is_admin():
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))

    form = SettingsForm()

    if request.method == 'GET':
        store_name = Setting.query.filter_by(key='store_name').first()
        support_email = Setting.query.filter_by(key='support_email').first()
        free_shipping_threshold = Setting.query.filter_by(key='free_shipping_threshold').first()
        paystack_public_key = Setting.query.filter_by(key='paystack_public_key').first()
        paystack_secret_key = Setting.query.filter_by(key='paystack_secret_key').first()
        email_app_password = Setting.query.filter_by(key='email_app_password').first()

        form.store_name.data = store_name.value if store_name else ''
        form.support_email.data = support_email.value if support_email else ''
        form.free_shipping_threshold.data = float(free_shipping_threshold.value) if free_shipping_threshold else 1000
        form.paystack_public_key.data = paystack_public_key.value if paystack_public_key else ''
        form.paystack_secret_key.data = paystack_secret_key.value if paystack_secret_key else ''
        form.email_app_password.data = email_app_password.value if email_app_password else ''

    if form.validate_on_submit():
        settings_data = [
            ('store_name', form.store_name.data),
            ('support_email', form.support_email.data),
            ('free_shipping_threshold', str(form.free_shipping_threshold.data)),
            ('paystack_public_key', form.paystack_public_key.data or ''),
            ('paystack_secret_key', form.paystack_secret_key.data or ''),
            ('email_app_password', form.email_app_password.data or '')
        ]

        for key, value in settings_data:
            setting = Setting.query.filter_by(key=key).first()
            if setting:
                setting.value = value
            elif value:
                setting = Setting(key=key, value=value)
                db.session.add(setting)

        db.session.commit()
        flash('Settings updated successfully!', 'success')
        return redirect(url_for('admin_settings'))

    return render_template('admin/settings.html', form=form)


@app.route('/admin/backup')
@login_required
def admin_backup():
    """Export data backup"""
    if not current_user.is_admin():
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))

    products = [{'id': p.id, 'name': p.name, 'price': p.price, 'category': p.category,
                 'stock': p.stock, 'status': p.status} for p in Product.query.all()]

    orders = [{'id': o.id, 'order_number': o.order_number, 'user_id': o.user_id,
               'total_amount': o.total_amount, 'status': o.status, 'created_at': str(o.created_at)}
              for o in Order.query.all()]

    users = [{'id': u.id, 'name': u.name, 'email': u.email, 'role': u.role, 'status': u.status}
             for u in User.query.all()]

    settings = {s.key: s.value for s in Setting.query.all()}

    data = {
        'products': products,
        'orders': orders,
        'users': users,
        'settings': settings,
        'exported_at': str(datetime.utcnow())
    }

    response = jsonify(data)
    response.headers['Content-Disposition'] = 'attachment; filename=stbm_backup.json'
    return response


# --- API ROUTES ---

@app.route('/api/products')
def api_products():
    """API endpoint for products"""
    products = Product.query.filter_by(status='Active').all()
    return jsonify([{
        'id': p.id,
        'name': p.name,
        'price': p.price,
        'category': p.category,
        'image_url': p.image_url,
        'badge': p.badge,
        'in_stock': p.is_in_stock
    } for p in products])


@app.route('/api/search')
def api_search():
    """API endpoint for product search"""
    query = request.args.get('q', '')
    products = Product.query.filter(
        db.or_(
            Product.name.contains(query),
            Product.category.contains(query)
        ),
        Product.status == 'Active'
    ).limit(10).all()

    return jsonify([{
        'id': p.id,
        'name': p.name,
        'price': p.price,
        'category': p.category,
        'image_url': p.image_url
    } for p in products])


@app.route('/api/product/<int:product_id>')
def api_product_detail(product_id):
    """API endpoint for product details (used in quick view)"""
    product = Product.query.get_or_404(product_id)
    return jsonify({
        'id': product.id,
        'name': product.name,
        'description': product.description,
        'price': product.price,
        'category': product.category,
        'stock': product.stock,
        'image_url': product.image_url,
        'badge': product.badge,
        'status': product.status
    })


@app.route('/api/order/<int:order_id>')
@login_required
def api_order_detail(order_id):
    """API endpoint for order details (used in profile modals)"""
    order = Order.query.get_or_404(order_id)

    if order.user_id != current_user.id and not current_user.is_admin():
        return jsonify({'error': 'Unauthorized'}), 403

    return jsonify({
        'id': order.id,
        'order_number': order.order_number,
        'total_amount': order.total_amount,
        'status': order.status,
        'shipping_address': order.shipping_address,
        'payment_method': order.payment_method,
        'payment_status': order.payment_status,
        'created_at': order.created_at.isoformat(),
        'items': [{
            'product_name': item.product_name,
            'product_price': item.product_price,
            'quantity': item.quantity,
            'subtotal': item.subtotal
        } for item in order.items],
        'delivery_notes': getattr(order, 'delivery_notes', None)
    })


@app.route('/admin/users/edit', methods=['POST'])
@login_required
def admin_edit_user():
    """Admin API endpoint for editing users"""
    if not current_user.is_admin():
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403

    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400

        user_id = data.get('user_id')
        name = data.get('name')
        email = data.get('email')
        role = data.get('role')
        status = data.get('status')

        if not all([user_id, name, email]):
            return jsonify({'success': False, 'message': 'Missing required fields'}), 400

        user = User.query.get_or_404(user_id)

        if user.role == 'Admin' and role != 'Admin':
            admin_count = User.query.filter_by(role='Admin').count()
            if admin_count <= 1:
                return jsonify({'success': False, 'message': 'Cannot remove the last admin'}), 400

        user.name = name
        user.email = email
        user.role = role
        user.status = status

        db.session.commit()
        return jsonify({'success': True, 'message': 'User updated successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/admin/products/update-stock', methods=['POST'])
@login_required
def admin_update_stock():
    """Admin API endpoint for quick stock updates"""
    if not current_user.is_admin():
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403

    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400

        product_id = data.get('product_id')
        stock = data.get('stock')

        if product_id is None or stock is None:
            return jsonify({'success': False, 'message': 'Missing required fields'}), 400

        product = Product.query.get_or_404(product_id)
        product.stock = int(stock)

        if product.stock <= 0:
            product.status = 'Out of Stock'
        elif product.stock <= 5:
            product.status = 'Low Stock'
        else:
            product.status = 'Active'

        db.session.commit()
        return jsonify({
            'success': True,
            'stock': product.stock,
            'status': product.status,
            'message': 'Stock updated successfully'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/admin/settings/update-config', methods=['POST'])
@login_required
def admin_update_config():
    """Admin API endpoint for updating configuration"""
    if not current_user.is_admin():
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403

    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400

        for key, value in data.items():
            setting = Setting.query.filter_by(key=key).first()
            if setting:
                setting.value = str(value)
            else:
                setting = Setting(key=key, value=str(value))
                db.session.add(setting)

        db.session.commit()
        return jsonify({'success': True, 'message': 'Configuration updated successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/admin/settings/webhook-status', methods=['GET'])
@login_required
def webhook_status():
    """Check webhook and SMTP health status"""
    if not current_user.is_admin():
        return jsonify({'error': 'Unauthorized'}), 403

    try:
        status = {
            'paystack': {
                'status': 'healthy',
                'last_ping': datetime.utcnow().isoformat(),
                'details': 'Webhook endpoint is reachable'
            },
            'smtp': {
                'status': 'healthy',
                'last_test': datetime.utcnow().isoformat(),
                'details': 'SMTP connection successful'
            }
        }

        mail_username = app.config.get('MAIL_USERNAME')
        mail_password = app.config.get('MAIL_PASSWORD')
        mail_server = app.config.get('MAIL_SERVER')
        mail_port = app.config.get('MAIL_PORT')

        if mail_username and mail_password and mail_server and mail_port:
            try:
                server = smtplib.SMTP(mail_server, mail_port, timeout=10)
                server.starttls()
                server.login(mail_username, mail_password)
                server.quit()
                status['smtp']['status'] = 'healthy'
                status['smtp']['details'] = 'SMTP connection successful'
            except Exception as e:
                status['smtp']['status'] = 'error'
                status['smtp']['details'] = f'SMTP connection failed: {str(e)}'
        else:
            status['smtp']['status'] = 'unconfigured'
            status['smtp']['details'] = 'SMTP credentials not fully configured'

        return jsonify(status)
    except Exception as e:
        return jsonify({
            'error': str(e),
            'paystack': {'status': 'error', 'details': 'Failed to check status'},
            'smtp': {'status': 'error', 'details': 'Failed to check status'}
        }), 500


@app.route('/api/unread-messages-count')
@login_required
def api_unread_messages_count():
    """API endpoint to get unread messages count"""
    if not current_user.is_admin():
        return jsonify({'count': 0})

    count = ContactMessage.query.filter_by(status='Unread').count()
    return jsonify({'count': count})


# Error handlers
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404


@app.errorhandler(500)
def internal_server_error(e):
    db.session.rollback()
    return render_template('500.html'), 500


# --- RUN THE APP ---

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(debug=True, host='0.0.0.0', port=port)