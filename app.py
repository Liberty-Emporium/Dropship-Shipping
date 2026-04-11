"""
Andyping Shipping App
Track orders and manage shipping for dropshipping business
"""

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, g
import os
import json
import sqlite3
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dropship-secret-key-2024')

DATA_DIR = os.environ.get('DATA_DIR', os.path.join('/data'))
os.makedirs(DATA_DIR, exist_ok=True)

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
    db.execute('''CREATE TABLE IF NOT EXISTS user_api_keys (
        user_id TEXT PRIMARY KEY,
        groq_key TEXT DEFAULT '',
        qwen_key TEXT DEFAULT '',
        active_provider TEXT DEFAULT 'qwen',
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS admin_api_keys (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        groq_key TEXT DEFAULT '',
        qwen_key TEXT DEFAULT '',
        active_provider TEXT DEFAULT 'qwen',
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    db.commit()
    db.close()

init_db()

def get_user_api_keys(user_id=None):
    """Get API keys - user key first, fall back to admin keys"""
    db = get_db()
    if user_id:
        row = db.execute('SELECT * FROM user_api_keys WHERE user_id = ?', (user_id,)).fetchone()
        if row and (row['groq_key'] or row['qwen_key']):
            return {'groq_key': row['groq_key'], 'qwen_key': row['qwen_key'], 'active_provider': row['active_provider']}
    row = db.execute('SELECT * FROM admin_api_keys WHERE id = 1').fetchone()
    if row:
        return {'groq_key': row['groq_key'], 'qwen_key': row['qwen_key'], 'active_provider': row['active_provider']}
    return {'groq_key': '', 'qwen_key': '', 'active_provider': 'qwen'}

def get_ceo_for_user(user_id=None):
    """Get an AICEO instance loaded with the right API keys"""
    from ai_ceo import AICEO
    keys = get_user_api_keys(user_id)
    return AICEO(api_keys=keys, active_provider=keys.get('active_provider', 'qwen'))

# ============== DATA FUNCTIONS ==============

def load_orders():
    path = os.path.join(DATA_DIR, 'orders.json')
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []

def save_orders(orders):
    with open(os.path.join(DATA_DIR, 'orders.json'), 'w') as f:
        json.dump(orders, f, indent=2)

def load_suppliers():
    path = os.path.join(DATA_DIR, 'suppliers.json')
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []

def save_suppliers(suppliers):
    with open(os.path.join(DATA_DIR, 'suppliers.json'), 'w') as f:
        json.dump(suppliers, f, indent=2)

def load_products():
    path = os.path.join(DATA_DIR, 'products.json')
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []

def save_products(products):
    with open(os.path.join(DATA_DIR, 'products.json'), 'w') as f:
        json.dump(products, f, indent=2)

# ============== ROUTES ==============

@app.route('/')
def index():
    """Dashboard showing overview"""
    orders = load_orders()
    suppliers = load_suppliers()
    products = load_products()
    
    total_orders = len(orders)
    pending_shipping = len([o for o in orders if o.get('status') == 'pending'])
    delivered = len([o for o in orders if o.get('status') == 'delivered'])
    total_revenue = sum(float(o.get('total', 0)) for o in orders)
    
    return render_template('index.html', 
                          total_orders=total_orders,
                          pending_shipping=pending_shipping,
                          delivered=delivered,
                          total_revenue=total_revenue,
                          suppliers=len(suppliers),
                          products=len(products))

@app.route('/orders')
def list_orders():
    orders = load_orders()
    status_filter = request.args.get('status', '')
    if status_filter:
        orders = [o for o in orders if o.get('status') == status_filter]
    return render_template('orders.html', orders=orders)

@app.route('/order/<order_id>')
def order_detail(order_id):
    orders = load_orders()
    order = next((o for o in orders if o.get('id') == order_id), None)
    if not order:
        flash('Order not found', 'error')
        return redirect(url_for('list_orders'))
    return render_template('order_detail.html', order=order)

@app.route('/order/add', methods=['GET', 'POST'])
def add_order():
    if request.method == 'POST':
        orders = load_orders()
        products = load_products()
        
        new_order = {
            'id': f"ORD-{len(orders) + 1:05d}",
            'customer_name': request.form.get('customer_name'),
            'customer_email': request.form.get('customer_email'),
            'customer_address': request.form.get('customer_address'),
            'customer_city': request.form.get('customer_city'),
            'customer_state': request.form.get('customer_state'),
            'customer_zip': request.form.get('customer_zip'),
            'product_id': request.form.get('product_id'),
            'product_name': request.form.get('product_name'),
            'quantity': int(request.form.get('quantity', 1)),
            'price': float(request.form.get('price', 0)),
            'shipping_cost': float(request.form.get('shipping_cost', 0)),
            'total': float(request.form.get('total', 0)),
            'status': 'pending',
            'tracking_number': '',
            'carrier': '',
            'created_at': datetime.now().isoformat()
        }
        
        new_order['total'] = (new_order['price'] * new_order['quantity']) + new_order['shipping_cost']
        
        orders.append(new_order)
        save_orders(orders)
        
        flash(f'Order {new_order["id"]} created!', 'success')
        return redirect(url_for('order_detail', order_id=new_order['id']))
    
    products = load_products()
    return render_template('add_order.html', products=products)

@app.route('/order/<order_id>/ship', methods=['GET', 'POST'])
def ship_order(order_id):
    orders = load_orders()
    order = next((o for o in orders if o.get('id') == order_id), None)
    
    if not order:
        flash('Order not found', 'error')
        return redirect(url_for('list_orders'))
    
    if request.method == 'POST':
        order['tracking_number'] = request.form.get('tracking_number')
        order['carrier'] = request.form.get('carrier')
        order['status'] = 'shipped'
        order['shipped_at'] = datetime.now().isoformat()
        save_orders(orders)
        flash('Order marked as shipped!', 'success')
        return redirect(url_for('order_detail', order_id=order_id))
    
    return render_template('ship_order.html', order=order)

@app.route('/order/<order_id>/delivered')
def mark_delivered(order_id):
    orders = load_orders()
    order = next((o for o in orders if o.get('id') == order_id), None)
    
    if order:
        order['status'] = 'delivered'
        order['delivered_at'] = datetime.now().isoformat()
        save_orders(orders)
        flash('Order marked as delivered!', 'success')
    
    return redirect(url_for('order_detail', order_id=order_id))

@app.route('/products')
def list_products():
    products = load_products()
    return render_template('products.html', products=products)

@app.route('/product/add', methods=['GET', 'POST'])
def add_product():
    if request.method == 'POST':
        products = load_products()
        suppliers = load_suppliers()
        
        new_product = {
            'id': f"PRD-{len(products) + 1:05d}",
            'name': request.form.get('name'),
            'sku': request.form.get('sku'),
            'supplier': request.form.get('supplier'),
            'cost': float(request.form.get('cost', 0)),
            'price': float(request.form.get('price', 0)),
            'weight': float(request.form.get('weight', 0)),
            'stock': int(request.form.get('stock', 0)),
            'created_at': datetime.now().isoformat()
        }
        
        products.append(new_product)
        save_products(products)
        
        flash(f'Product {new_product["id"]} added!', 'success')
        return redirect(url_for('list_products'))
    
    suppliers = load_suppliers()
    return render_template('add_product.html', suppliers=suppliers)

@app.route('/suppliers')
def list_suppliers():
    suppliers = load_suppliers()
    return render_template('suppliers.html', suppliers=suppliers)

@app.route('/supplier/add', methods=['GET', 'POST'])
def add_supplier():
    if request.method == 'POST':
        suppliers = load_suppliers()
        
        new_supplier = {
            'id': f"SUP-{len(suppliers) + 1:05d}",
            'name': request.form.get('name'),
            'email': request.form.get('email'),
            'phone': request.form.get('phone'),
            'address': request.form.get('address'),
            'website': request.form.get('website'),
            'notes': request.form.get('notes'),
            'created_at': datetime.now().isoformat()
        }
        
        suppliers.append(new_supplier)
        save_suppliers(suppliers)
        
        flash(f'Supplier {new_supplier["id"]} added!', 'success')
        return redirect(url_for('list_suppliers'))
    
    return render_template('add_supplier.html')

@app.route('/shipping')
def shipping():
    """Shipping calculator and label generator placeholder"""
    return render_template('shipping.html')

@app.route('/api/calculate-shipping', methods=['POST'])
def calculate_shipping():
    data = request.json
    weight = float(data.get('weight', 0))
    distance = float(data.get('distance', 0))
    
    # Simple shipping calculation (placeholder)
    base_rate = 5.00
    per_lb = 1.50
    per_mile = 0.10
    
    estimated = base_rate + (weight * per_lb) + (distance * per_mile)
    
    return jsonify({
        'estimated_cost': round(estimated, 2),
        'currency': 'USD'
    })

# ============== AI CEO API ==============

@app.route('/api/ceo/think', methods=['POST'])
def ceo_think():
    """Ask the AI CEO to think about something"""
    data = request.json
    prompt = data.get('prompt', '')
    if not prompt:
        return jsonify({'error': 'No prompt provided'}), 400
    ceo = get_ceo_for_user(session.get('user_id'))
    result = ceo.think(prompt)
    return jsonify({'response': result, 'ceo': ceo.name, 'time': datetime.now().isoformat()})

@app.route('/api/ceo/decide', methods=['POST'])
def ceo_decide():
    """Ask AI CEO to make a decision"""
    data = request.json
    situation = data.get('situation', '')
    if not situation:
        return jsonify({'error': 'No situation provided'}), 400
    ceo = get_ceo_for_user(session.get('user_id'))
    result = ceo.decide(situation)
    build_request = result.split('BUILD:')[1].strip() if 'BUILD:' in result else None
    return jsonify({'decision': result, 'build_request': build_request, 'ceo': ceo.name, 'time': datetime.now().isoformat()})

@app.route('/api/ceo/analyze', methods=['GET'])
def ceo_analyze():
    """Get AI CEO analysis of business"""
    ceo = get_ceo_for_user(session.get('user_id'))
    orders = load_orders()
    products = load_products()
    analysis = ceo.analyze_performance(orders, products)
    return jsonify({
        'analysis': analysis,
        'stats': {
            'total_orders': len(orders),
            'total_products': len(products),
            'pending': len([o for o in orders if o.get('status') == 'pending']),
            'shipped': len([o for o in orders if o.get('status') == 'shipped']),
            'delivered': len([o for o in orders if o.get('status') == 'delivered']),
            'revenue': sum(float(o.get('total', 0)) for o in orders)
        },
        'ceo': ceo.name
    })

@app.route('/api/ceo/marketing', methods=['GET'])
def ceo_marketing():
    """Get marketing plan from AI CEO"""
    ceo = get_ceo_for_user(session.get('user_id'))
    plan = ceo.create_marketing_plan()
    return jsonify({'plan': plan, 'ceo': ceo.name})

@app.route('/api/business/status', methods=['GET'])
def business_status():
    """Get overall business status"""
    orders = load_orders()
    products = load_products()
    suppliers = load_suppliers()
    
    return jsonify({
        'orders': len(orders),
        'products': len(products),
        'suppliers': len(suppliers),
        'revenue': sum(float(o.get('total', 0)) for o in orders),
        'pending_shipping': len([o for o in orders if o.get('status') == 'pending']),
        'recent_orders': orders[-5:] if orders else []
    })

# ============== STATIC PAGES ==============

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/ceo')
def ceo_dashboard():
    """AI CEO Dashboard"""
    return render_template('ceo_dashboard.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

# ============== PRICE AUTOMATION ==============

@app.route('/settings/pricing', methods=['GET', 'POST'])
def pricing_settings():
    """Price automation settings"""
    settings_path = os.path.join(DATA_DIR, 'pricing_settings.json')
    
    if request.method == 'POST':
        settings = {
            'default_markup_percent': float(request.form.get('default_markup', 50)),
            'min_profit_percent': float(request.form.get('min_profit', 20)),
            'shipping_handling': float(request.form.get('shipping_handling', 5)),
            'platform_fee_percent': float(request.form.get('platform_fee', 2.9)),
        }
        with open(settings_path, 'w') as f:
            json.dump(settings, f, indent=2)
        flash('Pricing settings saved!', 'success')
        return redirect(url_for('pricing_settings'))
    
    settings = {}
    if os.path.exists(settings_path):
        with open(settings_path) as f:
            settings = json.load(f)
    
    return render_template('pricing_settings.html', settings=settings)

def calculate_selling_price(cost, settings):
    """Auto-calculate selling price"""
    markup_percent = settings.get('default_markup_percent', 50)
    shipping = settings.get('shipping_handling', 5)
    platform_fee = settings.get('platform_fee_percent', 2.9)
    
    base_price = cost + shipping
    selling_price = base_price * (1 + markup_percent / 100)
    
    # Add platform fee to customer, not to us
    return round(selling_price, 2)

@app.route('/api/calculate-price', methods=['POST'])
def api_calculate_price():
    """Calculate selling price"""
    data = request.json
    cost = float(data.get('cost', 0))
    
    settings_path = os.path.join(DATA_DIR, 'pricing_settings.json')
    settings = {}
    if os.path.exists(settings_path):
        with open(settings_path) as f:
            settings = json.load(f)
    
    selling_price = calculate_selling_price(cost, settings)
    profit = selling_price - cost
    
    return jsonify({
        'cost': cost,
        'selling_price': selling_price,
        'profit': profit,
        'profit_percent': round((profit / cost * 100) if cost > 0 else 0, 1)
    })

# ============== ANALYTICS ==============

@app.route('/analytics')
def analytics():
    """Business analytics dashboard"""
    orders = load_orders()
    products = load_products()
    
    # Calculate stats
    total_revenue = sum(float(o.get('total', 0)) for o in orders)
    total_orders = len(orders)
    avg_order_value = total_revenue / total_orders if total_orders > 0 else 0
    
    # Orders by status
    pending = len([o for o in orders if o.get('status') == 'pending'])
    shipped = len([o for o in orders if o.get('status') == 'shipped'])
    delivered = len([o for o in orders if o.get('status') == 'delivered'])
    
    # Orders by day (last 7 days)
    orders_by_day = {}
    for order in orders:
        day = order.get('created_at', '')[:10]
        if day:
            orders_by_day[day] = orders_by_day.get(day, 0) + 1
    
    # Top products
    product_sales = {}
    for order in orders:
        prod = order.get('product_name', 'Unknown')
        product_sales[prod] = product_sales.get(prod, 0) + 1
    
    top_products = sorted(product_sales.items(), key=lambda x: x[1], reverse=True)[:5]
    
    return render_template('analytics.html',
                          total_revenue=total_revenue,
                          total_orders=total_orders,
                          avg_order_value=avg_order_value,
                          pending=pending,
                          shipped=shipped,
                          delivered=delivered,
                          orders_by_day=orders_by_day,
                          top_products=top_products)

# ============== PRODUCT IMPORT ==============

@app.route('/products/import', methods=['GET', 'POST'])
def import_products():
    """Import products from CSV"""
    if request.method == 'POST':
        file = request.files.get('file')
        if file:
            # Parse CSV
            content = file.read().decode('utf-8')
            lines = content.strip().split('\n')
            
            products = load_products()
            imported = 0
            
            for i, line in enumerate(lines):
                if i == 0:  # Skip header
                    continue
                parts = line.split(',')
                if len(parts) >= 4:
                    product = {
                        'id': f"PRD-{len(products) + imported + 1:05d}",
                        'name': parts[0].strip(),
                        'sku': parts[1].strip(),
                        'cost': float(parts[2].strip()) if parts[2].strip() else 0,
                        'price': float(parts[3].strip()) if len(parts) > 3 and parts[3].strip() else 0,
                        'supplier': parts[4].strip() if len(parts) > 4 else '',
                        'stock': 100,
                        'created_at': datetime.now().isoformat()
                    }
                    products.append(product)
                    imported += 1
            
            save_products(products)
            flash(f'Imported {imported} products!', 'success')
        
        return redirect(url_for('list_products'))
    
    return render_template('import_products.html')

# ============== CUSTOMER MANAGEMENT ==============

def load_customers():
    path = os.path.join(DATA_DIR, 'customers.json')
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []

def save_customers(customers):
    with open(os.path.join(DATA_DIR, 'customers.json'), 'w') as f:
        json.dump(customers, f, indent=2)

@app.route('/customers')
def list_customers():
    customers = load_customers()
    return render_template('customers.html', customers=customers)

@app.route('/customer/add', methods=['GET', 'POST'])
def add_customer():
    if request.method == 'POST':
        customers = load_customers()
        customer = {
            'id': f"CUST-{len(customers) + 1:05d}",
            'name': request.form.get('name'),
            'email': request.form.get('email'),
            'phone': request.form.get('phone'),
            'address': request.form.get('address'),
            'total_orders': 0,
            'total_spent': 0,
            'created_at': datetime.now().isoformat()
        }
        customers.append(customer)
        save_customers(customers)
        flash(f'Customer {customer["id"]} added!', 'success')
        return redirect(url_for('list_customers'))
    
    return render_template('add_customer.html')

# Auto-create customer when order is placed
@app.route('/api/auto-customer', methods=['POST'])
def auto_create_customer():
    """Automatically create or update customer from order"""
    data = request.json
    email = data.get('email', '')
    
    customers = load_customers()
    existing = next((c for c in customers if c.get('email') == email), None)
    
    if existing:
        existing['total_orders'] = existing.get('total_orders', 0) + 1
        existing['total_spent'] = existing.get('total_spent', 0) + data.get('total', 0)
    else:
        customer = {
            'id': f"CUST-{len(customers) + 1:05d}",
            'name': data.get('name', ''),
            'email': email,
            'phone': data.get('phone', ''),
            'address': data.get('address', ''),
            'total_orders': 1,
            'total_spent': data.get('total', 0),
            'created_at': datetime.now().isoformat()
        }
        customers.append(customer)
    
    save_customers(customers)
    return jsonify({'success': True, 'customer_count': len(customers)})

# ============== AUTO FULFILLMENT ==============

# import smtplib  # Disabled temporarily
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Email settings (stored in config)
def load_email_settings():
    path = os.path.join(DATA_DIR, 'email_settings.json')
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {'enabled': False}

def save_email_settings(settings):
    with open(os.path.join(DATA_DIR, 'email_settings.json'), 'w') as f:
        json.dump(settings, f, indent=2)

def send_email(to_email, subject, body, smtp_settings):
    """Send email notification"""
    try:
        msg = MIMEMultipart()
        msg['From'] = smtp_settings.get('from_email')
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html'))
        
        server = smtplib.SMTP(smtp_settings.get('smtp_host'), int(smtp_settings.get('smtp_port', 587)))
        server.starttls()
        server.login(smtp_settings.get('username'), smtp_settings.get('password'))
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False

@app.route('/settings/email', methods=['GET', 'POST'])
def email_settings_page():
    """Email configuration page"""
    settings = load_email_settings()
    
    if request.method == 'POST':
        settings = {
            'enabled': 'enabled' in request.form,
            'smtp_host': request.form.get('smtp_host', ''),
            'smtp_port': request.form.get('smtp_port', '587'),
            'username': request.form.get('username', ''),
            'password': request.form.get('password', ''),
            'from_email': request.form.get('from_email', ''),
        }
        save_email_settings(settings)
        flash('Email settings saved!', 'success')
        return redirect(url_for('email_settings_page'))
    
    return render_template('email_settings.html', settings=settings)

@app.route('/api/fulfill-auto/<order_id>', methods=['POST'])
def auto_fulfill_order(order_id):
    """Automatically fulfill an order"""
    orders = load_orders()
    order = next((o for o in orders if o.get('id') == order_id), None)
    
    if not order:
        return jsonify({'success': False, 'error': 'Order not found'}), 404
    
    suppliers = load_suppliers()
    supplier = next((s for s in suppliers if s.get('name') == order.get('supplier', '')), None)
    
    email_settings = load_email_settings()
    
    # Step 1: Send order to supplier
    if supplier and email_settings.get('enabled'):
        supplier_email_body = f"""
        <h2>New Order to Fulfill</h2>
        <p><strong>Order ID:</strong> {order['id']}</p>
        <p><strong>Product:</strong> {order['product_name']}</p>
        <p><strong>Quantity:</strong> {order['quantity']}</p>
        <p><strong>Shipping Address:</strong><br>
        {order['customer_name']}<br>
        {order['customer_address']}<br>
        {order['customer_city']}, {order['customer_state']} {order['customer_zip']}</p>
        <p><strong>Customer Email:</strong> {order['customer_email']}</p>
        """
        send_email(supplier.get('email', ''), f'New Order {order["id"]} - Please Fulfill', supplier_email_body, email_settings)
    
    # Step 2: Simulate getting tracking number (in real app, this would come from supplier API)
    tracking_carriers = ['USPS', 'UPS', 'FedEx', 'DHL']
    import random
    tracking_number = f"{random.choice(tracking_carriers)}{random.randint(1000000000, 9999999999)}"
    
    order['tracking_number'] = tracking_number
    order['carrier'] = random.choice(tracking_carriers)
    order['status'] = 'shipped'
    order['shipped_at'] = datetime.now().isoformat()
    order['auto_fulfilled'] = True
    
    save_orders(orders)
    
    # Step 3: Send tracking to customer
    if email_settings.get('enabled') and order.get('customer_email'):
        customer_email_body = f"""
        <h2>Your order has been shipped! 📦</h2>
        <p>Hi {order['customer_name']},</p>
        <p>Great news! Your order has been automatically processed and shipped.</p>
        <p><strong>Order ID:</strong> {order['id']}</p>
        <p><strong>Product:</strong> {order['product_name']} x {order['quantity']}</p>
        <p><strong>Tracking Number:</strong> {tracking_number}</p>
        <p><strong>Carrier:</strong> {order['carrier']}</p>
        <p>Track your package: <a href="https://t.track/{tracking_number}">Click here</a></p>
        <p>Thank you for your order!</p>
        """
        send_email(order['customer_email'], f'Order {order["id"]} Shipped!', customer_email_body, email_settings)
    
    return jsonify({
        'success': True,
        'order_id': order_id,
        'tracking_number': tracking_number,
        'carrier': order['carrier'],
        'email_sent': email_settings.get('enabled')
    })

@app.route('/api/fulfill-all-pending', methods=['POST'])
def auto_fulfill_all_pending():
    """Auto-fulfill all pending orders"""
    orders = load_orders()
    pending_orders = [o for o in orders if o.get('status') == 'pending']
    
    results = []
    for order in pending_orders:
        result = auto_fulfill_order(order['id']).get_json()
        results.append(result)
    
    return jsonify({
        'fulfilled': len([r for r in results if r.get('success')]),
        'results': results
    })

# ============== MARKETING TOOLS ==============

@app.route('/marketing')
def marketing():
    """Marketing tools dashboard"""
    return render_template('marketing.html')

@app.route('/api/create-ad', methods=['POST'])
def create_ad():
    """AI creates ad copy"""
    from ai_ceo import ceo
    
    data = request.json
    product = data.get('product', '')
    platform = data.get('platform', 'facebook')
    
    prompt = f"""Create a {platform} ad for this product. 
Product: {product}

Write:
- Attention-grabbing headline
- 2-3 body paragraphs
- Call to action

Keep it concise and high-converting."""
    
    ad = ceo.think(prompt)
    
    return jsonify({'ad': ad, 'platform': platform, 'product': product})

@app.route('/api/create-email', methods=['POST'])
def create_email():
    """AI creates email marketing"""
    from ai_ceo import ceo
    
    data = request.json
    email_type = data.get('type', 'welcome')  # welcome, promotional, follow-up
    
    prompts = {
        'welcome': 'Write a welcome email for a new customer who just made their first purchase',
        'promotional': 'Write a promotional email offering 20% off their next order',
        'follow-up': 'Write a follow-up email for customers who abandoned their cart'
    }
    
    email = ceo.think(prompts.get(email_type, prompts['welcome']))
    
    return jsonify({'email': email, 'type': email_type})

# ============== PRODUCT RESEARCH ==============

@app.route('/research')
def product_research():
    """Product research and trending products"""
    return render_template('research.html')

@app.route('/api/research-product', methods=['POST'])
def research_product():
    """AI researches a product niche"""
    from ai_ceo import ceo
    
    data = request.json
    niche = data.get('niche', '')
    
    prompt = f"""Analyze this product niche for dropshipping: {niche}

Tell me:
1. Is it a good product to sell? (pros/cons)
2. What's the ideal selling price?
3. Who is the target audience?
4. What marketing angles work best?
5. Any red flags to avoid?

Be specific and helpful."""
    
    analysis = ceo.think(prompt)
    
    return jsonify({'niche': niche, 'analysis': analysis})

@app.route('/api/trending-niches', methods=['GET'])
def trending_niches():
    """Get trending niches suggestions"""
    from ai_ceo import ceo
    
    prompt = """List 10 trending product niches for dropshipping in 2024/2025. 
For each, give a brief one-sentence on why it's popular.
Keep it simple - just the niche name and one sentence."""
    
    niches = ceo.think(prompt)
    
    return jsonify({'niches': niches})

# ============== INVENTORY ALERTS ==============

@app.route('/alerts')
def inventory_alerts():
    """Low stock and other alerts"""
    products = load_products()
    orders = load_orders()
    
    low_stock = [p for p in products if p.get('stock', 100) < 10]
    out_of_stock = [p for p in products if p.get('stock', 100) <= 0]
    
    # Orders needing attention
    pending_orders = [o for o in orders if o.get('status') == 'pending']
    
    return render_template('alerts.html', 
                           low_stock=low_stock,
                           out_of_stock=out_of_stock,
                           pending_orders=pending_orders)

# ============== PROFIT CALCULATOR ==============

@app.route('/profit-calculator')
def profit_calculator():
    """Standalone profit calculator"""
    return render_template('profit_calculator.html')

@app.route('/api/calculate-profit', methods=['POST'])
def calculate_profit_api():
    """Detailed profit calculation"""
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
    
    return jsonify({
        'revenue': selling_price,
        'total_cost': total_cost,
        'fees': total_fees,
        'profit': profit,
        'profit_margin': round(profit_margin, 1)
    })


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        # For now, just redirect to dashboard
        # Later: Stripe integration for 9/month
        return redirect(url_for('dashboard'))
    return render_template('signup.html')


# ============== STRIPE PAYMENTS ($59/month) ==============

# import stripe  # Temporarily disabled - need keys first

# Get from environment or set test key (for now)
STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', 'sk_test_placeholder')
# stripe.api_key = STRIPE_SECRET_KEY

PRICE_ID = os.environ.get('STRIPE_PRICE_ID', 'price_placeholder')  # $59/month price ID

@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    """Create Stripe checkout for $59/month subscription"""
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price': PRICE_ID,
                'quantity': 1,
            }],
            mode='subscription',
            success_url=request.host_url + 'success?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=request.host_url + 'cancel',
        )
        return jsonify({'url': checkout_session.url})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/success')
def success():
    return render_template('success.html')

@app.route('/cancel')
def cancel():
    return render_template('cancel.html')

@app.route('/webhook', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhooks for subscription events"""
    payload = request.data
    sig_header = request.headers.get('stripe-signature')
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, os.environ.get('STRIPE_WEBHOOK_SECRET')
        )
        
        if event['type'] == 'checkout.session.completed':
            # Handle successful subscription
            session = event['data']['object']
            # Save customer info, activate subscription, etc.
            pass
        elif event['type'] == 'customer.subscription.deleted':
            # Handle cancelled subscription
            pass
            
        return jsonify({'success': True})
    except stripe.error.SignatureVerificationError:
        return jsonify({'error': 'Invalid signature'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 400


# ============== AI KEY SETTINGS ==============

@app.route('/admin/ai-settings', methods=['GET', 'POST'])
def admin_ai_settings():
    """Admin: set system-wide default AI keys"""
    db = get_db()
    if request.method == 'POST':
        groq_key = request.form.get('groq_key', '').strip()
        qwen_key = request.form.get('qwen_key', '').strip()
        active_provider = request.form.get('active_provider', 'qwen')
        db.execute('''INSERT OR REPLACE INTO admin_api_keys (id, groq_key, qwen_key, active_provider, updated_at)
            VALUES (1, ?, ?, ?, CURRENT_TIMESTAMP)''', (groq_key, qwen_key, active_provider))
        db.commit()
        flash('Admin AI settings saved!', 'success')
        return redirect(url_for('admin_ai_settings'))
    row = db.execute('SELECT * FROM admin_api_keys WHERE id = 1').fetchone()
    keys = dict(row) if row else {'groq_key': '', 'qwen_key': '', 'active_provider': 'qwen'}
    for k in ['groq_key', 'qwen_key']:
        if keys.get(k):
            keys[k] = keys[k][:8] + '...'
    return render_template('admin_ai_settings.html', keys=keys)

@app.route('/my-settings', methods=['GET', 'POST'])
def my_settings():
    """Per-user AI API key settings"""
    user_id = session.get('user_id', 'guest')
    db = get_db()
    if request.method == 'POST':
        groq_key = request.form.get('groq_key', '').strip()
        qwen_key = request.form.get('qwen_key', '').strip()
        active_provider = request.form.get('active_provider', 'qwen')
        db.execute('''INSERT OR REPLACE INTO user_api_keys
            (user_id, groq_key, qwen_key, active_provider, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)''', (user_id, groq_key, qwen_key, active_provider))
        db.commit()
        flash('Your AI settings saved!', 'success')
        return redirect(url_for('my_settings'))
    row = db.execute('SELECT * FROM user_api_keys WHERE user_id = ?', (user_id,)).fetchone()
    keys = dict(row) if row else {'groq_key': '', 'qwen_key': '', 'active_provider': 'qwen'}
    for k in ['groq_key', 'qwen_key']:
        if keys.get(k):
            keys[k] = keys[k][:8] + '...'
    return render_template('my_settings.html', keys=keys)
