"""Тесты для маршрутов Flask-приложения из app.py.

Каждый тест работает с отдельной базой данных в памяти (через фикстуру
`client`) и не обращается к внешним сервисам (CoinGecko, SMTP), поэтому
набор безопасно запускать в CI (GitHub Actions).
"""
import sqlite3

import pytest

import app as app_module


@pytest.fixture
def client(monkeypatch):
    # Изолированная БД в памяти, чтобы тесты не трогали реальный users.db.
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    cur = app_module.init_db(conn)
    monkeypatch.setattr(app_module, 'conn', conn)
    monkeypatch.setattr(app_module, 'cur', cur)
    monkeypatch.setattr(app_module, 'cache', {})

    # Не ходим в реальный CoinGecko API во время тестов.
    monkeypatch.setattr(app_module, 'get_live_rates', lambda: {})
    # Не отправляем реальные письма во время тестов.
    monkeypatch.setattr(app_module, 'send_welcome_email', lambda *a, **k: True)

    app_module.app.config.update(TESTING=True)
    with app_module.app.test_client() as test_client:
        yield test_client

    conn.close()


def register(client, name='User', email='user@example.com', password='secret123'):
    return client.post('/register/', data={
        'name': name,
        'email': email,
        'password': password,
    }, follow_redirects=False)


def test_homepage_ok(client):
    response = client.get('/')
    assert response.status_code == 200


def test_register_hashes_password(client):
    register(client)

    user = app_module.get_user_by_email('user@example.com')
    assert user is not None
    assert user[3] != 'secret123'  # пароль не должен храниться в открытом виде


def test_register_duplicate_email_does_not_create_second_user(client):
    register(client)
    register(client, name='Other')

    cur = app_module.cur
    cur.execute('SELECT COUNT(*) FROM users WHERE email = ?', ['user@example.com'])
    assert cur.fetchone()[0] == 1


def test_login_with_correct_password_succeeds(client):
    register(client)

    response = client.post('/login/', data={
        'email': 'user@example.com',
        'password': 'secret123',
    }, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers['Location'].startswith('/user/')


def test_login_with_wrong_password_fails(client):
    register(client)

    response = client.post('/login/', data={
        'email': 'user@example.com',
        'password': 'wrong-password',
    })

    assert response.status_code == 200
    assert 'Пароль неверный'.encode() in response.data


def test_login_unknown_email_fails(client):
    response = client.post('/login/', data={
        'email': 'nobody@example.com',
        'password': 'whatever',
    })

    assert response.status_code == 200
    assert 'Нет такой почты'.encode() in response.data


def test_user_page_not_found(client):
    response = client.get('/user/9999')
    assert response.status_code == 404


def test_api_posts_returns_json(client):
    response = client.get('/api/posts')
    assert response.status_code == 200
    data = response.get_json()
    assert 'posts' in data
    assert data['total'] == 0


def test_add_post_requires_login(client):
    response = client.post('/add_post', data={
        'title': 'Title',
        'content': 'Content',
        'category': '1',
    })
    assert response.status_code == 302
    assert response.headers['Location'] == '/login/'
