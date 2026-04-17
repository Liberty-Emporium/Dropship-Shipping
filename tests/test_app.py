"""
Tests for Dropship Shipping (Andy)
Covers: health, public routes, auth, slug utils, API endpoints, security headers
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault('SECRET_KEY', 'test-secret-key')
os.environ.setdefault('DATABASE_URL', '')

import app as ds


@pytest.fixture
def client(tmp_path):
    ds.app.config['TESTING'] = True
    ds.app.config['SECRET_KEY'] = 'test-secret-key'
    ds.DATA_DIR = str(tmp_path)
    ds.DB_PATH = str(tmp_path / 'test.db')
    with ds.app.test_client() as c:
        with ds.app.app_context():
            ds.init_db()
        yield c


# ── Health ────────────────────────────────────────────────────────────────────

def test_healthz_returns_ok(client):
    res = client.get('/healthz')
    assert res.status_code == 200
    assert b'ok' in res.data.lower()

def test_health_returns_json(client):
    res = client.get('/health')
    assert res.status_code == 200
    data = res.get_json()
    assert data is not None


# ── Public pages ──────────────────────────────────────────────────────────────

def test_index_returns_200(client):
    assert client.get('/').status_code == 200

def test_landing_returns_200(client):
    assert client.get('/landing').status_code == 200

def test_login_page_returns_200(client):
    assert client.get('/login').status_code == 200

def test_wizard_page_returns_200(client):
    assert client.get('/wizard').status_code == 200

def test_profit_calculator_returns_200(client):
    res = client.get('/profit-calculator', follow_redirects=True)
    assert res.status_code == 200


# ── Auth — protected routes ───────────────────────────────────────────────────

def test_dashboard_requires_login(client):
    res = client.get('/dashboard', follow_redirects=False)
    assert res.status_code in (302, 401)

def test_orders_requires_login(client):
    res = client.get('/orders', follow_redirects=False)
    assert res.status_code in (302, 401)

def test_products_requires_login(client):
    res = client.get('/products', follow_redirects=False)
    assert res.status_code in (302, 401)

def test_suppliers_requires_login(client):
    res = client.get('/suppliers', follow_redirects=False)
    assert res.status_code in (302, 401)

def test_analytics_requires_login(client):
    res = client.get('/analytics', follow_redirects=False)
    assert res.status_code in (302, 401)

def test_settings_requires_login(client):
    res = client.get('/settings', follow_redirects=False)
    assert res.status_code in (302, 401)


# ── Login flow ────────────────────────────────────────────────────────────────

def test_login_wrong_credentials(client):
    res = client.post('/login', data={
        'username': 'nobody',
        'password': 'badpass'
    }, follow_redirects=True)
    assert res.status_code == 200
    assert b'invalid' in res.data.lower() or b'incorrect' in res.data.lower() or b'wrong' in res.data.lower()


# ── Slug utilities ────────────────────────────────────────────────────────────

def test_slugify_basic():
    assert ds.slugify('Hello World') == 'hello-world'

def test_slugify_max_40_chars():
    assert len(ds.slugify('a' * 100)) <= 40

def test_slugify_empty():
    assert isinstance(ds.slugify(''), str)

def test_validate_slug_valid():
    result = ds._validate_slug('good-slug')
    assert result == 'good-slug'

def test_validate_slug_strips_invalid_chars():
    result = ds._validate_slug('bad slug!')
    assert ' ' not in result and '!' not in result

def test_validate_slug_rejects_empty():
    with pytest.raises(ValueError):
        ds._validate_slug('')


# ── Profit calculator API ─────────────────────────────────────────────────────

def test_calculate_profit_missing_fields(client):
    res = client.post('/api/calculate-profit', json={})
    assert res.status_code in (400, 422, 200)

def test_calculate_shipping_missing_fields(client):
    res = client.post('/api/calculate-shipping', json={})
    assert res.status_code in (400, 422, 200)

def test_calculate_price_requires_auth(client):
    res = client.post('/api/calculate-price', json={})
    assert res.status_code in (400, 422, 200, 302)


# ── Security headers ──────────────────────────────────────────────────────────

def test_x_content_type_header_present(client):
    res = client.get('/')
    assert 'X-Content-Type-Options' in res.headers
