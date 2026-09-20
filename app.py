# Импорты
from flask import Flask, render_template, request, redirect, session, jsonify
import sqlite3
import smtplib
from email.mime.text import MIMEText
import os
import secrets
import time
from datetime import datetime
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
import traceback
import sys
import threading
import requests

# Загружаем переменные окружения из .env файла
load_dotenv()

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)  # Секретный ключ для сессий


def init_db(connection):
    """Создаёт схему БД (если её ещё нет) и наполняет справочники по умолчанию."""
    cursor = connection.cursor()

    # Создание таблицы пользователей
    cursor.execute('''CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                email TEXT,
                password TEXT,
                last_login TIMESTAMP,
                role TEXT DEFAULT 'user'
    )''')

    # Создание таблицы категорий
    cursor.execute('''CREATE TABLE IF NOT EXISTS categories(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT
    )''')

    # Создание таблицы постов
    cursor.execute('''CREATE TABLE IF NOT EXISTS posts(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                content TEXT,
                user_id INTEGER,
                category_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                edit_count INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (category_id) REFERENCES categories(id)
    )''')

    # Создание таблицы уведомлений
    cursor.execute('''CREATE TABLE IF NOT EXISTS notifications(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT,
                details TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
    )''')

    # Создание таблицы токенов аутентификации
    cursor.execute('''CREATE TABLE IF NOT EXISTS auth_tokens(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                token TEXT UNIQUE,
                expires_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
    )''')

    default_categories = [
        ('Программирование', 'Статьи о программировании и разработке'),
        ('Дизайн', 'Статьи о дизайне и UX/UI'),
        ('Путешествия', 'Рассказы о путешествиях'),
        ('Кулинария', 'Рецепты и кулинарные советы'),
        ('Спорт', 'Новости и статьи о спорте')
    ]

    for category in default_categories:
        cursor.execute('INSERT OR IGNORE INTO categories(name, description) VALUES (?, ?)', category)

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_id ON posts(user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_notif_user_id ON notifications(user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_token ON auth_tokens(token)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_category_id ON posts(category_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_post_title ON posts(title)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_post_content ON posts(content)')

    connection.commit()
    return cursor


# Подключение к базе данных
conn = sqlite3.connect('users.db', check_same_thread=False)
cur = init_db(conn)

# Простой кэш в памяти
cache = {}
def get_cached_posts(page, per_page):
    cache_key = f'posts_{page}_{per_page}'
    # Проверяем, есть ли данные в кэше и не устарели ли они (60 секунд)
    if cache_key in cache:
        cached_data, timestamp = cache[cache_key]
        if time.time() - timestamp < 60:
            return cached_data
    # Если нет в кэше или устарело, получаем из БД
    offset = (page - 1) * per_page
    cur.execute('''
        SELECT posts.*, users.name, categories.name as category_name
        FROM posts 
        JOIN users ON posts.user_id = users.id
        LEFT JOIN categories ON posts.category_id = categories.id
        ORDER BY posts.created_at DESC
        LIMIT ? OFFSET ?
    ''', [per_page, offset])
    posts = cur.fetchall()
    # Сохраняем в кэш
    cache[cache_key] = (posts, time.time())
    return posts

def send_error_email(subject, body):
    """Отправляет письмо об ошибке администратору."""
    from_email = os.getenv("EMAIL_USER")
    password = os.getenv("EMAIL_PASSWORD")
    admin_email = os.getenv("ADMIN_EMAIL")
    
    if not from_email or not password or not admin_email:
        print("ОШИБКА: EMAIL_USER, EMAIL_PASSWORD или ADMIN_EMAIL не установлены")
        return False
    
    msg = MIMEText(body, 'plain', 'utf-8')
    msg['Subject'] = subject
    msg['From'] = from_email
    msg['To'] = admin_email
    
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(from_email, password)
        server.send_message(msg)
        server.quit()
        print(f"Письмо об ошибке отправлено на {admin_email}")
        return True
    except Exception as e:
        print(f"Ошибка отправки письма об ошибке: {e}")
        return False

def get_post_by_id(post_id):
    cur.execute('''SELECT posts.*, users.name, categories.name as category_name
                   FROM posts 
                   JOIN users ON posts.user_id = users.id
                   LEFT JOIN categories ON posts.category_id = categories.id
                   WHERE posts.id = ?''', [post_id])
    return cur.fetchone()


def update_post(post_id, title, content, category_id):
    cur.execute('''UPDATE posts 
                   SET title = ?, content = ?, category_id = ?, 
                       edit_count = edit_count + 1
                   WHERE id = ?''', 
                [title, content, category_id, post_id])
    conn.commit()

# Функция отправки welcome-письма
def send_welcome_email(to_email, username):
    # Получаем данные из переменных окружения
    from_email = os.getenv("EMAIL_USER")
    password = os.getenv("EMAIL_PASSWORD")    
    # Проверяем, что переменные загружены
    if not from_email or not password:
        print("ОШИБКА: EMAIL_USER или EMAIL_PASSWORD не установлены в переменных окружения!")
        return False
    subject = "Добро пожаловать в наш блог!"
    body = f"""
    Привет, {username}!
    Спасибо за регистрацию в нашем блога.
    С уважением,
    Команда блога
    """
    msg = MIMEText(body, 'plain', 'utf-8')
    msg['Subject'] = subject
    msg['From'] = from_email
    msg['To'] = to_email    
    try:
        # Настройки для Gmail
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()  # Включаем шифрование
        server.login(from_email, password)
        server.send_message(msg)
        server.quit()        
        print(f"  Письмо успешно отправлено на {to_email}")
        return True
    except smtplib.SMTPAuthenticationError:
        print(" Ошибка аутентификации. Проверьте email и пароль приложения.")
    except Exception as e:
        print(f" Ошибка отправки письма: {e}")   
    return False

# Добавляет нового пользователя и возвращает его ID
def add_user(name, email, password):
    cur.execute('INSERT INTO users(name, email, password, last_login) VALUES (?, ?, ?, ?)', 
                [name, email, password, datetime.now()])
    conn.commit()
    cur.execute('SELECT id FROM users WHERE email = ?', [email])
    return cur.fetchone()[0]

# Возвращает пользователя по его ID
def get_user_by_id(user_id):
    cur.execute('SELECT * FROM users WHERE id = ?', [user_id])
    return cur.fetchone()

# Возвращает пользователя по его электронной почте
def get_user_by_email(email):
    cur.execute('SELECT * FROM users WHERE email = ?', [email])
    return cur.fetchone()

# Обновляет время последнего входа
def update_last_login(user_id):
    cur.execute('UPDATE users SET last_login = ? WHERE id = ?', 
                [datetime.now(), user_id])
    conn.commit()

# Добавляет новый пост с привязкой к пользователю
def add_new_post(title, content, user_id, category_id):
    cur.execute('INSERT INTO posts(title, content, user_id, category_id) VALUES (?, ?, ?, ?)', 
                [title, content, user_id, category_id])
    conn.commit()
    
    # Очищаем кэш постов при добавлении нового
    global cache
    cache = {}

def get_all_categories():
    cur.execute('SELECT * FROM categories ORDER BY name')
    return cur.fetchall()

# Возвращает посты пользователя
def get_posts_by_user(user_id):
    cur.execute('SELECT * FROM posts WHERE user_id = ? ORDER BY created_at DESC', [user_id])
    return cur.fetchall()

def get_posts_by_category(category_id):
    cur.execute('''SELECT posts.*, users.name, categories.name as category_name
                   FROM posts 
                   JOIN users ON posts.user_id = users.id
                   LEFT JOIN categories ON posts.category_id = categories.id
                   WHERE posts.category_id = ? 
                   ORDER BY posts.created_at DESC''', 
                [category_id])
    return cur.fetchall()

# Функция для поиска постов 
def search_posts(query):
    search_pattern = f'%{query}%'
    cur.execute('''SELECT posts.*, users.name, categories.name as category_name
                   FROM posts 
                   JOIN users ON posts.user_id = users.id
                   LEFT JOIN categories ON posts.category_id = categories.id
                   WHERE posts.title LIKE ? OR posts.content LIKE ?
                   ORDER BY posts.created_at DESC''', 
                [search_pattern, search_pattern])
    return cur.fetchall()

# Функция для получения всех пользователей 
def get_all_users():
    cur.execute('SELECT * FROM users')
    return cur.fetchall()

# Создает токен аутентификации
def create_auth_token(user_id, remember=False):
    token = secrets.token_hex(32)    
    if remember:
        expires_at = time.time() + 30 * 24 * 60 * 60  # 30 дней
    else:
        expires_at = time.time() + 60 * 60  # 1 час    
    cur.execute('INSERT INTO auth_tokens(user_id, token, expires_at) VALUES (?, ?, ?)',
                [user_id, token, expires_at])
    conn.commit()    
    return token

# Проверяет токен аутентификации
def validate_auth_token(token):
    cur.execute('SELECT user_id FROM auth_tokens WHERE token = ? AND expires_at > ?', [token, time.time()])
    result = cur.fetchone()    
    if result:
        return result[0]
    return None

# Удаляет токен аутентификации
def delete_auth_token(token):
    cur.execute('DELETE FROM auth_tokens WHERE token = ?', [token])
    conn.commit()

# Логирует уведомление
def log_notification(user_id, action, details):
    cur.execute('INSERT INTO notifications(user_id, action, details) VALUES (?, ?, ?)',
                [user_id, action, details])
    conn.commit()

# Возвращает уведомления пользователя
def get_notifications_by_user(user_id):
    cur.execute('SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC', 
                [user_id])
    return cur.fetchall()

# Middleware для проверки аутентификации
@app.before_request
def check_auth():
    if 'user_id' not in session:
        token = request.cookies.get('auth_token')
        if token:
            user_id = validate_auth_token(token)
            if user_id:
                user = get_user_by_id(user_id)
                if user:
                    session['user_id'] = user[0]
                    session['user_name'] = user[1]

def get_live_rates():
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {
        "ids": "bitcoin,ethereum,solana,tether",
        "vs_currencies": "usd,eur,rub",
    }
    try:
        response = requests.get(url, params=params, timeout=5)
        print("STATUS:", response.status_code, response.text[:200])
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print("RATES ERROR:", repr(e))
    return {}



# Рендерим стартовую страницу с пагинацией
@app.route('/')
def main():
    page = request.args.get('page', 1, type=int) # Исправлено тире на =
    per_page = 5
    
    cur.execute('SELECT COUNT(*) FROM posts')
    total_posts = cur.fetchone()[0]
    
    posts = get_cached_posts(page, per_page)
    users = cur.execute('SELECT * FROM users').fetchall()
    
    user_name = None
    if 'user_id' in session:
        user_name = session['user_name']
        
    total_pages = (total_posts + per_page - 1) // per_page
    
    # ИСПРАВЛЕНО: Переменную rates здесь больше передавать НЕ НАДО, 
    # контекстный процессор сделает это сам автоматически!
    return render_template(
        'main.html',
        posts=posts,
        users=users,
        user_name=user_name,
        current_page=page,
        total_pages=total_pages
    )


@app.context_processor
def inject_live_rates():

    return dict(rates=get_live_rates())


@app.errorhandler(500)
def internal_error(error):
    """Обрабатывает внутренние ошибки сервера (500) и уведомляет администратора."""
    # Получаем информацию об исключении
    exc_type, exc_value, exc_traceback = sys.exc_info()
    
    if exc_traceback is not None:
        tb_str = ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    else:
        tb_str = "Трассировка недоступна"
    
    # Собираем информацию о запросе
    request_info = (
        f"URL: {request.url}\n"
        f"Method: {request.method}\n"
        f"IP: {request.remote_addr}\n"
    )
    
    # Формируем тему и тело письма
    subject = f"Ошибка 500: {exc_type.__name__ if exc_type else 'Unknown'} - {exc_value if exc_value else 'No message'}"
    body = (
        "Произошла внутренняя ошибка сервера.\n\n"
        f"Информация о запросе:\n{request_info}\n"
        f"Трассировка:\n{tb_str}"
    )
    
    # Отправляем письмо в отдельном потоке, чтобы не блокировать ответ
    email_thread = threading.Thread(target=send_error_email, args=(subject, body))
    email_thread.daemon = True
    email_thread.start()
    
    # Возвращаем стандартный ответ пользователю
    return "Внутренняя ошибка сервера. Администратор уведомлен.", 500

@app.route("/converter")
def converter():
    return render_template('converter_handle.html')

# API для получения постов с пагинацией (JSON)

@app.route('/api/posts')
def api_posts():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 5, type=int)
    # Используем кэшированные данные
    posts = get_cached_posts(page, per_page)    
    # Преобразуем в словарь
    posts_list = []
    for post in posts:
        posts_list.append({
            'id': post[0],
            'title': post[1],
            'content': post[2][:200] + '...' if len(post[2]) > 200 else post[2],
            'user_id': post[3],
            'category_id': post[4],
            'created_at': post[5],
            'author': post[6],
            'category': post[7] if post[7] else 'Без категории'
        })
    # Получаем общее количество постов
    cur.execute('SELECT COUNT(*) FROM posts')
    total = cur.fetchone()[0]
    return jsonify({
        'posts': posts_list,
        'page': page,
        'per_page': per_page,
        'total': total,
        'total_pages': (total + per_page - 1) // per_page
    })

# Регистрация пользователя
@app.route('/register/', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        password = request.form.get('password')
        user = get_user_by_email(email)
        if user is None:
            user_id = add_user(name, email, generate_password_hash(password))
            # Отправляем письмо
            email_sent = send_welcome_email(email, name)            
            # Логируем действие
            if email_sent:
                log_notification(user_id, 'welcome_email_sent', 
                               f'Приветственное письмо отправлено на {email}')
            else:
                log_notification(user_id, 'welcome_email_failed', 
                               f'Не удалось отправить письмо на {email}')           
            return redirect('/login/')
        else:
            print('Такой пользователь уже есть')    
    return render_template('register.html')

# Процесс входа
@app.route('/login/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        remember = request.form.get('remember')        
        user = get_user_by_email(email)        
        if user is None:
            return render_template('login.html', message="Нет такой почты")
        if check_password_hash(user[3], password):
            print('Вход выполнен')
            # Сохраняем в сессию
            session['user_id'] = user[0]
            session['user_name'] = user[1]            
            # Обновляем время последнего входа
            update_last_login(user[0])            
            # Если "Запомнить меня", создаем токен
            if remember:
                token = create_auth_token(user[0], remember=True)
                response = redirect(f'/user/{user[0]}')
                response.set_cookie('auth_token', token, max_age=30*24*60*60)
            else:
                response = redirect(f'/user/{user[0]}')            
            # Логируем вход
            log_notification(user[0], 'login', 'Пользователь вошел в систему')
            return response
        else:
            return render_template('login.html', message="Пароль неверный")    
    return render_template('login.html')


@app.route('/api/v1/posts')
def api_v1_get_posts():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 5, type=int)
    author_id = request.args.get('author_id', type=int)

    # Валидация пагинации
    if page < 1:
        page = 1
    if per_page < 1 or per_page > 100:
        per_page = 5

    offset = (page - 1) * per_page

    base_select = '''
        SELECT posts.id, posts.title, posts.content, posts.user_id,
               posts.category_id, posts.created_at,
               users.name AS author, categories.name AS category
        FROM posts
        JOIN users ON posts.user_id = users.id
        LEFT JOIN categories ON posts.category_id = categories.id
    '''

    if author_id is not None:
        cur.execute('SELECT id, name FROM users WHERE id = ?', [author_id])
        author = cur.fetchone()
        if not author:
            return jsonify({
                'error': 'Автор не найден',
                'author_id': author_id
            }), 404

        cur.execute('SELECT COUNT(*) FROM posts WHERE user_id = ?', [author_id])
        total = cur.fetchone()[0]

        cur.execute(
            base_select + ' WHERE posts.user_id = ? ORDER BY posts.created_at DESC LIMIT ? OFFSET ?',
            [author_id, per_page, offset]
        )
    else:
        cur.execute('SELECT COUNT(*) FROM posts')
        total = cur.fetchone()[0]

        cur.execute(
            base_select + ' ORDER BY posts.created_at DESC LIMIT ? OFFSET ?',
            [per_page, offset]
        )

    rows = cur.fetchall()
    posts_list = [
        {
            'id': row[0],
            'title': row[1],
            'content': row[2][:200] + '...' if row[2] and len(row[2]) > 200 else row[2],
            'user_id': row[3],
            'category_id': row[4],
            'created_at': row[5],
            'author': row[6],
            'category': row[7] if row[7] else 'Без категории'
        }
        for row in rows
    ]

    response = {
        'posts': posts_list,
        'page': page,
        'per_page': per_page,
        'total': total,
        'total_pages': (total + per_page - 1) // per_page
    }

    if author_id is not None:
        response['filter'] = {
            'author_id': author_id,
            'author_name': author[1]
        }

    return jsonify(response)

# Выход из системы
@app.route('/logout')
def logout():
    token = request.cookies.get('auth_token')
    if token:
        delete_auth_token(token)    
    session.clear()    
    response = redirect('/')
    response.set_cookie('auth_token', '', expires=0)    
    return response

# Страница пользователя
@app.route('/user/<int:user_id>')
def user_page(user_id):
    user = get_user_by_id(user_id)
    posts = get_posts_by_user(user_id)
    notifications = get_notifications_by_user(user_id)    
    if user:
        return render_template('user_page.html', user=user, posts=posts, notifications=notifications)    
    return "Пользователь не найден", 404

@app.route('/add_post', methods=['GET', 'POST'])
def add_post():
    if request.method == 'POST':
        title = request.form.get('title')
        content = request.form.get('content')
        category_id = request.form.get('category', type=int)
        if 'user_id' in session:
            user_id = session['user_id']
        else:
            return redirect('/login/')    
        add_new_post(title, content, user_id, category_id)
        # Получаем название категории для лога
        cur.execute('SELECT name FROM categories WHERE id = ?', [category_id])
        category_name = cur.fetchone()
        category_name = category_name[0] if category_name else 'Неизвестно'
        
        log_notification(user_id, 'new_post', 
                        f'Создан пост "{title}" в категории "{category_name}"')
        return redirect('/')
    # При GET-запросе передаем категории в шаблон
    categories = get_all_categories()
    return render_template('new_post.html', categories=categories)

@app.route('/edit_post/<int:post_id>', methods=['GET', 'POST'])
def edit_post(post_id):
    # Проверяем авторизацию
    if 'user_id' not in session:
        return redirect('/login/')
    
    post = get_post_by_id(post_id)
    
    if not post:
        return "Пост не найден", 404
    
    # Проверяем, что пользователь — автор поста или админ
    user = get_user_by_id(session['user_id'])
    is_admin = bool(user) and user[5] == 'admin'
    
    if post[3] != session['user_id'] and not is_admin:
        return "У вас нет прав на редактирование этого поста", 403
    
    if request.method == 'POST':
        title = request.form.get('title')
        content = request.form.get('content')
        category_id = request.form.get('category', type=int)

        update_post(post_id, title, content, category_id)
        
        # Логируем действие
        log_notification(session['user_id'], 'edit_post', 
                        f'Отредактирован пост "{title}" (ID: {post_id})')
        
        return redirect(f'/post/{post_id}')
    
    # GET-запрос: показываем форму с текущими данными
    categories = get_all_categories()
    return render_template('edit_post.html', 
                         post=post, 
                         categories=categories,
                         edit_count=post[6] if len(post) > 6 else 0)

# Маршрут для поиска 
@app.route('/search')
def search():
    query = request.args.get('q', '')
    
    if query:
        posts = search_posts(query)
        return render_template('main.html', 
                             posts=posts, 
                             users=get_all_users(),
                             user_name=session.get('user_name'),
                             search_query=query)
    
    return redirect('/')

# Маршрут для отображения постов по категории 
@app.route('/category/<int:category_id>')
def category_posts(category_id):
    posts = get_posts_by_category(category_id)    
    # Получаем информацию о категории
    cur.execute('SELECT * FROM categories WHERE id = ?', [category_id])
    category = cur.fetchone()    
    if not category:
        return "Категория не найдена", 404    
    return render_template('category.html', 
                         posts=posts, 
                         category=category,
                         user_name=session.get('user_name'))

if __name__ == "__main__":
    debug_mode = os.getenv('FLASK_DEBUG', '0') == '1'
    app.run(host='0.0.0.0', port=5000, debug=debug_mode)