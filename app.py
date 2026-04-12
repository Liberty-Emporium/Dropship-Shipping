"""
Andyping Shipping App - Multi-Tenant
Track orders and manage shipping for dropshipping business
"""

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, g
import os
import json
import uuid
import hashlib
import sqlite3
from datetime import datetime
from functools import wraps
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dropship-secret-key-2024')

DATA_DIR = os.environ.get('DATA_DIR', '/data')
os.makedirs(DATA_DIR, exist_ok=True)

PLAN_LIMITS = {
    'free':       {'orders': 50,    'products': 10,  'suppliers': 3},
    'pro':        {'orders': 99999, 'products': 9999, 'suppliers': 999},
    'enterprise': {'orders': 99999, 'products': 9999, 'suppliers': 999},
}

# ============== DATABASE ==============
DB_FILE = os.path.join(DATA_DIR, 'dropship.db')

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_FILE)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    db = sqlite3.connect(DB_FILE)
    db.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            plan TEXT NOT NULL DEFAULT 'free',
            stripe_customer_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            customer_name TEXT,
            customer_email TEXT,
            customer_address TEXT,
            customer_city TEXT,
            customer_state TEXT,
            customer_zip TEXT,
            product_id TEXT,
            product_name TEXT,
            quantity INTEGER DEFAULT 1,
            price REAL DEFAULT 0,
            shipping_cost REAL DEFAULT 0,
            total REAL DEFAULT 0,
            status TEXT DEFAULT 'pending',
            tracking_number TEXT,
            carrier TEXT,
            auto_fulfilled INTEGER DEFAULT 0,
            shipped_at TIMESTAMP,
            delivered_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS products (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT,
            sku TEXT,
            supplier TEXT,
            cost REAL DEFAULT 0,
            price REAL DEFAULT 0,
            weight REAL DEFAULT 0,
            stock INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS suppliers (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT,
            email TEXT,
            phone TEXT,
            address TEXT,
            website TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS customers (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT,
            email TEXT,
            phone TEXT,
            address TEXT,
            total_orders INTEGER DEFAULT 0,
            total_spent REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS user_api_keys (
            user_id TEXT PRIMARY KEY,
            groq_key TEXT DEFAULT '',
            qwen_key TEXT DEFAULT '',
            active_provider TEXT DEFAULT 'qwen',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS admin_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    ''')
    # Default admin account
    existing = db.execute("SELECT id FROM users WHERE email = 'admin'").fetchone()
    if not existing:
        db.execute(
            "INSERT INTO users (id, email, name, password_hash, plan) VALUES (?,?,?,?,?)",
            (str(uuid.uuid4()), 'admin', 'Admin', hash_password('admin1'), 'enterprise')
        )
    db.commit()
    db.close()

init_db()

# ============== HELPERS ==============

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password, hashed):
    return hash_password(password) == hashed

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to continue.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def plan_required(min_plan):
    order = ['free', 'pro', 'enterprise']
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login'))
            db = get_db()
            user = db.execute('SELECT plan FROM users WHERE id = ?', (session['user_id'],)).fetchone()
            if not user or order.index(user['plan']) < order.index(min_plan):
                flash(f'This feature requires the {min_plan.title()} plan.', 'error')
                return redirect(url_for('upgrade'))
            return f(*args, **kwargs)
        return decorated
    return decorator

def get_current_user():
    if 'user_id' not in session:
        return None
    return get_db().execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()

def get_admin_setting(key, default=''):
    row = get_db().execute('SELECT value FROM admin_settings WHERE key = ?', (key,)).fetchone()
    return row['value'] if row else default

def set_admin_setting(key, value):
    db = get_db()
    db.execute('INSERT OR REPLACE INTO admin_settings (key, value) VALUES (?,?)', (key, value))
    db.commit()

def get_user_api_keys(user_id=None):
    db = get_db()
    if user_id:
        row = db.execute('SELECT * FROM user_api_keys WHERE user_id = ?', (user_id,)).fetchone()
        if row and (row['groq_key'] or row['qwen_key']):
            return {'groq_key': row['groq_key'], 'qwen_key': row['qwen_key'], 'active_provider': row['active_provider']}
    groq = get_admin_setting('groq_key', '')
    qwen = get_admin_setting('qwen_key', '')
    provider = get_admin_setting('active_provider', 'qwen')
    return {'groq_key': groq, 'qwen_key': qwen, 'active_provider': provider}

def get_ceo_for_user(user_id=None):
    from ai_ceo import AICEO
    keys = get_user_api_keys(user_id)
    return AICEO(api_keys=keys, active_provider=keys.get('active_provider', 'qwen'))

def uid():
    return str(uuid.uuid4())

# ============== AUTH ==============

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email', '').lower().strip()
        password = request.form.get('password', '')
        name = request.form.get('name', '')
        if not email or not password or not name:
            flash('Please fill in all fields.', 'error')
            return redirect(url_for('register'))
        db = get_db()
        if db.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone():
            flash('Email already registered.', 'error')
            return redirect(url_for('register'))
        user_id = uid()
        db.execute('INSERT INTO users (id, email, name, password_hash, plan) VALUES (?,?,?,?,?)',
                   (user_id, email, name, hash_password(password), 'free'))
        db.commit()
        session['user_id'] = user_id
        session['user_name'] = name
        flash('Account created! Welcome!', 'success')
        return redirect(url_for('index'))
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').lower().strip()
        password = request.form.get('password', '')
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
        if user and verify_password(password, user['password_hash']):
            session['user_id'] = user['id']
            session['user_name'] = user['name']
            flash('Logged in!', 'success')
            return redirect(url_for('index'))
        flash('Invalid email or password.', 'error')
        return redirect(url_for('login'))
    return render_template('landing.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    return redirect(url_for('register'))

# ============== DASHBOARD ==============

@app.route('/')
@login_required
def index():
    user_id = session['user_id']
    db = get_db()
    total_orders = db.execute('SELECT COUNT(*) FROM orders WHERE user_id=?', (user_id,)).fetchone()[0]
    pending_shipping = db.execute("SELECT COUNT(*) FROM orders WHERE user_id=? AND status='pending'", (user_id,)).fetchone()[0]
    delivered = db.execute("SELECT COUNT(*) FROM orders WHERE user_id=? AND status='delivered'", (user_id,)).fetchone()[0]
    total_revenue = db.execute('SELECT COALESCE(SUM(total),0) FROM orders WHERE user_id=?', (user_id,)).fetchone()[0]
    suppliers_count = db.execute('SELECT COUNT(*) FROM suppliers WHERE user_id=?', (user_id,)).fetchone()[0]
    products_count = db.execute('SELECT COUNT(*) FROM products WHERE user_id=?', (user_id,)).fetchone()[0]
    return render_template('index.html',
                           total_orders=total_orders,
                           pending_shipping=pending_shipping,
                           delivered=delivered,
                           total_revenue=total_revenue,
                           suppliers=suppliers_count,
                           products=products_count)

# ============== ORDERS ==============

@app.route('/orders')
@login_required
def list_orders():
    user_id = session['user_id']
    status_filter = request.args.get('status', '')
    db = get_db()
    if status_filter:
        orders = db.execute('SELECT * FROM orders WHERE user_id=? AND status=? ORDER BY created_at DESC',
                            (user_id, status_filter)).fetchall()
    else:
        orders = db.execute('SELECT * FROM orders WHERE user_id=? ORDER BY created_at DESC', (user_id,)).fetchall()
    return render_template('orders.html', orders=orders)

@app.route('/order/<order_id>')
@login_required
def order_detail(order_id):
    db = get_db()
    order = db.execute('SELECT * FROM orders WHERE id=? AND user_id=?', (order_id, session['user_id'])).fetchone()
    if not order:
        flash('Order not found', 'error')
        return redirect(url_for('list_orders'))
    return render_template('order_detail.html', order=order)

@app.route('/order/add', methods=['GET', 'POST'])
@login_required
def add_order():
    user_id = session['user_id']
    db = get_db()
    if request.method == 'POST':
        price = float(request.form.get('price', 0))
        quantity = int(request.form.get('quantity', 1))
        shipping_cost = float(request.form.get('shipping_cost', 0))
        total = (price * quantity) + shipping_cost
        order_id = uid()
        db.execute('''INSERT INTO orders (id, user_id, customer_name, customer_email, customer_address,
            customer_city, customer_state, customer_zip, product_id, product_name, quantity, price,
            shipping_cost, total, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (order_id, user_id,
             request.form.get('customer_name'), request.form.get('customer_email'),
             request.form.get('customer_address'), request.form.get('customer_city'),
             request.form.get('customer_state'), request.form.get('customer_zip'),
             request.form.get('product_id'), request.form.get('product_name'),
             quantity, price, shipping_cost, total, 'pending'))
        db.commit()
        flash('Order created!', 'success')
        return redirect(url_for('order_detail', order_id=order_id))
    products = db.execute('SELECT * FROM products WHERE user_id=?', (user_id,)).fetchall()
    return render_template('add_order.html', products=products)

@app.route('/order/<order_id>/ship', methods=['GET', 'POST'])
@login_required
def ship_order(order_id):
    user_id = session['user_id']
    db = get_db()
    order = db.execute('SELECT * FROM orders WHERE id=? AND user_id=?', (order_id, user_id)).fetchone()
    if not order:
        flash('Order not found', 'error')
        return redirect(url_for('list_orders'))
    if request.method == 'POST':
        db.execute("UPDATE orders SET tracking_number=?, carrier=?, status='shipped', shipped_at=? WHERE id=? AND user_id=?",
                   (request.form.get('tracking_number'), request.form.get('carrier'),
                    datetime.now().isoformat(), order_id, user_id))
        db.commit()
        flash('Order marked as shipped!', 'success')
        return redirect(url_for('order_detail', order_id=order_id))
    return render_template('ship_order.html', order=order)

@app.route('/order/<order_id>/delivered')
@login_required
def mark_delivered(order_id):
    user_id = session['user_id']
    db = get_db()
    db.execute("UPDATE orders SET status='delivered', delivered_at=? WHERE id=? AND user_id=?",
               (datetime.now().isoformat(), order_id, user_id))
    db.commit()
    flash('Order marked as delivered!', 'success')
    return redirect(url_for('order_detail', order_id=order_id))

# ============== PRODUCTS ==============

@app.route('/products')
@login_required
def list_products():
    products = get_db().execute('SELECT * FROM products WHERE user_id=? ORDER BY created_at DESC', (session['user_id'],)).fetchall()
    return render_template('products.html', products=products)

@app.route('/product/add', methods=['GET', 'POST'])
@login_required
def add_product():
    user_id = session['user_id']
    db = get_db()
    if request.method == 'POST':
        db.execute('INSERT INTO products (id, user_id, name, sku, supplier, cost, price, weight, stock) VALUES (?,?,?,?,?,?,?,?,?)',
                   (uid(), user_id, request.form.get('name'), request.form.get('sku'),
                    request.form.get('supplier'), float(request.form.get('cost', 0)),
                    float(request.form.get('price', 0)), float(request.form.get('weight', 0)),
                    int(request.form.get('stock', 0))))
        db.commit()
        flash('Product added!', 'success')
        return redirect(url_for('list_products'))
    suppliers = db.execute('SELECT * FROM suppliers WHERE user_id=?', (user_id,)).fetchall()
    return render_template('add_product.html', suppliers=suppliers)

@app.route('/products/import', methods=['GET', 'POST'])
@login_required
def import_products():
    user_id = session['user_id']
    if request.method == 'POST':
        file = request.files.get('file')
        if file:
            content = file.read().decode('utf-8')
            lines = content.strip().split('\n')
            db = get_db()
            imported = 0
            for i, line in enumerate(lines):
                if i == 0:
                    continue
                parts = line.split(',')
                if len(parts) >= 4:
                    db.execute('INSERT INTO products (id, user_id, name, sku, cost, price, supplier, stock) VALUES (?,?,?,?,?,?,?,?)',
                               (uid(), user_id, parts[0].strip(), parts[1].strip(),
                                float(parts[2].strip() or 0), float(parts[3].strip() or 0),
                                parts[4].strip() if len(parts) > 4 else '', 100))
                    imported += 1
            db.commit()
            flash(f'Imported {imported} products!', 'success')
        return redirect(url_for('list_products'))
    return render_template('import_products.html')

# ============== SUPPLIERS ==============

@app.route('/suppliers')
@login_required
def list_suppliers():
    suppliers = get_db().execute('SELECT * FROM suppliers WHERE user_id=? ORDER BY created_at DESC', (session['user_id'],)).fetchall()
    return render_template('suppliers.html', suppliers=suppliers)

@app.route('/supplier/add', methods=['GET', 'POST'])
@login_required
def add_supplier():
    user_id = session['user_id']
    if request.method == 'POST':
        db = get_db()
        db.execute('INSERT INTO suppliers (id, user_id, name, email, phone, address, website, notes) VALUES (?,?,?,?,?,?,?,?)',
                   (uid(), user_id, request.form.get('name'), request.form.get('email'),
                    request.form.get('phone'), request.form.get('address'),
                    request.form.get('website'), request.form.get('notes')))
        db.commit()
        flash('Supplier added!', 'success')
        return redirect(url_for('list_suppliers'))
    return render_template('add_supplier.html')

# ============== CUSTOMERS ==============

@app.route('/customers')
@login_required
def list_customers():
    customers = get_db().execute('SELECT * FROM customers WHERE user_id=? ORDER BY created_at DESC', (session['user_id'],)).fetchall()
    return render_template('customers.html', customers=customers)

@app.route('/customer/add', methods=['GET', 'POST'])
@login_required
def add_customer():
    user_id = session['user_id']
    if request.method == 'POST':
        db = get_db()
        db.execute('INSERT INTO customers (id, user_id, name, email, phone, address) VALUES (?,?,?,?,?,?)',
                   (uid(), user_id, request.form.get('name'), request.form.get('email'),
                    request.form.get('phone'), request.form.get('address')))
        db.commit()
        flash('Customer added!', 'success')
        return redirect(url_for('list_customers'))
    return render_template('add_customer.html')

# ============== ANALYTICS ==============

@app.route('/analytics')
@login_required
def analytics():
    user_id = session['user_id']
    db = get_db()
    orders = db.execute('SELECT * FROM orders WHERE user_id=?', (user_id,)).fetchall()
    products = db.execute('SELECT * FROM products WHERE user_id=?', (user_id,)).fetchall()

    total_revenue = sum(o['total'] for o in orders)
    total_orders = len(orders)
    avg_order_value = total_revenue / total_orders if total_orders > 0 else 0
    pending = sum(1 for o in orders if o['status'] == 'pending')
    shipped = sum(1 for o in orders if o['status'] == 'shipped')
    delivered = sum(1 for o in orders if o['status'] == 'delivered')

    orders_by_day = {}
    for o in orders:
        day = (o['created_at'] or '')[:10]
        if day:
            orders_by_day[day] = orders_by_day.get(day, 0) + 1

    product_sales = {}
    for o in orders:
        prod = o['product_name'] or 'Unknown'
        product_sales[prod] = product_sales.get(prod, 0) + 1
    top_products = sorted(product_sales.items(), key=lambda x: x[1], reverse=True)[:5]

    return render_template('analytics.html',
                           total_revenue=total_revenue, total_orders=total_orders,
                           avg_order_value=avg_order_value, pending=pending,
                           shipped=shipped, delivered=delivered,
                           orders_by_day=orders_by_day, top_products=top_products)

# ============== ALERTS ==============

@app.route('/alerts')
@login_required
def inventory_alerts():
    user_id = session['user_id']
    db = get_db()
    products = db.execute('SELECT * FROM products WHERE user_id=?', (user_id,)).fetchall()
    orders = db.execute("SELECT * FROM orders WHERE user_id=? AND status='pending'", (user_id,)).fetchall()
    low_stock = [p for p in products if p['stock'] < 10]
    out_of_stock = [p for p in products if p['stock'] <= 0]
    return render_template('alerts.html', low_stock=low_stock, out_of_stock=out_of_stock, pending_orders=orders)

# ============== UPGRADE / BILLING ==============

@app.route('/upgrade', methods=['GET', 'POST'])
@login_required
def upgrade():
    user = get_current_user()
    if request.method == 'POST':
        plan = request.form.get('plan', 'free')
        db = get_db()
        db.execute('UPDATE users SET plan=? WHERE id=?', (plan, user['id']))
        db.commit()
        flash(f'Upgraded to {plan.title()} plan!', 'success')
        return redirect(url_for('index'))
    return render_template('success.html', current_plan=user['plan'])

# ============== SETTINGS ==============

@app.route('/admin/ai-settings', methods=['GET', 'POST'])
@login_required
def admin_ai_settings():
    user = get_current_user()
    if user['plan'] != 'enterprise':
        flash('Admin settings require enterprise plan.', 'error')
        return redirect(url_for('index'))
    if request.method == 'POST':
        for k in ['groq_key', 'qwen_key', 'active_provider']:
            val = request.form.get(k, '').strip()
            if val:
                set_admin_setting(k, val)
        flash('Admin AI settings saved!', 'success')
        return redirect(url_for('admin_ai_settings'))
    keys = {k: get_admin_setting(k, '') for k in ['groq_key', 'qwen_key', 'active_provider']}
    for k in ['groq_key', 'qwen_key']:
        if keys[k]:
            keys[k] = keys[k][:8] + '...'
    return render_template('admin_ai_settings.html', keys=keys)

@app.route('/my-settings', methods=['GET', 'POST'])
@login_required
def my_settings():
    user_id = session['user_id']
    db = get_db()
    if request.method == 'POST':
        db.execute('INSERT OR REPLACE INTO user_api_keys (user_id, groq_key, qwen_key, active_provider, updated_at) VALUES (?,?,?,?,CURRENT_TIMESTAMP)',
                   (user_id, request.form.get('groq_key','').strip(),
                    request.form.get('qwen_key','').strip(),
                    request.form.get('active_provider','qwen')))
        db.commit()
        flash('Your AI settings saved!', 'success')
        return redirect(url_for('my_settings'))
    row = db.execute('SELECT * FROM user_api_keys WHERE user_id=?', (user_id,)).fetchone()
    keys = dict(row) if row else {'groq_key': '', 'qwen_key': '', 'active_provider': 'qwen'}
    for k in ['groq_key', 'qwen_key']:
        if keys.get(k):
            keys[k] = keys[k][:8] + '...'
    return render_template('my_settings.html', keys=keys)

@app.route('/settings/pricing', methods=['GET', 'POST'])
@login_required
def pricing_settings():
    user_id = session['user_id']
    db = get_db()
    key = f'pricing_{user_id}'
    if request.method == 'POST':
        settings = json.dumps({
            'default_markup_percent': float(request.form.get('default_markup', 50)),
            'min_profit_percent': float(request.form.get('min_profit', 20)),
            'shipping_handling': float(request.form.get('shipping_handling', 5)),
            'platform_fee_percent': float(request.form.get('platform_fee', 2.9)),
        })
        set_admin_setting(key, settings)
        flash('Pricing settings saved!', 'success')
        return redirect(url_for('pricing_settings'))
    raw = get_admin_setting(key, '{}')
    settings = json.loads(raw) if raw else {}
    return render_template('pricing_settings.html', settings=settings)

@app.route('/settings/email', methods=['GET', 'POST'])
@login_required
def email_settings_page():
    user_id = session['user_id']
    key = f'email_{user_id}'
    if request.method == 'POST':
        settings = json.dumps({
            'enabled': 'enabled' in request.form,
            'smtp_host': request.form.get('smtp_host', ''),
            'smtp_port': request.form.get('smtp_port', '587'),
            'username': request.form.get('username', ''),
            'password': request.form.get('password', ''),
            'from_email': request.form.get('from_email', ''),
        })
        set_admin_setting(key, settings)
        flash('Email settings saved!', 'success')
        return redirect(url_for('email_settings_page'))
    raw = get_admin_setting(key, '{}')
    settings = json.loads(raw) if raw else {'enabled': False}
    return render_template('email_settings.html', settings=settings)

# ============== STRIPE / BILLING ==============

STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '')
STRIPE_PRICE_ID = os.environ.get('STRIPE_PRICE_ID', '')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')
stripe_enabled = bool(STRIPE_SECRET_KEY and STRIPE_SECRET_KEY.startswith('sk_'))

if stripe_enabled:
    import stripe
    stripe.api_key = STRIPE_SECRET_KEY

@app.route('/create-checkout-session', methods=['POST'])
@login_required
def create_checkout_session():
    if not stripe_enabled:
        return jsonify({'error': 'Stripe not configured'}), 400
    user = get_current_user()
    try:
        checkout_session = stripe.checkout.Session.create(
            customer_email=user['email'],
            payment_method_types=['card'],
            line_items=[{'price': STRIPE_PRICE_ID, 'quantity': 1}],
            mode='subscription',
            success_url=request.host_url + 'success?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=request.host_url + 'cancel',
            metadata={'user_id': user['id']}
        )
        return jsonify({'url': checkout_session.url})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/webhook', methods=['POST'])
def stripe_webhook():
    if not stripe_enabled:
        return jsonify({'error': 'Stripe not configured'}), 400
    payload = request.data
    sig_header = request.headers.get('stripe-signature')
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        if event['type'] == 'checkout.session.completed':
            obj = event['data']['object']
            user_id = obj.get('metadata', {}).get('user_id')
            customer_id = obj.get('customer')
            if user_id:
                db = get_db()
                db.execute("UPDATE users SET plan='pro', stripe_customer_id=? WHERE id=?", (customer_id, user_id))
                db.commit()
        elif event['type'] == 'customer.subscription.deleted':
            customer_id = event['data']['object'].get('customer')
            if customer_id:
                db = get_db()
                db.execute("UPDATE users SET plan='free' WHERE stripe_customer_id=?", (customer_id,))
                db.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/success')
def success():
    return render_template('success.html')

@app.route('/cancel')
def cancel():
    return render_template('cancel.html')

# ============== AI CEO ==============

@app.route('/ceo')
@login_required
def ceo_dashboard():
    return render_template('ceo_dashboard.html')

@app.route('/api/ceo/think', methods=['POST'])
@login_required
def ceo_think():
    data = request.json
    prompt = data.get('prompt', '')
    if not prompt:
        return jsonify({'error': 'No prompt provided'}), 400
    ceo = get_ceo_for_user(session.get('user_id'))
    return jsonify({'response': ceo.think(prompt), 'ceo': ceo.name, 'time': datetime.now().isoformat()})

@app.route('/api/ceo/analyze', methods=['GET'])
@login_required
def ceo_analyze():
    user_id = session['user_id']
    db = get_db()
    orders = db.execute('SELECT * FROM orders WHERE user_id=?', (user_id,)).fetchall()
    products = db.execute('SELECT * FROM products WHERE user_id=?', (user_id,)).fetchall()
    ceo = get_ceo_for_user(user_id)
    analysis = ceo.analyze_performance(orders, products)
    return jsonify({
        'analysis': analysis,
        'stats': {
            'total_orders': len(orders),
            'total_products': len(products),
            'revenue': sum(o['total'] for o in orders)
        },
        'ceo': ceo.name
    })

@app.route('/api/ceo/marketing', methods=['GET'])
@login_required
def ceo_marketing():
    ceo = get_ceo_for_user(session.get('user_id'))
    plan = ceo.create_marketing_plan()
    return jsonify({'plan': plan, 'ceo': ceo.name})

@app.route('/api/business/status', methods=['GET'])
@login_required
def business_status():
    user_id = session['user_id']
    db = get_db()
    orders = db.execute('SELECT * FROM orders WHERE user_id=?', (user_id,)).fetchall()
    products = db.execute('SELECT * FROM products WHERE user_id=?', (user_id,)).fetchall()
    suppliers = db.execute('SELECT * FROM suppliers WHERE user_id=?', (user_id,)).fetchall()
    return jsonify({
        'orders': len(orders),
        'products': len(products),
        'suppliers': len(suppliers),
        'revenue': sum(o['total'] for o in orders),
        'pending_shipping': sum(1 for o in orders if o['status'] == 'pending'),
    })

# ============== MISC APIs ==============

@app.route('/shipping')
@login_required
def shipping():
    return render_template('shipping.html')

@app.route('/api/calculate-shipping', methods=['POST'])
def calculate_shipping():
    data = request.json
    weight = float(data.get('weight', 0))
    distance = float(data.get('distance', 0))
    estimated = 5.00 + (weight * 1.50) + (distance * 0.10)
    return jsonify({'estimated_cost': round(estimated, 2), 'currency': 'USD'})

@app.route('/api/calculate-price', methods=['POST'])
@login_required
def api_calculate_price():
    data = request.json
    cost = float(data.get('cost', 0))
    key = f'pricing_{session["user_id"]}'
    raw = get_admin_setting(key, '{}')
    settings = json.loads(raw) if raw else {}
    markup = settings.get('default_markup_percent', 50)
    shipping = settings.get('shipping_handling', 5)
    selling_price = round((cost + shipping) * (1 + markup / 100), 2)
    profit = selling_price - cost
    return jsonify({'cost': cost, 'selling_price': selling_price, 'profit': profit,
                    'profit_percent': round((profit / cost * 100) if cost > 0 else 0, 1)})

@app.route('/profit-calculator')
@login_required
def profit_calculator():
    return render_template('profit_calculator.html')

@app.route('/api/calculate-profit', methods=['POST'])
def calculate_profit_api():
    data = request.json
    product_cost = float(data.get('product_cost', 0))
    shipping_cost = float(data.get('shipping_cost', 0))
    selling_price = float(data.get('selling_price', 0))
    platform_fee_percent = float(data.get('platform_fee', 2.9))
    payment_processing_percent = float(data.get('payment_processing', 2.9))
    total_cost = product_cost + shipping_cost
    platform_fee = selling_price * (platform_fee_percent / 100)
    payment_fee = selling_price * (payment_processing_percent / 100)
    total_fees = platform_fee + payment_fee
    profit = selling_price - total_cost - total_fees
    profit_margin = (profit / selling_price * 100) if selling_price > 0 else 0
    return jsonify({'revenue': selling_price, 'total_cost': total_cost, 'fees': total_fees,
                    'profit': profit, 'profit_margin': round(profit_margin, 1)})

@app.route('/marketing')
@login_required
def marketing():
    return render_template('marketing.html')

@app.route('/research')
@login_required
def product_research():
    return render_template('research.html')

@app.route('/about')
def about():
    return render_template('about.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
