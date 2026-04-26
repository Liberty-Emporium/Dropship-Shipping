"""
AI Auto Dropshipping - Multi-Tenant SaaS Platform
Full multi-tenant dropshipping platform with AI CEO powered by OpenRouter
"""

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, g

import time as _rl_time
from collections import defaultdict as _defaultdict
_rate_store = _defaultdict(list)
_RATE_WINDOW = 60
_RATE_MAX = 10

def _check_login_rate(ip):
    now = _rl_time.time()
    _rate_store[ip] = [t for t in _rate_store[ip] if now - t < _RATE_WINDOW]
    if len(_rate_store[ip]) >= _RATE_MAX:
        return False
    _rate_store[ip].append(now)
    return True

import os, json, sqlite3, hashlib, uuid, datetime, functools

# ============================================================
# RATE LIMITER — No external dependencies required
# ============================================================
import time as _rl_time

def _is_rate_limited(db, key, max_calls=5, window_seconds=60):
    """Returns True if this key has exceeded the rate limit."""
    try:
        db.execute("""CREATE TABLE IF NOT EXISTS rate_limits (
            key TEXT NOT NULL, window_start INTEGER NOT NULL,
            count INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (key, window_start))""")
        db.execute("DELETE FROM rate_limits WHERE window_start < ?",
                   (int(_rl_time.time()) - window_seconds * 2,))
        now = int(_rl_time.time())
        ws = now - (now % window_seconds)
        row = db.execute(
            "SELECT count FROM rate_limits WHERE key=? AND window_start=?",
            (key, ws)).fetchone()
        if row is None:
            db.execute("INSERT OR IGNORE INTO rate_limits VALUES (?,?,1)", (key, ws))
            db.commit()
            return False
        if row[0] >= max_calls:
            return True
        db.execute("UPDATE rate_limits SET count=count+1 WHERE key=? AND window_start=?",
                   (key, ws))
        db.commit()
        return False
    except Exception:
        return False


app = Flask(__name__)

def _get_secret_key():
    env_key = os.environ.get('SECRET_KEY')
    if env_key:
        return env_key
    data_dir = os.environ.get('RAILWAY_DATA_DIR') or os.environ.get('DATA_DIR') or '/data'
    key_file = os.path.join(data_dir, 'secret_key')
    try:
        os.makedirs(data_dir, exist_ok=True)
        if os.path.exists(key_file):
            with open(key_file) as f:
                key = f.read().strip()
            if key:
                return key
        import secrets as _sec
        key = _sec.token_hex(32)
        with open(key_file, 'w') as f:
            f.write(key)
        return key
    except Exception:
        import secrets as _sec
        return _sec.token_hex(32)


# ══════════════════════════════════════════════════════════════════════════════
# MULTI-TENANT INFRASTRUCTURE — Applied 2026-04-16
# ══════════════════════════════════════════════════════════════════════════════
import re as _re, threading as _threading, queue as _queue, zipfile as _zipfile
import io as _io
from functools import wraps as _wraps

# ── 1. Slug Validation ────────────────────────────────────────────────────────
def _validate_slug(slug):
    """Sanitize and validate a tenant slug. Raises ValueError if invalid."""
    if not slug:
        raise ValueError("Empty slug")
    clean = _re.sub(r"[^a-z0-9\-]", "", str(slug).lower().strip())
    clean = _re.sub(r"-+", "-", clean).strip("-")[:60]
    reserved = {"admin","api","static","health","login","logout","overseer","guest","demo"}
    if not clean or clean in reserved:
        raise ValueError(f"Invalid or reserved slug: {slug}")
    return clean

# ── 2. Audit Log ──────────────────────────────────────────────────────────────
_AUDIT_FILE = os.path.join(
    os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "/data"), "audit.log"
)

def _audit(action, slug=None, user=None, details=None):
    """Fire-and-forget audit entry. Never raises."""
    try:
        from datetime import datetime as _dt
        import json as _j
        slug  = slug  or (session.get("impersonating_slug") or session.get("store_slug") or "system")
        user  = user  or session.get("username", "unknown")
        ip    = request.environ.get("HTTP_X_FORWARDED_FOR", request.remote_addr) if request else ""
        line  = _j.dumps({
            "ts": _dt.utcnow().isoformat(),
            "slug": slug, "user": user,
            "action": action, "ip": ip,
            "details": details or {}
        })
        os.makedirs(os.path.dirname(_AUDIT_FILE), exist_ok=True)
        with open(_AUDIT_FILE, "a") as _f:
            _f.write(line + "\n")
    except Exception:
        pass

# ── 3. Background Job Queue ───────────────────────────────────────────────────
class _JobQueue:
    def __init__(self):
        self._q = _queue.Queue()
        t = _threading.Thread(target=self._worker, daemon=True)
        t.start()
    def enqueue(self, fn, *args, **kwargs):
        self._q.put((fn, args, kwargs))
    def _worker(self):
        while True:
            try:
                fn, args, kwargs = self._q.get(timeout=1)
                try:
                    fn(*args, **kwargs)
                except Exception as e:
                    try:
                        app.logger.error(f"[JobQueue] {e}")
                    except Exception:
                        pass
                self._q.task_done()
            except _queue.Empty:
                pass

_job_queue = _JobQueue()

# ── 4. Per-Tenant Rate Limiter ────────────────────────────────────────────────
import time as _time
from collections import defaultdict as _defaultdict
_tenant_calls = _defaultdict(list)

def _tenant_rate_ok(slug, max_calls=120, window=60):
    now = _time.time()
    _tenant_calls[slug] = [t for t in _tenant_calls[slug] if now - t < window]
    if len(_tenant_calls[slug]) >= max_calls:
        return False
    _tenant_calls[slug].append(now)
    return True

def _tenant_rate_limit(max_calls=120):
    def decorator(f):
        @_wraps(f)
        def decorated(*args, **kwargs):
            slug = session.get("impersonating_slug") or session.get("store_slug")
            if slug and not _tenant_rate_ok(slug, max_calls):
                return jsonify({"error": "Too many requests. Please slow down."}), 429
            return f(*args, **kwargs)
        return decorated
    return decorator

# ── 5. Trial Status ───────────────────────────────────────────────────────────
def _get_trial_status(slug):
    """Returns 'paid', 'active', or 'expired'."""
    try:
        from datetime import datetime as _dt
        cfg_path = os.path.join(
            os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "/data"),
            "customers", slug, "config.json"
        )
        if not os.path.exists(cfg_path):
            return "active"
        with open(cfg_path) as f:
            import json as _j; cfg = _j.load(f)
        if cfg.get("plan") == "paid":
            return "paid"
        trial_end = cfg.get("trial_ends")
        if not trial_end:
            return "active"
        return "active" if _dt.utcnow() < _dt.fromisoformat(trial_end) else "expired"
    except Exception:
        return "active"

def _trial_gate(f):
    """Redirect expired trials to upgrade page."""
    @_wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_guest"):
            slug = session.get("impersonating_slug") or session.get("store_slug")
            if slug and _get_trial_status(slug) == "expired":
                if not session.get("role") == "overseer":
                    flash("Your trial has expired. Upgrade to continue.", "warning")
                    return redirect("/upgrade")
        return f(*args, **kwargs)
    return decorated

# ── 6. Tenant Health Summary (for Overseer) ────────────────────────────────────
def _get_tenant_health():
    from datetime import datetime as _dt
    import json as _j, csv as _csv
    customers_dir = os.path.join(
        os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "/data"), "customers"
    )
    stores = []
    if not os.path.exists(customers_dir):
        return stores
    for slug in os.listdir(customers_dir):
        cfg_path = os.path.join(customers_dir, slug, "config.json")
        if not os.path.isdir(os.path.join(customers_dir, slug)):
            continue
        if not os.path.exists(cfg_path):
            continue
        try:
            with open(cfg_path) as f:
                cfg = _j.load(f)
            status = _get_trial_status(slug)
            trial_end = cfg.get("trial_ends","")
            days_left = 0
            if status == "active" and trial_end:
                days_left = max(0, (_dt.fromisoformat(trial_end) - _dt.utcnow()).days)

            # Count items
            items = 0
            inv = os.path.join(customers_dir, slug, "inventory.csv")
            if os.path.exists(inv):
                with open(inv) as f:
                    items = max(0, sum(1 for _ in f) - 1)

            # Last active
            tdir = os.path.join(customers_dir, slug)
            mtimes = [os.path.getmtime(os.path.join(tdir, fn))
                      for fn in os.listdir(tdir)
                      if os.path.isfile(os.path.join(tdir, fn))]
            last_active = _dt.fromtimestamp(max(mtimes)).strftime("%Y-%m-%d %H:%M") if mtimes else ""

            stores.append({
                "slug":        slug,
                "store_name":  cfg.get("store_name", slug),
                "email":       cfg.get("contact_email",""),
                "plan":        cfg.get("plan","trial"),
                "status":      status,
                "days_left":   days_left,
                "items":       items,
                "created":     cfg.get("created_at","")[:10],
                "last_active": last_active,
                "mrr":         20.0 if cfg.get("plan") == "paid" else 0,
            })
        except Exception:
            continue
    return sorted(stores, key=lambda x: (x["plan"] != "paid", x["last_active"]), reverse=True)

# ── 7. Data Export ─────────────────────────────────────────────────────────────
@app.route("/settings/export-data")
def _export_tenant_data():
    if not session.get("logged_in"):
        return redirect("/login")
    if session.get("is_guest"):
        flash("Sign up to export your data.", "error")
        return redirect("/")
    slug = session.get("impersonating_slug") or session.get("store_slug")
    if not slug:
        abort(403)
    _audit("data_export", slug)
    customers_dir = os.path.join(
        os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "/data"), "customers"
    )
    tenant_dir = os.path.join(customers_dir, slug)
    buf = _io.BytesIO()
    with _zipfile.ZipFile(buf, "w", _zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(tenant_dir):
            for fname in files:
                full = os.path.join(root, fname)
                arcname = os.path.relpath(full, tenant_dir)
                zf.write(full, arcname)
    buf.seek(0)
    safe_slug = _re.sub(r"[^a-z0-9\-]", "", slug)
    from flask import send_file as _sf
    return _sf(buf, mimetype="application/zip", as_attachment=True,
               download_name=f"{safe_slug}-data-export.zip")

# ── 8. Overseer Tenant Health API ────────────────────────────────────────────
@app.route("/overseer/tenant-health")
def _overseer_tenant_health():
    if session.get("role") != "overseer" and session.get("username") != "admin":
        abort(403)
    return jsonify(_get_tenant_health())

# ══════════════════════════════════════════════════════════════════════════════
# END MULTI-TENANT INFRASTRUCTURE
# ══════════════════════════════════════════════════════════════════════════════

# Session security hardening
app.config['SESSION_COOKIE_SECURE'] = False  # Set True when HTTPS confirmed
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = 3600  # 1 hour
app.secret_key = _get_secret_key()

import secrets as _secrets_module

def _get_csrf_token():
    """Generate or retrieve CSRF token from session."""
    if 'csrf_token' not in session:
        session['csrf_token'] = _secrets_module.token_hex(32)
    return session['csrf_token']

def _validate_csrf():
    """Validate CSRF token on POST requests. Returns True if valid."""
    if request.method != 'POST':
        return True
    # Skip API routes
    if request.path.startswith('/api/'):
        return True
    token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
    return token and token == session.get('csrf_token')

app.jinja_env.globals['csrf_token'] = _get_csrf_token


# Use /data if available and writable, fallback to local ./data directory
_data_pref = os.environ.get('DATA_DIR', '/data')
try:
    os.makedirs(_data_pref, exist_ok=True)
    # Test write
    _test = os.path.join(_data_pref, '.write_test')
    open(_test, 'w').close(); os.remove(_test)
    DATA_DIR = _data_pref
except Exception:
    DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
    os.makedirs(DATA_DIR, exist_ok=True)

CUSTOMERS_DIR = os.path.join(DATA_DIR, 'customers')
os.makedirs(CUSTOMERS_DIR, exist_ok=True)

ADMIN_USER  = os.environ.get('ADMIN_USER', 'admin')
ADMIN_PASS  = os.environ.get('ADMIN_PASSWORD', 'admin1')
ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', 'jay@libertyemporium.com')
APP_NAME    = os.environ.get('APP_NAME', 'AI Auto Dropshipping')

# ── DB ────────────────────────────────────────────────────────────────────────
DB_FILE = os.path.join(DATA_DIR, 'dropship.db')

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_FILE)
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA synchronous=NORMAL")
        g.db.execute("PRAGMA foreign_keys=ON")
        g.db.execute("PRAGMA busy_timeout=5000")
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db is not None: db.close()

def init_db():
    db = sqlite3.connect(DB_FILE)
    db.execute('''CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        password TEXT NOT NULL,
        role TEXT DEFAULT 'user',
        email TEXT,
        store_slug TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS app_config (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')
    # Create default admin
    pw = hashlib.sha256(ADMIN_PASS.encode()).hexdigest()
    db.execute('INSERT OR IGNORE INTO users (username,password,role,email) VALUES (?,?,?,?)',
               (ADMIN_USER, pw, 'admin', ADMIN_EMAIL))
    db.commit()
    db.close()

init_db()

# ── Helpers ───────────────────────────────────────────────────────────────────
def hash_pw(pw): return _bcrypt_hash(pw)

def slugify(name):
    import re
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:40]

def get_config(key, default=''):
    db = get_db()
    row = db.execute('SELECT value FROM app_config WHERE key=?', (key,)).fetchone()
    return row['value'] if row else default

def set_config(key, value):
    get_db().execute('INSERT OR REPLACE INTO app_config (key,value) VALUES (?,?)', (key, str(value)))
    get_db().commit()

# ── Tenant data paths ──────────────────────────────────────────────────────────
def tenant_dir(slug=None):
    if slug:
        d = os.path.join(CUSTOMERS_DIR, slug)
        os.makedirs(d, exist_ok=True)
        return d
    return DATA_DIR

def active_slug():
    return session.get('impersonating_slug') or session.get('store_slug') or None

def load_json(path, default=None):
    if default is None: default = []
    if os.path.exists(path):
        try:
            with open(path) as f: return json.load(f)
        except: pass
    return default

def save_json(path, data):
    with open(path, 'w') as f: json.dump(data, f, indent=2)

def data_path(filename, slug=None):
    return os.path.join(tenant_dir(slug), filename)

def load_orders(slug=None):    return load_json(data_path('orders.json', slug))
def save_orders(d, slug=None): save_json(data_path('orders.json', slug), d)
def load_products(slug=None):    return load_json(data_path('products.json', slug))
def save_products(d, slug=None): save_json(data_path('products.json', slug), d)
def load_suppliers(slug=None):    return load_json(data_path('suppliers.json', slug))
def save_suppliers(d, slug=None): save_json(data_path('suppliers.json', slug), d)
def load_customers_data(slug=None):    return load_json(data_path('customers.json', slug))
def save_customers_data(d, slug=None): save_json(data_path('customers.json', slug), d)

def load_client_config(slug):
    p = os.path.join(CUSTOMERS_DIR, slug, 'config.json')
    return load_json(p, {})

def save_client_config(slug, cfg):
    os.makedirs(os.path.join(CUSTOMERS_DIR, slug), exist_ok=True)
    save_json(os.path.join(CUSTOMERS_DIR, slug, 'config.json'), cfg)

def list_client_stores():
    stores = []
    if not os.path.exists(CUSTOMERS_DIR): return stores
    for slug in os.listdir(CUSTOMERS_DIR):
        cfg_path = os.path.join(CUSTOMERS_DIR, slug, 'config.json')
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path) as f: cfg = json.load(f)
                stores.append(cfg)
            except: pass
    return sorted(stores, key=lambda s: s.get('created_at',''), reverse=True)

def load_leads():  return load_json(os.path.join(DATA_DIR, 'leads.json'))
def save_leads(d): save_json(os.path.join(DATA_DIR, 'leads.json'), d)

# ── Auth decorators ────────────────────────────────────────────────────────────
def login_required(f):
    @functools.wraps(f)
    def decorated(*a, **kw):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*a, **kw)
    return decorated

def admin_required(f):
    @functools.wraps(f)
    def decorated(*a, **kw):
        if not session.get('logged_in') or session.get('role') != 'admin':
            flash('Admin access required.', 'error')
            return redirect(url_for('login'))
        return f(*a, **kw)
    return decorated

def client_required(f):
    @functools.wraps(f)
    def decorated(*a, **kw):
        if not session.get('logged_in'):
            slug = request.view_args.get('slug', '')
            return redirect(url_for('store_login', slug=slug) if slug else url_for('login'))
        return f(*a, **kw)
    return decorated

# ── OpenRouter / AI ────────────────────────────────────────────────────────────
def get_openrouter_key(slug=None):
    if slug:
        cfg = load_client_config(slug)
        if cfg.get('openrouter_key'): return cfg['openrouter_key']
    return get_config('openrouter_key', os.environ.get('OPENROUTER_API_KEY',''))

def get_openrouter_model(slug=None):
    if slug:
        cfg = load_client_config(slug)
        if cfg.get('openrouter_model'): return cfg['openrouter_model']
    return get_config('openrouter_model', 'google/gemini-flash-1.5')

def get_groq_key(slug=None):
    if slug:
        cfg = load_client_config(slug)
        if cfg.get('groq_key'): return cfg['groq_key']
    return get_config('groq_key', os.environ.get('GROQ_API_KEY',''))

def ai_chat(messages, slug=None):
    """Call AI with messages. Tries OpenRouter, falls back to Groq."""
    import urllib.request as ur, urllib.error as ue
    # Try OpenRouter
    key = get_openrouter_key(slug)
    if key:
        try:
            payload = json.dumps({'model': get_openrouter_model(slug), 'messages': messages, 'max_tokens': 800}).encode()
            req = ur.Request('https://openrouter.ai/api/v1/chat/completions', data=payload, headers={
                'Authorization': f'Bearer {key}', 'Content-Type': 'application/json',
                'HTTP-Referer': 'https://libertyemporium.com', 'X-Title': APP_NAME
            })
            with ur.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())['choices'][0]['message']['content']
        except Exception as e:
            print(f'OpenRouter error: {e}')
    # Try Groq
    key = get_groq_key(slug)
    if key:
        try:
            payload = json.dumps({'model': 'llama-3.3-70b-versatile', 'messages': messages, 'max_tokens': 800}).encode()
            req = ur.Request('https://api.groq.com/openai/v1/chat/completions', data=payload, headers={
                'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'
            })
            with ur.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())['choices'][0]['message']['content']
        except Exception as e:
            print(f'Groq error: {e}')
    return "AI unavailable — configure your API keys in Settings ⚙️"

def ceo_think(prompt, slug=None, context=None):
    system = """You are the AI CEO of a dropshipping business. You make smart decisions, create marketing strategies, analyze data, and give actionable advice. Be concise, specific, and decisive."""
    if context:
        system += f"\n\nBusiness context: {context}"
    return ai_chat([{'role':'system','content':system},{'role':'user','content':prompt}], slug)

# ── AI Assistant (OpenRouter) ────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _get_ai_key(slug=None):
    """Get OpenRouter key — checks both old and new config key names."""
    if slug:
        cfg = load_client_config(slug)
        key = cfg.get('openrouter_key','') or cfg.get('openrouter_api_key','')
        if key: return key
    return get_config('openrouter_key','') or get_config('openrouter_api_key','') or os.environ.get('OPENROUTER_API_KEY','')

def _get_ai_model(slug=None):
    """Get AI model — checks both old and new config key names."""
    if slug:
        cfg = load_client_config(slug)
        m = cfg.get('openrouter_model','') or cfg.get('ai_chat_model','')
        if m: return m
    return get_config('openrouter_model','') or get_config('ai_chat_model','') or 'google/gemini-flash-1.5'

# ── Context for templates ──────────────────────────────────────────────────────
def ctx():
    slug = active_slug()
    store_name = APP_NAME
    if slug:
        cfg = load_client_config(slug) or {}
        store_name = cfg.get('store_name', APP_NAME)
    return {
        'app_name': APP_NAME,
        'store_name': store_name,
        'current_user': session.get('username'),
        'current_role': session.get('role'),
        'store_slug': slug,
        'impersonating': bool(session.get('impersonating_slug')),
        'oc_configured': False,
    }

# ── Landing / Public ───────────────────────────────────────────────────────────
@app.route('/')
def index():
    if session.get('logged_in'):
        return redirect(url_for('dashboard'))
    return render_template('landing.html', **ctx())

@app.route('/landing')
def landing(): return render_template('landing.html', **ctx())

@app.route('/healthz')
def healthz(): return 'ok'

@app.route('/health')
def health_check():
    """Health check endpoint."""
    try:
        db = get_db()
        db.execute("SELECT 1").fetchone()
        db_status = "ok"
    except Exception as e:
        db_status = f"error"
    import json
    status = "ok" if db_status == "ok" else "degraded"
    return json.dumps({"status": status, "db": db_status}),            200 if status == "ok" else 503,            {"Content-Type": "application/json"}



# ── Auth ───────────────────────────────────────────────────────────────────────
@app.route('/login', methods=['GET','POST'])
def login():
    # Rate limiting — 10 login attempts per minute per IP
    _ip = request.remote_addr or 'unknown'
    if _is_rate_limited(get_db(), f'login:{_ip}', max_calls=10, window_seconds=60):
        return jsonify({'error': 'Too many login attempts. Please wait 1 minute.'}), 429

    if request.method == 'POST':
        username = request.form.get('username','').strip()
        password = request.form.get('password','').strip()
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        if user and user['password'] == hash_pw(password):
            session.clear()
            session['logged_in'] = True
            session['username'] = username
            session['role'] = user['role']
            if user['store_slug']:
                session['store_slug'] = user['store_slug']
            return redirect(url_for('dashboard'))
        flash('Invalid credentials.', 'error')
    return render_template('login.html', **ctx())

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('landing'))

# Per-tenant login
@app.route('/store/<slug>/login', methods=['GET','POST'])
def store_login(slug):
    cfg = load_client_config(slug)
    if not cfg:
        flash('Store not found.', 'error')
        return redirect(url_for('landing'))
    if request.method == 'POST':
        email    = request.form.get('email','').strip()
        password = request.form.get('password','').strip()
        users_path = os.path.join(CUSTOMERS_DIR, slug, 'users.json')
        users = load_json(users_path, {})
        user = users.get(email)
        if user and user.get('password') == hash_pw(password):
            session.clear()
            session['logged_in'] = True
            session['username']   = email
            session['role']       = user.get('role','client')
            session['store_slug'] = slug
            return redirect(url_for('dashboard'))
        flash('Invalid credentials.', 'error')
    return render_template('store_login.html', cfg=cfg, slug=slug, **ctx())

# ── Trial signup ───────────────────────────────────────────────────────────────
@app.route('/start-trial', methods=['GET','POST'])
def start_trial():
    if request.method == 'POST':
        store_name    = request.form.get('store_name','').strip()
        contact_email = request.form.get('contact_email','').strip()
        contact_name  = request.form.get('contact_name','').strip()
        niche         = request.form.get('niche','general').strip()
        if not store_name or not contact_email:
            flash('Store name and email are required.', 'error')
            return redirect(url_for('wizard'))

        # Check for duplicate email across all stores
        for existing in list_client_stores():
            existing_slug = existing.get('slug','')
            users_check = os.path.join(CUSTOMERS_DIR, existing_slug, 'users.json')
            if os.path.exists(users_check):
                existing_users = load_json(users_check, {})
                if contact_email in existing_users:
                    flash(f'An account with {contact_email} already exists. Please sign in instead.', 'error')
                    return redirect(url_for('login'))

        slug = slugify(store_name)
        base_slug = slug; counter = 1
        while os.path.exists(os.path.join(CUSTOMERS_DIR, slug)):
            slug = f'{base_slug}-{counter}'; counter += 1
        now = datetime.datetime.now().isoformat()
        trial_end = (datetime.datetime.now() + datetime.timedelta(days=14)).isoformat()
        cfg = {
            'store_name': store_name, 'slug': slug,
            'contact_name': contact_name, 'contact_email': contact_email,
            'niche': niche, 'plan': 'trial', 'status': 'active',
            'trial_start': now, 'trial_end': trial_end, 'created_at': now,
        }
        save_client_config(slug, cfg)
        # Create login
        import secrets as _sec
        temp_pw = _sec.token_urlsafe(8)
        users_path = os.path.join(CUSTOMERS_DIR, slug, 'users.json')
        save_json(users_path, {contact_email: {'password': hash_pw(temp_pw), 'role': 'client', 'store_slug': slug, 'created_at': now}})
        # Save lead
        leads = load_leads()
        leads.append({'store_name':store_name,'contact_email':contact_email,'contact_name':contact_name,'slug':slug,'created_at':now,'type':'trial'})
        save_leads(leads)
        # Auto login
        session.clear()
        session['logged_in'] = True
        session['username']   = contact_email
        session['role']       = 'client'
        session['store_slug'] = slug
        flash(f'Welcome! Your login: {contact_email} / {temp_pw} — save this!', 'success')
        return redirect(url_for('dashboard'))
    return redirect(url_for('wizard'))

@app.route('/wizard')
def wizard():
    return render_template('wizard.html', **ctx())

# ── Dashboard ──────────────────────────────────────────────────────────────────
@app.route('/dashboard')
@login_required
def dashboard():
    slug = active_slug()
    orders   = load_orders(slug)
    products = load_products(slug)
    suppliers = load_suppliers(slug)
    total_revenue = sum(float(o.get('total',0)) for o in orders)
    pending  = len([o for o in orders if o.get('status')=='pending'])
    shipped  = len([o for o in orders if o.get('status')=='shipped'])
    delivered = len([o for o in orders if o.get('status')=='delivered'])
    low_stock = [p for p in products if p.get('stock',100) < 10]
    return render_template('index.html',
        total_orders=len(orders), pending_shipping=pending,
        delivered=delivered, shipped=shipped,
        total_revenue=total_revenue,
        suppliers=len(suppliers), products=len(products),
        low_stock=low_stock, **ctx())

# ── Orders ─────────────────────────────────────────────────────────────────────
@app.route('/orders')
@login_required
def list_orders():
    slug = active_slug()
    orders = load_orders(slug)
    status_filter = request.args.get('status','')
    if status_filter: orders = [o for o in orders if o.get('status')==status_filter]
    return render_template('orders.html', orders=orders, **ctx())

@app.route('/order/<order_id>')
@login_required
def order_detail(order_id):
    slug = active_slug()
    orders = load_orders(slug)
    order = next((o for o in orders if o.get('id')==order_id), None)
    if not order: flash('Order not found','error'); return redirect(url_for('list_orders'))
    return render_template('order_detail.html', order=order, **ctx())

@app.route('/order/add', methods=['GET','POST'])
@login_required
def add_order():
    slug = active_slug()
    if request.method == 'POST':
        orders = load_orders(slug)
        new_order = {
            'id': f"ORD-{len(orders)+1:05d}",
            'customer_name':    request.form.get('customer_name'),
            'customer_email':   request.form.get('customer_email'),
            'customer_address': request.form.get('customer_address'),
            'customer_city':    request.form.get('customer_city'),
            'customer_state':   request.form.get('customer_state'),
            'customer_zip':     request.form.get('customer_zip'),
            'product_id':       request.form.get('product_id'),
            'product_name':     request.form.get('product_name'),
            'quantity':         int(request.form.get('quantity',1)),
            'price':            float(request.form.get('price',0)),
            'shipping_cost':    float(request.form.get('shipping_cost',0)),
            'status': 'pending',
            'tracking_number': '', 'carrier': '',
            'created_at': datetime.datetime.now().isoformat()
        }
        new_order['total'] = (new_order['price'] * new_order['quantity']) + new_order['shipping_cost']
        orders.append(new_order)
        save_orders(orders, slug)
        flash(f'Order {new_order["id"]} created!', 'success')
        return redirect(url_for('order_detail', order_id=new_order['id']))
    products = load_products(slug)
    return render_template('add_order.html', products=products, **ctx())

@app.route('/order/<order_id>/ship', methods=['GET','POST'])
@login_required
def ship_order(order_id):
    slug = active_slug()
    orders = load_orders(slug)
    order = next((o for o in orders if o.get('id')==order_id), None)
    if not order: flash('Order not found','error'); return redirect(url_for('list_orders'))
    if request.method == 'POST':
        order['tracking_number'] = request.form.get('tracking_number')
        order['carrier']         = request.form.get('carrier')
        order['status']          = 'shipped'
        order['shipped_at']      = datetime.datetime.now().isoformat()
        save_orders(orders, slug)
        flash('Order shipped!','success')
        return redirect(url_for('order_detail', order_id=order_id))
    return render_template('ship_order.html', order=order, **ctx())

@app.route('/order/<order_id>/delivered')
@login_required
def mark_delivered(order_id):
    slug = active_slug()
    orders = load_orders(slug)
    order = next((o for o in orders if o.get('id')==order_id), None)
    if order:
        order['status'] = 'delivered'
        order['delivered_at'] = datetime.datetime.now().isoformat()
        save_orders(orders, slug)
        flash('Order delivered!','success')
    return redirect(url_for('order_detail', order_id=order_id))

# ── Products ───────────────────────────────────────────────────────────────────
@app.route('/products')
@login_required
def list_products():
    return render_template('products.html', products=load_products(active_slug()), **ctx())

@app.route('/product/add', methods=['GET','POST'])
@login_required
def add_product():
    slug = active_slug()
    if request.method == 'POST':
        products = load_products(slug)
        products.append({
            'id': f"PRD-{len(products)+1:05d}",
            'name': request.form.get('name'), 'sku': request.form.get('sku'),
            'supplier': request.form.get('supplier'),
            'cost': float(request.form.get('cost',0)),
            'price': float(request.form.get('price',0)),
            'weight': float(request.form.get('weight',0)),
            'stock': int(request.form.get('stock',0)),
            'created_at': datetime.datetime.now().isoformat()
        })
        save_products(products, slug)
        flash('Product added!','success')
        return redirect(url_for('list_products'))
    return render_template('add_product.html', suppliers=load_suppliers(slug), **ctx())

# ── Suppliers ──────────────────────────────────────────────────────────────────
@app.route('/suppliers')
@login_required
def list_suppliers():
    return render_template('suppliers.html', suppliers=load_suppliers(active_slug()), **ctx())

@app.route('/supplier/add', methods=['GET','POST'])
@login_required
def add_supplier():
    slug = active_slug()
    if request.method == 'POST':
        suppliers = load_suppliers(slug)
        suppliers.append({
            'id': f"SUP-{len(suppliers)+1:05d}",
            'name': request.form.get('name'), 'email': request.form.get('email'),
            'phone': request.form.get('phone'), 'address': request.form.get('address'),
            'website': request.form.get('website'), 'notes': request.form.get('notes'),
            'created_at': datetime.datetime.now().isoformat()
        })
        save_suppliers(suppliers, slug)
        flash('Supplier added!','success')
        return redirect(url_for('list_suppliers'))
    return render_template('add_supplier.html', **ctx())

# ── Customers ──────────────────────────────────────────────────────────────────
@app.route('/customers')
@login_required
def list_customers():
    return render_template('customers.html', customers=load_customers_data(active_slug()), **ctx())

@app.route('/customer/add', methods=['GET','POST'])
@login_required
def add_customer():
    slug = active_slug()
    if request.method == 'POST':
        customers = load_customers_data(slug)
        customers.append({
            'id': f"CUST-{len(customers)+1:05d}",
            'name': request.form.get('name'), 'email': request.form.get('email'),
            'phone': request.form.get('phone'), 'address': request.form.get('address'),
            'total_orders': 0, 'total_spent': 0,
            'created_at': datetime.datetime.now().isoformat()
        })
        save_customers_data(customers, slug)
        flash('Customer added!','success')
        return redirect(url_for('list_customers'))
    return render_template('add_customer.html', **ctx())

# ── Analytics ──────────────────────────────────────────────────────────────────
@app.route('/analytics')
@login_required
def analytics():
    slug = active_slug()
    orders   = load_orders(slug)
    products = load_products(slug)
    total_revenue   = sum(float(o.get('total',0)) for o in orders)
    total_orders    = len(orders)
    avg_order_value = total_revenue / total_orders if total_orders > 0 else 0
    pending   = len([o for o in orders if o.get('status')=='pending'])
    shipped   = len([o for o in orders if o.get('status')=='shipped'])
    delivered = len([o for o in orders if o.get('status')=='delivered'])
    orders_by_day = {}
    for o in orders:
        day = o.get('created_at','')[:10]
        if day: orders_by_day[day] = orders_by_day.get(day,0)+1
    product_sales = {}
    for o in orders:
        prod = o.get('product_name','Unknown')
        product_sales[prod] = product_sales.get(prod,0)+1
    top_products = sorted(product_sales.items(), key=lambda x:x[1], reverse=True)[:5]
    return render_template('analytics.html',
        total_revenue=total_revenue, total_orders=total_orders,
        avg_order_value=avg_order_value, pending=pending, shipped=shipped,
        delivered=delivered, orders_by_day=orders_by_day, top_products=top_products, **ctx())

# ── AI CEO ─────────────────────────────────────────────────────────────────────
@app.route('/ceo')
@login_required
def ceo_dashboard():
    return render_template('ceo_dashboard.html', **ctx())

@app.route('/api/ceo/think', methods=['POST'])
@login_required
def api_ceo_think():
    slug   = active_slug()
    prompt = (request.get_json() or {}).get('prompt','')
    if not prompt: return jsonify({'error':'No prompt'}), 400
    orders   = load_orders(slug)
    products = load_products(slug)
    context  = f"Orders: {len(orders)}, Products: {len(products)}, Revenue: ${sum(float(o.get('total',0)) for o in orders):.2f}"
    return jsonify({'response': ceo_think(prompt, slug, context), 'time': datetime.datetime.now().isoformat()})

@app.route('/api/ceo/analyze', methods=['GET'])
@login_required
def api_ceo_analyze():
    slug     = active_slug()
    orders   = load_orders(slug)
    products = load_products(slug)
    revenue  = sum(float(o.get('total',0)) for o in orders)
    prompt   = f"Analyze my dropshipping business: {len(orders)} orders, {len(products)} products, ${revenue:.2f} revenue. Pending: {len([o for o in orders if o.get('status')=='pending'])}. Give 3 specific recommendations."
    return jsonify({'analysis': ceo_think(prompt, slug), 'stats': {'orders': len(orders), 'products': len(products), 'revenue': revenue}})

@app.route('/api/ceo/marketing', methods=['GET'])
@login_required
def api_ceo_marketing():
    slug   = active_slug()
    orders = load_orders(slug)
    prompt = f"Create a 7-day marketing plan for my dropshipping store. I have {len(orders)} orders so far. Make it specific and actionable."
    return jsonify({'plan': ceo_think(prompt, slug)})

@app.route('/api/research-product', methods=['POST'])
@login_required
def api_research_product():
    slug  = active_slug()
    niche = (request.get_json() or {}).get('niche','')
    if not niche: return jsonify({'error':'No niche'}), 400
    prompt = f"Analyze this dropshipping niche: {niche}. Cover: profit potential, target audience, competition, best marketing angles, red flags to avoid."
    return jsonify({'niche': niche, 'analysis': ceo_think(prompt, slug)})

@app.route('/api/create-ad', methods=['POST'])
@login_required
def api_create_ad():
    slug    = active_slug()
    data    = request.get_json() or {}
    product = data.get('product','')
    platform = data.get('platform','facebook')
    prompt  = f"Write a high-converting {platform} ad for this dropshipping product: {product}. Include: headline, body copy (2-3 paragraphs), and a strong call to action."
    return jsonify({'ad': ceo_think(prompt, slug), 'platform': platform})

@app.route('/api/trending-niches', methods=['GET'])
@login_required
def api_trending_niches():
    slug   = active_slug()
    prompt = "List 10 hot dropshipping niches right now. For each: niche name, one sentence why it's popular, and estimated profit margin. Be specific."
    return jsonify({'niches': ceo_think(prompt, slug)})

# ── Marketing / Research / Alerts ─────────────────────────────────────────────
@app.route('/marketing')
@login_required
def marketing(): return render_template('marketing.html', **ctx())

@app.route('/research')
@login_required
def product_research(): return render_template('research.html', **ctx())

@app.route('/alerts')
@login_required
def inventory_alerts():
    slug     = active_slug()
    products = load_products(slug)
    orders   = load_orders(slug)
    return render_template('alerts.html',
        low_stock=[p for p in products if p.get('stock',100) < 10],
        out_of_stock=[p for p in products if p.get('stock',100) <= 0],
        pending_orders=[o for o in orders if o.get('status')=='pending'], **ctx())

@app.route('/profit-calculator')
@login_required
def profit_calculator(): return render_template('profit_calculator.html', **ctx())

@app.route('/shipping')
@login_required
def shipping(): return render_template('shipping.html', **ctx())

# ── Settings ───────────────────────────────────────────────────────────────────
@app.route('/settings', methods=['GET','POST'])
@login_required
def settings():
    slug = active_slug()
    is_admin = session.get('role') == 'admin'
    if request.method == 'POST':
        openrouter_key   = request.form.get('openrouter_key','').strip()
        openrouter_model = request.form.get('openrouter_model','google/gemini-flash-1.5').strip()
        groq_key         = request.form.get('groq_key','').strip()
        if slug and not is_admin:
            cfg = load_client_config(slug)
            if openrouter_key:   cfg['openrouter_key']   = openrouter_key
            if openrouter_model: cfg['openrouter_model'] = openrouter_model
            if groq_key:         cfg['groq_key']         = groq_key
            save_client_config(slug, cfg)
        else:
            if openrouter_key:   set_config('openrouter_key', openrouter_key)
            if openrouter_model: set_config('openrouter_model', openrouter_model)
            if groq_key:         set_config('groq_key', groq_key)
        flash('Settings saved!','success')
        return redirect(url_for('settings'))
    current_model = get_openrouter_model(slug)
    current_key = get_openrouter_key(slug)
    key_set = bool(current_key or get_groq_key(slug))
    # Build a masked preview so the user can confirm which key is active
    if current_key and len(current_key) > 8:
        current_key_preview = current_key[:8] + '...' + current_key[-4:]
    elif current_key:
        current_key_preview = '••••••••'
    else:
        current_key_preview = ''
    oc = {'gateway_url': '', 'token': '', 'agent': '', 'token_set': False}
    return render_template('settings.html', current_model=current_model, key_set=key_set,
                           current_key_preview=current_key_preview, oc=oc, **ctx())

@app.route('/settings/email', methods=['GET','POST'])
@login_required
def email_settings_page():
    slug     = active_slug()
    path     = data_path('email_settings.json', slug)
    settings = load_json(path, {})
    if request.method == 'POST':
        settings = {
            'enabled':   'enabled' in request.form,
            'smtp_host': request.form.get('smtp_host',''),
            'smtp_port': request.form.get('smtp_port','587'),
            'username':  request.form.get('username',''),
            'password':  request.form.get('password',''),
            'from_email':request.form.get('from_email',''),
        }
        save_json(path, settings)
        flash('Email settings saved!','success')
        return redirect(url_for('email_settings_page'))
    return render_template('email_settings.html', settings=settings, **ctx())

@app.route('/settings/pricing', methods=['GET','POST'])
@login_required
def pricing_settings():
    slug = active_slug()
    path = data_path('pricing_settings.json', slug)
    settings = load_json(path, {})
    if request.method == 'POST':
        settings = {
            'default_markup_percent': float(request.form.get('default_markup',50)),
            'min_profit_percent':     float(request.form.get('min_profit',20)),
            'shipping_handling':      float(request.form.get('shipping_handling',5)),
            'platform_fee_percent':   float(request.form.get('platform_fee',2.9)),
        }
        save_json(path, settings)
        flash('Pricing settings saved!','success')
        return redirect(url_for('pricing_settings'))
    return render_template('pricing_settings.html', settings=settings, **ctx())

# ── Overseer (super admin) ─────────────────────────────────────────────────────
@app.route('/overseer')
@admin_required
def overseer():
    stores = list_client_stores()
    revenue = sum(99.0 for s in stores if s.get('status')=='active')
    return render_template('overseer.html', stores=stores, total_revenue=revenue,
        active_count=sum(1 for s in stores if s.get('status')=='active'),
        suspended_count=sum(1 for s in stores if s.get('status')=='suspended'),
        leads=load_leads(), **ctx())

@app.route('/overseer/client/create', methods=['POST'])
@admin_required
def overseer_create_client():
    store_name    = request.form.get('store_name','').strip()
    contact_email = request.form.get('contact_email','').strip()
    temp_password = request.form.get('temp_password','').strip()
    niche         = request.form.get('niche','general')
    if not store_name or not contact_email or not temp_password:
        flash('Name, email, and password required.','error')
        return redirect(url_for('overseer'))
    slug = slugify(store_name)
    base = slug; counter = 1
    while os.path.exists(os.path.join(CUSTOMERS_DIR, slug)):
        slug = f'{base}-{counter}'; counter += 1
    now = datetime.datetime.now().isoformat()
    cfg = {'store_name':store_name,'slug':slug,'contact_email':contact_email,'niche':niche,'plan':'starter','status':'active','created_at':now}
    save_client_config(slug, cfg)
    users_path = os.path.join(CUSTOMERS_DIR, slug, 'users.json')
    save_json(users_path, {contact_email: {'password': hash_pw(temp_password), 'role':'client', 'store_slug':slug, 'created_at':now}})
    flash(f'Client "{store_name}" created! Login: {contact_email} / {temp_password}', 'success')
    return redirect(url_for('overseer'))

@app.route('/overseer/client/<slug>/impersonate', methods=['POST'])
@admin_required
def overseer_impersonate(slug):
    cfg = load_client_config(slug)
    if not cfg: flash('Store not found.','error'); return redirect(url_for('overseer'))
    session['impersonating_slug'] = slug
    flash(f'Now managing {cfg["store_name"]}.','success')
    return redirect(url_for('dashboard'))

@app.route('/overseer/exit-impersonate')
@admin_required
def overseer_exit():
    session.pop('impersonating_slug', None)
    flash('Returned to overseer.','success')
    return redirect(url_for('overseer'))

@app.route('/overseer/client/<slug>/suspend', methods=['POST'])
@admin_required
def overseer_suspend(slug):
    cfg = load_client_config(slug)
    if cfg:
        cfg['status'] = 'suspended' if cfg.get('status')=='active' else 'active'
        save_client_config(slug, cfg)
        flash(f'Store {cfg["status"]}.','success')
    return redirect(url_for('overseer'))

@app.route('/overseer/client/<slug>/delete', methods=['POST'])
@admin_required
def overseer_delete(slug):
    import shutil
    store_dir = os.path.join(CUSTOMERS_DIR, slug)
    if os.path.exists(store_dir): shutil.rmtree(store_dir)
    flash('Store deleted.','success')
    return redirect(url_for('overseer'))

# ── AI Assistant API (OpenRouter) ────────────────────────────────────────────────────────────────────────────────────────────────────────
@app.route('/api/bot/chat', methods=['POST'])
@login_required
def api_bot_chat():
    import urllib.request as ur, urllib.error as ue
    slug    = active_slug()
    data    = request.get_json() or {}
    message = data.get('message','').strip()
    history = data.get('history',[])
    image_b64  = data.get('image', None)
    image_mime = data.get('image_mime','image/jpeg')
    if not message and not image_b64: return jsonify({'error':'No message'}), 400
    api_key = _get_ai_key(slug)
    model   = _get_ai_model(slug)
    if not api_key:
        return jsonify({'error':'No OpenRouter API key set. Add one in Settings ⚙️ → API Keys.'}), 400
    orders   = load_orders(slug)
    products = load_products(slug)
    revenue  = sum(float(o.get('total',0)) for o in orders)
    pending  = len([o for o in orders if o.get('status')=='pending'])
    store_name = load_client_config(slug).get('store_name', APP_NAME) if slug else APP_NAME
    items_summary = '; '.join(f"{p.get('name','?')} (${p.get('price','?')})" for p in products[:15])
    system = (f"You are the AI CEO for {store_name}, a dropshipping business. "
              f"Stats: {len(orders)} orders, {len(products)} products, ${revenue:.2f} revenue, {pending} pending. "
              f"Products: {items_summary}. "
              f"Help with strategy, product research, pricing, marketing, and growing the business. Be concise.")
    messages = [{'role':'system','content':system}]
    for h in history[-10:]:
        if h.get('role') in ('user','assistant') and h.get('content'):
            messages.append({'role':h['role'],'content':h['content']})
    if image_b64:
        user_content = [{'type':'text','text': message or 'Analyze this for my dropshipping store.'},
                        {'type':'image_url','image_url':{'url':f'data:{image_mime};base64,{image_b64}'}}]
    else:
        user_content = message
    messages.append({'role':'user','content':user_content})
    try:
        payload = json.dumps({'model':model,'messages':messages,'stream':False}).encode()
        req = ur.Request('https://openrouter.ai/api/v1/chat/completions', data=payload,
            headers={'Authorization':f'Bearer {api_key}','Content-Type':'application/json',
                     'HTTP-Referer':'https://dropship-ai.app','X-Title':"Jay\'s Dropship Shipping Ran By AI"})
        with ur.urlopen(req, timeout=90) as resp:
            result = json.loads(resp.read())
        return jsonify({'reply': result['choices'][0]['message']['content']})
    except ue.HTTPError as e:
        body=''
        try: body=e.read().decode()
        except: pass
        return jsonify({'error': f'OpenRouter error {e.code}: {body or e.reason}'}), 502
    except Exception as e:
        return jsonify({'error': str(e)}), 502

# ── Misc API ───────────────────────────────────────────────────────────────────
@app.route('/api/business/status', methods=['GET'])
@login_required
def api_business_status():
    slug = active_slug()
    orders = load_orders(slug); products = load_products(slug); suppliers = load_suppliers(slug)
    return jsonify({'orders':len(orders),'products':len(products),'suppliers':len(suppliers),
        'revenue':sum(float(o.get('total',0)) for o in orders),
        'pending_shipping':len([o for o in orders if o.get('status')=='pending']),
        'recent_orders':orders[-5:] if orders else []})

@app.route('/api/calculate-shipping', methods=['POST'])
def api_calc_shipping():
    data = request.json or {}
    weight = float(data.get('weight',0)); distance = float(data.get('distance',0))
    return jsonify({'estimated_cost': round(5.00+(weight*1.50)+(distance*0.10),2),'currency':'USD'})

@app.route('/api/calculate-price', methods=['POST'])
@login_required
def api_calculate_price():
    slug = active_slug()
    data = request.json or {}
    cost = float(data.get('cost',0))
    path = data_path('pricing_settings.json', slug)
    settings = load_json(path, {})
    markup = settings.get('default_markup_percent',50)
    shipping = settings.get('shipping_handling',5)
    selling_price = round((cost + shipping) * (1 + markup/100), 2)
    return jsonify({'cost':cost,'selling_price':selling_price,'profit':round(selling_price-cost,2),'profit_percent':round((selling_price-cost)/cost*100 if cost else 0,1)})

@app.route('/api/calculate-profit', methods=['POST'])
def api_calc_profit():
    data = request.json or {}
    product_cost = float(data.get('product_cost',0)); shipping_cost = float(data.get('shipping_cost',0))
    selling_price = float(data.get('selling_price',0))
    platform_fee  = selling_price * float(data.get('platform_fee',2.9))/100
    payment_fee   = selling_price * float(data.get('payment_processing',2.9))/100
    total_cost = product_cost + shipping_cost
    profit = selling_price - total_cost - platform_fee - payment_fee
    return jsonify({'revenue':selling_price,'total_cost':total_cost,'fees':round(platform_fee+payment_fee,2),'profit':round(profit,2),'profit_margin':round(profit/selling_price*100 if selling_price else 0,1)})

@app.route('/api/auto-customer', methods=['POST'])
@login_required
def api_auto_customer():
    slug = active_slug()
    data = request.get_json() or {}
    email = data.get('email','')
    customers = load_customers_data(slug)
    existing = next((c for c in customers if c.get('email')==email), None)
    if existing:
        existing['total_orders'] = existing.get('total_orders',0)+1
        existing['total_spent']  = existing.get('total_spent',0)+data.get('total',0)
    else:
        customers.append({'id':f"CUST-{len(customers)+1:05d}",'name':data.get('name',''),'email':email,'phone':data.get('phone',''),'address':data.get('address',''),'total_orders':1,'total_spent':data.get('total',0),'created_at':datetime.datetime.now().isoformat()})
    save_customers_data(customers, slug)
    return jsonify({'success':True,'customer_count':len(customers)})

@app.route('/products/import', methods=['GET','POST'])
@login_required
def import_products():
    slug = active_slug()
    if request.method == 'POST':
        file = request.files.get('file')
        if file:
            content = file.read().decode('utf-8')
            lines   = content.strip().split('\n')
            products = load_products(slug); imported = 0
            for i, line in enumerate(lines):
                if i == 0: continue
                parts = line.split(',')
                if len(parts) >= 4:
                    products.append({'id':f"PRD-{len(products)+imported+1:05d}",'name':parts[0].strip(),'sku':parts[1].strip(),'cost':float(parts[2].strip()) if parts[2].strip() else 0,'price':float(parts[3].strip()) if len(parts)>3 and parts[3].strip() else 0,'supplier':parts[4].strip() if len(parts)>4 else '','stock':100,'created_at':datetime.datetime.now().isoformat()})
                    imported += 1
            save_products(products, slug)
            flash(f'Imported {imported} products!','success')
        return redirect(url_for('list_products'))
    return render_template('import_products.html', **ctx())

@app.route('/signup', methods=['GET','POST'])
def signup():
    if request.method == 'POST':
        return redirect(url_for('wizard'))
    return render_template('signup.html', **ctx())

@app.route('/success')
def success(): return render_template('success.html', **ctx())

@app.route('/cancel')
def cancel(): return render_template('cancel.html', **ctx())


# ============================================================

# ============================================================
# STRUCTURED LOGGING + METRICS
# ============================================================
import logging as _log, time as _t

import bcrypt as _bcrypt_lib

def _sha256_hash(pw):
    import hashlib
    return hashlib.sha256(pw.encode()).hexdigest()

def _is_sha256_hash(h):
    return isinstance(h, str) and len(h) == 64 and all(c in '0123456789abcdef' for c in h.lower())

def _bcrypt_hash(pw):
    return _bcrypt_lib.hashpw(pw.encode('utf-8'), _bcrypt_lib.gensalt()).decode('utf-8')

def _bcrypt_verify(pw, stored):
    if _is_sha256_hash(stored):
        return _sha256_hash(pw) == stored, True  # valid, needs_upgrade
    try:
        return _bcrypt_lib.checkpw(pw.encode('utf-8'), stored.encode('utf-8')), False
    except Exception:
        return False, False


_log_handler = _log.StreamHandler()
_log_handler.setFormatter(_log.Formatter('%(asctime)s %(levelname)s %(message)s'))
app.logger.addHandler(_log_handler)
app.logger.setLevel(_log.INFO)

def _ensure_metrics():
    try:
        db = get_db()
        db.execute("""CREATE TABLE IF NOT EXISTS metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            metric TEXT NOT NULL, value REAL DEFAULT 1,
            tenant_slug TEXT,
            created_at TEXT DEFAULT (datetime('now')))""")
        db.commit()
    except Exception:
        pass

def track(metric, value=1, slug=None):
    try:
        _ensure_metrics()
        get_db().execute(
            "INSERT INTO metrics (metric,value,tenant_slug) VALUES (?,?,?)",
            (metric, value, slug))
        get_db().commit()
    except Exception:
        pass

@app.before_request
def _start_timer():
    from flask import g
    g._start = _t.time()


@app.after_request
def _add_security_headers(response):
    """Security headers on every response."""
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    if 'Content-Security-Policy' not in response.headers:
        response.headers['Content-Security-Policy'] = "default-src 'self' 'unsafe-inline' 'unsafe-eval' https: data: blob:;"
    return response

@app.after_request
def _log_req(response):
    from flask import g
    if not request.path.startswith('/static'):
        ms = (_t.time() - getattr(g, '_start', _t.time())) * 1000
        if ms > 800:
            app.logger.warning(f"SLOW {request.method} {request.path} {response.status_code} {ms:.0f}ms")
    return response



# ============================================================
# SEO — Sitemap + Robots.txt
# ============================================================
# ── AI Product Sourcer ────────────────────────────────────────────────────────
@app.route('/source')
@login_required
def sourcer_page():
    return render_template('sourcer.html', last_query=request.args.get('q',''), **ctx())

@app.route('/api/source-products', methods=['POST'])
@login_required
def api_source_products():
    """AI searches for wholesale deals and returns structured product data."""
    import datetime as _dt
    data = request.get_json()
    niche = data.get('niche', '').strip()
    if not niche:
        return jsonify({'error': 'Please enter a product to search for.'}), 400

    key = _get_ai_key(active_slug())
    if not key:
        return jsonify({'error': 'Add your OpenRouter API key in Settings first.'}), 400

    model = _get_ai_model(active_slug())

    prompt = f"""You are an expert dropshipping sourcing agent. The user wants to sell: {niche}

Search your knowledge for real wholesale suppliers and return sourcing data.

Return ONLY valid JSON in this exact format:
{{
  "products": [
    {{
      "name": "Product name",
      "description": "Brief description",
      "cost": 12.50,
      "sell_price": 34.99,
      "supplier": "AliExpress / DHgate / Faire / etc",
      "supplier_url": "https://...",
      "sku": "AUTO-001",
      "weight": 0.5,
      "stock": 100,
      "shipping_estimate": "7-14 days from China",
      "category": "Shoes"
    }}
  ],
  "suppliers": [
    {{
      "name": "Supplier name",
      "platform": "AliExpress / DHgate / Wholesale platform",
      "website": "https://...",
      "description": "What they specialize in",
      "min_order": "$50",
      "shipping": "7-14 days",
      "rating": "4.8"
    }}
  ],
  "summary": "2-3 sentence market analysis: profit potential, competition level, best platforms to sell on"
}}

Rules:
- Return 5-8 products with realistic wholesale prices
- Include 2-4 real supplier platforms (AliExpress, DHgate, Alibaba, Faire, etc.)
- Cost should be 25-50% of sell price for good margins
- Be specific with real product names and prices
- Return ONLY the JSON, no other text"""

    import re as _re

    def _extract_json(text):
        """Robustly extract a JSON object from AI response text."""
        # Strip code fences
        if '```json' in text:
            text = text.split('```json')[1].split('```')[0].strip()
        elif '```' in text:
            text = text.split('```')[1].split('```')[0].strip()
        else:
            # Find the outermost JSON object in the response
            match = _re.search(r'\{[\s\S]*\}', text)
            if match:
                text = match.group(0)
        return text.strip()

    def _call_ai(prompt_text):
        """Make one OpenRouter call and return the content string."""
        import urllib.request as _ur
        _payload = json.dumps({
            'model': model,
            'messages': [{'role': 'user', 'content': prompt_text}],
            'max_tokens': 2000
        }).encode()
        _req = _ur.Request(
            'https://openrouter.ai/api/v1/chat/completions',
            data=_payload,
            headers={
                'Authorization': f'Bearer {key}',
                'Content-Type': 'application/json',
                'HTTP-Referer': 'https://libertyemporium.com',
                'X-Title': 'AI Auto Dropshipping'
            }
        )
        with _ur.urlopen(_req, timeout=45) as _resp:
            _result = json.loads(_resp.read())
            return _result['choices'][0]['message']['content'].strip()

    try:
        text = _call_ai(prompt)
        extracted = _extract_json(text)

        try:
            sourced = json.loads(extracted)
        except json.JSONDecodeError:
            # Retry once with an even stricter instruction
            retry_prompt = (
                "You MUST return ONLY valid JSON and nothing else. "
                "No markdown, no explanation, no code fences. Just the raw JSON object.\n\n"
                + prompt
            )
            text2 = _call_ai(retry_prompt)
            extracted2 = _extract_json(text2)
            try:
                sourced = json.loads(extracted2)
            except json.JSONDecodeError:
                return jsonify({'error': 'AI returned invalid JSON after two attempts. Try switching to a different model in Settings.'}), 500

        # Validate the response has the expected structure
        if 'products' not in sourced:
            sourced['products'] = []
        if 'suppliers' not in sourced:
            sourced['suppliers'] = []
        if 'summary' not in sourced:
            sourced['summary'] = ''

        return jsonify(sourced)

    except json.JSONDecodeError as e:
        return jsonify({'error': 'AI returned invalid data. Try a different model in Settings.'}), 500
    except Exception as e:
        return jsonify({'error': f'Sourcing failed: {str(e)}'}), 500

@app.route('/api/source-products/add-product', methods=['POST'])
@login_required
def api_add_sourced_product():
    """Add a single sourced product to the products list."""
    import datetime as _dt
    data = request.get_json()
    p = data.get('product', {})
    if not p or not p.get('name'):
        return jsonify({'error': 'Invalid product data'}), 400

    slug = active_slug()
    products = load_products(slug)
    new_id = f"PRD-{len(products)+1:05d}"
    products.append({
        'id': new_id,
        'name': p.get('name', 'Unknown'),
        'sku': p.get('sku', f'SKU-{new_id}'),
        'supplier': p.get('supplier', ''),
        'cost': float(p.get('cost', 0)),
        'price': float(p.get('sell_price', 0)),
        'weight': float(p.get('weight', 0.5)),
        'stock': int(p.get('stock', 50)),
        'description': p.get('description', ''),
        'category': p.get('category', ''),
        'supplier_url': p.get('supplier_url', ''),
        'shipping_estimate': p.get('shipping_estimate', ''),
        'source': 'AI Sourcer',
        'created_at': _dt.datetime.now().isoformat()
    })
    save_products(products, slug)
    return jsonify({'success': True, 'id': new_id})

@app.route('/api/source-products/add-supplier', methods=['POST'])
@login_required
def api_add_sourced_supplier():
    """Add a supplier to the suppliers list."""
    import datetime as _dt
    data = request.get_json()
    s = data.get('supplier', {})
    if not s or not s.get('name'):
        return jsonify({'error': 'Invalid supplier data'}), 400

    slug = active_slug()
    suppliers = load_suppliers(slug)
    # Don't add duplicates
    if any(x.get('name','').lower() == s['name'].lower() for x in suppliers):
        return jsonify({'success': True, 'note': 'Already exists'})
    new_id = f"SUP-{len(suppliers)+1:05d}"
    suppliers.append({
        'id': new_id,
        'name': s.get('name', ''),
        'email': s.get('email', ''),
        'phone': s.get('phone', ''),
        'address': s.get('address', ''),
        'website': s.get('website', ''),
        'platform': s.get('platform', ''),
        'min_order': s.get('min_order', ''),
        'shipping': s.get('shipping', ''),
        'rating': s.get('rating', ''),
        'notes': s.get('description', '') + ' | ' + s.get('notes', ''),
        'source': 'AI Sourcer',
        'created_at': _dt.datetime.now().isoformat()
    })
    save_suppliers(suppliers, slug)
    return jsonify({'success': True, 'id': new_id})

@app.route('/api/source-products/add-all', methods=['POST'])
@login_required
def api_add_all_sourced():
    """Add all sourced products and suppliers at once."""
    import datetime as _dt
    data = request.get_json()
    slug = active_slug()
    products_added = 0
    suppliers_added = 0

    for p in data.get('products', []):
        if not p.get('name'): continue
        products = load_products(slug)
        new_id = f"PRD-{len(products)+1:05d}"
        products.append({
            'id': new_id, 'name': p.get('name',''), 'sku': p.get('sku', f'SKU-{new_id}'),
            'supplier': p.get('supplier',''), 'cost': float(p.get('cost',0)),
            'price': float(p.get('sell_price',0)), 'weight': float(p.get('weight',0.5)),
            'stock': int(p.get('stock',50)), 'description': p.get('description',''),
            'supplier_url': p.get('supplier_url',''), 'source': 'AI Sourcer',
            'created_at': _dt.datetime.now().isoformat()
        })
        save_products(products, slug)
        products_added += 1

    for s in data.get('suppliers', []):
        if not s.get('name'): continue
        suppliers = load_suppliers(slug)
        if any(x.get('name','').lower() == s['name'].lower() for x in suppliers):
            continue
        new_id = f"SUP-{len(suppliers)+1:05d}"
        suppliers.append({
            'id': new_id, 'name': s.get('name',''), 'website': s.get('website',''),
            'platform': s.get('platform',''), 'min_order': s.get('min_order',''),
            'shipping': s.get('shipping',''), 'rating': s.get('rating',''),
            'notes': s.get('description',''), 'source': 'AI Sourcer',
            'created_at': _dt.datetime.now().isoformat()
        })
        save_suppliers(suppliers, slug)
        suppliers_added += 1

    return jsonify({'success': True, 'products_added': products_added, 'suppliers_added': suppliers_added})

@app.route('/sitemap.xml')
def sitemap():
    """Auto-generated XML sitemap for SEO."""
    host = request.host_url.rstrip('/')
    urls = [
        {'loc': f"{host}/",          'priority': '1.0', 'changefreq': 'weekly'},
        {'loc': f"{host}/login",     'priority': '0.8', 'changefreq': 'monthly'},
        {'loc': f"{host}/signup",    'priority': '0.9', 'changefreq': 'monthly'},
        {'loc': f"{host}/pricing",   'priority': '0.8', 'changefreq': 'monthly'},
    ]
    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        xml.append(f"  <url>")
        xml.append(f"    <loc>{u['loc']}</loc>")
        xml.append(f"    <changefreq>{u['changefreq']}</changefreq>")
        xml.append(f"    <priority>{u['priority']}</priority>")
        xml.append(f"  </url>")
    xml.append('</urlset>')
    return '\n'.join(xml), 200, {'Content-Type': 'application/xml'}

@app.route('/robots.txt')
def robots():
    """robots.txt for search engine crawling guidance."""
    host = request.host_url.rstrip('/')
    content = f"""User-agent: *
Allow: /
Disallow: /admin
Disallow: /overseer
Disallow: /api/
Sitemap: {host}/sitemap.xml
"""
    return content, 200, {'Content-Type': 'text/plain'}


# GLOBAL ERROR HANDLERS
# ============================================================
@app.errorhandler(404)
def not_found_error(e):
    if request.path.startswith('/api/'):
        return __import__('flask').jsonify({'error': 'Not found'}), 404
    return render_template('404.html') if os.path.exists(
        os.path.join(app.template_folder or 'templates', '404.html')
    ) else ('<h1>404 - Page Not Found</h1>', 404)

@app.errorhandler(500)
def internal_error(e):
    app.logger.error(f"UNHANDLED_500: {str(e)}", exc_info=True)
    if request.path.startswith('/api/'):
        return __import__('flask').jsonify({'error': 'Internal server error'}), 500
    return '<h1>500 - Something went wrong. We are looking into it.</h1>', 500

@app.errorhandler(429)
def rate_limit_error(e):
    return __import__('flask').jsonify({'error': 'Too many requests. Please slow down.'}), 429


# ── Admin-only API token UI routes ───────────────────────────────────────────
@app.route('/api/token/ui', methods=['POST'])
def api_token_ui_generate():
    if not session.get('role') == 'admin':
        return jsonify({'error': 'Admin only'}), 403
    import secrets as _s, hashlib as _h, datetime as _dt
    user_id = session.get('user_id') or session.get('super_admin_id') or 1
    label = 'ui-generated'
    raw_token = _s.token_urlsafe(48)
    token_hash = _h.sha256(raw_token.encode()).hexdigest()
    expires_at = (_dt.datetime.utcnow() + _dt.timedelta(days=365)).isoformat()
    conn = get_db()
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS api_tokens (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, token_hash TEXT UNIQUE, label TEXT, expires_at TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
        conn.execute('DELETE FROM api_tokens WHERE user_id=? AND label=?', (user_id, label))
        conn.execute('INSERT INTO api_tokens (user_id,token_hash,label,expires_at) VALUES (?,?,?,?)', (user_id, token_hash, label, expires_at))
        conn.commit()
    finally:
        conn.close()
    return jsonify({'success':True,'api_token':raw_token,'expires_at':expires_at})

@app.route('/api/token/ui', methods=['DELETE'])
def api_token_ui_revoke():
    if not session.get('role') == 'admin':
        return jsonify({'error': 'Admin only'}), 403
    user_id = session.get('user_id') or session.get('super_admin_id') or 1
    conn = get_db()
    try:
        conn.execute('DELETE FROM api_tokens WHERE user_id=? AND label=?', (user_id, 'ui-generated'))
        conn.commit()
    finally:
        conn.close()
    return jsonify({'success':True})


# ── API Key Infrastructure ────────────────────────────────────────────────────
import secrets as _api_secrets, hashlib as _api_hash, functools as _api_functools

_API_KEYS_FILE = os.path.join(os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', '/data'), 'api_keys.json')

def _load_api_keys():
    if os.path.exists(_API_KEYS_FILE):
        with open(_API_KEYS_FILE) as f:
            import json as _j; return _j.load(f)
    return {}

def _save_api_keys(keys):
    with open(_API_KEYS_FILE, 'w') as f:
        import json as _j; _j.dump(keys, f, indent=2)

def _require_api_key(f):
    """Decorator: require valid API key via X-API-Key header, Authorization: Bearer, or ?api_key= param."""
    @_api_functools.wraps(f)
    def decorated(*args, **kwargs):
        key = (request.headers.get('X-API-Key') or
               request.args.get('api_key') or
               (request.headers.get('Authorization','')[7:].strip() if request.headers.get('Authorization','').startswith('Bearer ') else None))
        if not key:
            return jsonify({'error': 'API key required. Pass as X-API-Key header or Authorization: Bearer <key>'}), 401
        keys = _load_api_keys()
        if key not in keys:
            return jsonify({'error': 'Invalid API key'}), 401
        return f(*args, **kwargs)
    return decorated

@app.route('/admin/api-generator')
@login_required
def _admin_api_generator_page():
    if session.get('username') != 'admin' and session.get('role') != 'overseer':
        return jsonify({'error': 'Admin only'}), 403
    keys = _load_api_keys()
    new_key = request.args.get('new_key', '')
    base_url = request.host_url.rstrip('/')
    return render_template('admin_api_generator.html',
        api_keys=keys, new_key=new_key, base_url=base_url,
        endpoints=[('GET', '/api/orders', 'Get all orders'), ('GET', '/api/orders/<id>', 'Get single order'), ('GET', '/api/products', 'Get all products'), ('GET', '/api/suppliers', 'Get all suppliers'), ('GET', '/api/stats', 'Dashboard stats'), ('GET', '/health', 'Health check (no auth)')])

@app.route('/admin/api-generator/generate', methods=['POST'])
@login_required
def _admin_api_generate():
    if session.get('username') != 'admin' and session.get('role') != 'overseer':
        return jsonify({'error': 'Admin only'}), 403
    from datetime import datetime as _dt
    label = request.form.get('label','Testing Key').strip() or 'Testing Key'
    raw_key = 'ds_' + _api_secrets.token_urlsafe(32)
    keys = _load_api_keys()
    keys[raw_key] = {'name': label, 'created_by': 'admin', 'created_at': _dt.utcnow().isoformat(), 'active': True}
    _save_api_keys(keys)
    flash(f'API key generated!', 'success')
    return redirect('/admin/api-generator?new_key=' + raw_key)

@app.route('/admin/api-generator/revoke/<path:key>', methods=['POST'])
@login_required
def _admin_api_revoke(key):
    if session.get('username') != 'admin' and session.get('role') != 'overseer':
        return jsonify({'error': 'Admin only'}), 403
    keys = _load_api_keys()
    if key in keys:
        del keys[key]
        _save_api_keys(keys)
        flash('Key revoked.', 'success')
    return redirect('/admin/api-generator')

# ── Public API ───────────────────────────────────────────────────────────────
@app.route('/api/orders', methods=['GET'])
@_require_api_key
def _api_get_orders():
    try:
        orders = load_orders() if callable(globals().get('load_orders')) else []
        return jsonify({'count': len(orders), 'orders': orders})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/orders/<order_id>', methods=['GET'])
@_require_api_key
def _api_get_order(order_id):
    try:
        orders = load_orders() if callable(globals().get('load_orders')) else []
        order = next((o for o in orders if str(o.get('id','')) == str(order_id)), None)
        if not order: return jsonify({'error': 'Order not found'}), 404
        return jsonify(order)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/products', methods=['GET'])
@_require_api_key
def _api_ds_products():
    try:
        products = load_products() if callable(globals().get('load_products')) else []
        return jsonify({'count': len(products), 'products': products})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/suppliers', methods=['GET'])
@_require_api_key
def _api_ds_suppliers():
    try:
        suppliers = load_suppliers() if callable(globals().get('load_suppliers')) else []
        return jsonify({'count': len(suppliers), 'suppliers': suppliers})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/stats', methods=['GET'])
@_require_api_key
def _api_ds_stats():
    try:
        orders = load_orders() if callable(globals().get('load_orders')) else []
        products = load_products() if callable(globals().get('load_products')) else []
        return jsonify({'total_orders': len(orders), 'total_products': len(products), 'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)


# ── Email / SMTP ───────────────────────────────────────────────────────────────

def get_smtp_config():
    """Load SMTP settings from env vars.
    Set these in Railway: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM
    """
    return {
        'host':     os.environ.get('SMTP_HOST', ''),
        'port':     int(os.environ.get('SMTP_PORT', 587)),
        'user':     os.environ.get('SMTP_USER', ''),
        'password': os.environ.get('SMTP_PASSWORD', ''),
        'from':     os.environ.get('SMTP_FROM', os.environ.get('SMTP_USER', 'noreply@example.com')),
    }

def send_email(to, subject, body):
    """Send plain-text email via SMTP. Returns (True, '') or (False, error_msg).
    If SMTP is not configured, logs to console instead (safe — never leaks to browser).
    """
    cfg = get_smtp_config()
    if not cfg['host'] or not cfg['user'] or not cfg['password']:
        # SMTP not configured — log to console only, never show token on screen
        print(f'[EMAIL-NOOP] To: {to} | Subject: {subject}', flush=True)
        print(f'[EMAIL-NOOP] Body: {body}', flush=True)
        return False, 'SMTP not configured'
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(body, 'plain', 'utf-8')
        msg['Subject'] = subject
        msg['From']    = cfg['from']
        msg['To']      = to
        if cfg['port'] == 465:
            with smtplib.SMTP_SSL(cfg['host'], 465, timeout=15) as s:
                s.login(cfg['user'], cfg['password'])
                s.sendmail(cfg['from'], [to], msg.as_string())
        else:
            with smtplib.SMTP(cfg['host'], cfg['port'], timeout=15) as s:
                s.ehlo(); s.starttls()
                s.login(cfg['user'], cfg['password'])
                s.sendmail(cfg['from'], [to], msg.as_string())
        return True, ''
    except Exception as e:
        return False, str(e)


# ── Forgot / Reset Password ────────────────────────────────────────────────────

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    import hashlib as _hl, secrets as _sec, datetime as _dt
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        # Check if email exists across all tenants
        found = False
        token = _sec.token_urlsafe(24)
        # Save reset token
        import os as _os
        resets_path = _os.path.join(DATA_DIR, 'password_resets.json')
        resets = []
        try:
            if _os.path.exists(resets_path):
                with open(resets_path) as f: resets = json.load(f)
        except: pass
        # Check tenant users
        for store in list_client_stores():
            upath = _os.path.join(CUSTOMERS_DIR, store['slug'], 'users.json')
            if not _os.path.exists(upath): continue
            with open(upath) as f:
                users = json.load(f)
            if email in users:
                found = True
                resets = [r for r in resets if r.get('email') != email]
                resets.append({
                    'email': email, 'token': token, 'slug': store['slug'],
                    'expires': (_dt.datetime.now() + _dt.timedelta(hours=2)).isoformat(),
                    'created': _dt.datetime.now().isoformat()
                })
                break
        if found:
            with open(resets_path, 'w') as f: json.dump(resets, f, indent=2)
            reset_url = request.host_url.rstrip('/') + f'/reset-password/{token}'
            send_email(
                to=email,
                subject='Reset Your Password',
                body=(
                    "Hi,\n\n"
                    "A password reset was requested for your account.\n\n"
                    "Click this link to set a new password (valid for 2 hours):\n"
                    + reset_url +
                    "\n\nIf you didn't request this, you can safely ignore this email.\n\n"
                    "-- Support"
                )
            )
            flash('If that email is registered, a reset link has been sent.', 'info')
        else:
            # Don't reveal if email exists
            flash('If that email is registered, a reset link has been generated.', 'info')
        return redirect(url_for('forgot_password'))
    return render_template('forgot_password.html', **ctx())

@app.route('/debug-error')
def debug_error():
    import traceback
    try:
        return render_template('login.html', **ctx())
    except Exception as e:
        return '<pre>' + traceback.format_exc() + '</pre>', 500


@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    import os as _os, datetime as _dt
    resets_path = _os.path.join(DATA_DIR, 'password_resets.json')
    resets = []
    try:
        if _os.path.exists(resets_path):
            with open(resets_path) as f: resets = json.load(f)
    except: pass
    reset = next((r for r in resets if r.get('token') == token), None)
    if not reset:
        flash('Invalid or expired reset link.', 'error')
        return redirect(url_for('login'))
    if _dt.datetime.fromisoformat(reset['expires']) < _dt.datetime.now():
        flash('Reset link has expired. Please request a new one.', 'error')
        return redirect(url_for('forgot_password'))
    if request.method == 'POST':
        new_pw = request.form.get('password', '').strip()
        if len(new_pw) < 6:
            flash('Password must be at least 6 characters.', 'error')
            return render_template('reset_password.html', token=token, **ctx())
        # Update password
        slug = reset['slug']
        email = reset['email']
        upath = _os.path.join(CUSTOMERS_DIR, slug, 'users.json')
        with open(upath) as f: users = json.load(f)
        if email in users:
            users[email]['password'] = hash_pw(new_pw)
            with open(upath, 'w') as f: json.dump(users, f, indent=2)
        # Remove used token
        resets = [r for r in resets if r.get('token') != token]
        with open(resets_path, 'w') as f: json.dump(resets, f, indent=2)
        flash('Password updated! You can now sign in.', 'success')
        return redirect(url_for('login'))
    return render_template('reset_password.html', token=token, email=reset.get('email',''), **ctx())
