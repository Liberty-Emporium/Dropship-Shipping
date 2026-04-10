"""
Dropshipping Shipping App
Track orders and manage shipping for dropshipping business
"""

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import os
import json
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.urandom(24)

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
os.makedirs(DATA_DIR, exist_ok=True)

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
    from ai_ceo import ceo
    
    data = request.json
    prompt = data.get('prompt', '')
    
    if not prompt:
        return jsonify({'error': 'No prompt provided'}), 400
    
    result = ceo.think(prompt)
    
    return jsonify({
        'response': result,
        'ceo': ceo.name,
        'time': datetime.now().isoformat()
    })

@app.route('/api/ceo/decide', methods=['POST'])
def ceo_decide():
    """Ask AI CEO to make a decision"""
    from ai_ceo import ceo
    
    data = request.json
    situation = data.get('situation', '')
    
    if not situation:
        return jsonify({'error': 'No situation provided'}), 400
    
    result = ceo.decide(situation)
    
    # Check if it wants to BUILD something
    build_request = None
    if 'BUILD:' in result:
        build_request = result.split('BUILD:')[1].strip()
    
    return jsonify({
        'decision': result,
        'build_request': build_request,
        'ceo': ceo.name,
        'time': datetime.now().isoformat()
    })

@app.route('/api/ceo/analyze', methods=['GET'])
def ceo_analyze():
    """Get AI CEO analysis of business"""
    from ai_ceo import ceo
    
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
    from ai_ceo import ceo
    
    plan = ceo.create_marketing_plan()
    
    return jsonify({
        'plan': plan,
        'ceo': ceo.name
    })

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
