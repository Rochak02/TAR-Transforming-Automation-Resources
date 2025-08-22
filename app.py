# This is the updated Flask web server application (app.py)
# New Features:
# 1. Parallel username/password login system.
# 2. Secure password hashing for storage and verification.
# 3. Updated database schema and registration to include passwords.

import os
import shutil
import sqlite3
from flask import Flask, render_template, request, url_for, g, jsonify, redirect, send_from_directory, make_response
from flask_socketio import SocketIO
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

DATABASE = 'users.db'
USER_FILES_DIR = 'user_files'

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-very-secret-key!'
app.config['USER_FILES_DIR'] = USER_FILES_DIR
socketio = SocketIO(app)

# --- Database Functions ---

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        db.execute('DROP TABLE IF EXISTS users;')
        db.execute('''
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid TEXT NOT NULL UNIQUE,
                username TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL,
                first_name TEXT NOT NULL,
                middle_name TEXT,
                last_name TEXT NOT NULL,
                age INTEGER,
                gender TEXT,
                state TEXT,
                email TEXT,
                contact_number TEXT,
                address TEXT
            );
        ''')
        db.commit()
    print("Initialized the database with the new schema including passwords.")

# --- File and Directory Setup ---
if not os.path.exists(USER_FILES_DIR):
    os.makedirs(USER_FILES_DIR)

# --- Helper function for path safety ---
def get_safe_path(base_path, user_provided_path=""):
    if user_provided_path:
        user_provided_path = os.path.normpath(user_provided_path).lstrip('/')
    full_path = os.path.join(base_path, user_provided_path)
    if os.path.commonprefix((os.path.realpath(full_path), os.path.realpath(base_path))) != os.path.realpath(base_path):
        raise ValueError("Attempted directory traversal.")
    return full_path

# --- Web Routes ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/user/<uid>')
def handle_user_scan(uid):
    with app.app_context():
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE uid = ?', (uid,)).fetchone()
        target_url = url_for('register_page', uid=uid)
        if user:
            target_url = url_for('user_dashboard', uid=uid)
        socketio.emit('card_scanned', {'url': target_url})
    return jsonify(status="ok")

@app.route('/login', methods=['POST'])
def handle_login():
    username = request.form['username']
    password = request.form['password']
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()

    if user and check_password_hash(user['password'], password):
        dashboard_url = url_for('user_dashboard', uid=user['uid'])
        return jsonify({'success': True, 'dashboard_url': dashboard_url})
    
    return jsonify({'success': False, 'message': 'Invalid username or password.'})


@app.route('/register/<uid>', methods=['GET'])
def register_page(uid):
    return render_template('register.html', uid=uid)

@app.route('/register', methods=['POST'])
def register_user():
    # Capture all form data
    uid = request.form['uid']
    username = request.form['username']
    password = request.form['password']
    first_name = request.form['first_name']
    middle_name = request.form['middle_name']
    last_name = request.form['last_name']
    age = request.form['age']
    gender = request.form['gender']
    state = request.form['state']
    email = request.form['email']
    contact_number = request.form['contact_number']
    address = request.form['address']

    # Hash the password for secure storage
    hashed_password = generate_password_hash(password)
    
    db = get_db()
    try:
        db.execute('''
            INSERT INTO users (uid, username, password, first_name, middle_name, last_name, age, gender, state, email, contact_number, address)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (uid, username, hashed_password, first_name, middle_name, last_name, age, gender, state, email, contact_number, address))
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'message': 'Username already exists.'})

    user_dir = os.path.join(app.config['USER_FILES_DIR'], uid)
    if not os.path.exists(user_dir):
        os.makedirs(user_dir)
    
    dashboard_url = url_for('user_dashboard', uid=uid)
    return jsonify({'success': True, 'dashboard_url': dashboard_url})

@app.route('/dashboard/<uid>/', defaults={'path': ''})
@app.route('/dashboard/<uid>/<path:path>')
def user_dashboard(uid, path):
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE uid = ?', (uid,)).fetchone()
    if not user:
        return redirect(url_for('register_page', uid=uid))

    base_user_dir = os.path.join(app.config['USER_FILES_DIR'], uid)
    try:
        current_path = get_safe_path(base_user_dir, path)
    except ValueError:
        return "Invalid path specified.", 400

    if not os.path.exists(current_path) or not os.path.isdir(current_path):
        return "Path does not exist.", 404

    items = os.listdir(current_path)
    files = sorted([item for item in items if os.path.isfile(os.path.join(current_path, item))])
    folders = sorted([item for item in items if os.path.isdir(os.path.join(current_path, item))])

    breadcrumbs = []
    if path:
        parts = path.split('/')
        for i, part in enumerate(parts):
            breadcrumb_path = '/'.join(parts[:i+1])
            breadcrumbs.append({'name': part, 'path': breadcrumb_path})

    response = make_response(render_template('dashboard.html', user=user, files=files, folders=folders, current_path=path, breadcrumbs=breadcrumbs))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

# Other routes remain the same
@app.route('/upload/<uid>', methods=['POST'])
def upload_file(uid):
    current_path = request.form.get('current_path', '')
    base_user_dir = os.path.join(app.config['USER_FILES_DIR'], uid)
    try:
        upload_dir = get_safe_path(base_user_dir, current_path)
    except ValueError:
        return "Invalid path specified.", 400
    if 'file' not in request.files:
        return redirect(url_for('user_dashboard', uid=uid, path=current_path))
    file = request.files['file']
    if file.filename == '':
        return redirect(url_for('user_dashboard', uid=uid, path=current_path))
    if file:
        filename = secure_filename(file.filename)
        file.save(os.path.join(upload_dir, filename))
    return redirect(url_for('user_dashboard', uid=uid, path=current_path))

@app.route('/create_folder/<uid>', methods=['POST'])
def create_folder(uid):
    current_path = request.form.get('current_path', '')
    folder_name = request.form.get('folder_name')
    base_user_dir = os.path.join(app.config['USER_FILES_DIR'], uid)
    if folder_name:
        try:
            target_dir = get_safe_path(base_user_dir, current_path)
            new_folder_path = os.path.join(target_dir, secure_filename(folder_name))
            if not os.path.exists(new_folder_path):
                os.makedirs(new_folder_path)
        except ValueError:
            return "Invalid path specified.", 400
    return redirect(url_for('user_dashboard', uid=uid, path=current_path))

@app.route('/delete/<uid>/<path:item_path>', methods=['POST'])
def delete_item(uid, item_path):
    base_user_dir = os.path.join(app.config['USER_FILES_DIR'], uid)
    try:
        full_path = get_safe_path(base_user_dir, item_path)
        parent_dir = os.path.dirname(item_path)
    except ValueError:
        return "Invalid path specified.", 400
    if os.path.exists(full_path):
        if os.path.isfile(full_path):
            os.remove(full_path)
        elif os.path.isdir(full_path):
            shutil.rmtree(full_path)
    return redirect(url_for('user_dashboard', uid=uid, path=parent_dir))

@app.route('/view/<uid>/<path:filename>')
def view_file(uid, filename):
    base_user_dir = os.path.join(app.config['USER_FILES_DIR'], uid)
    try:
        file_dir = os.path.dirname(filename)
        actual_filename = os.path.basename(filename)
        directory_to_serve = get_safe_path(base_user_dir, file_dir)
        return send_from_directory(directory_to_serve, actual_filename)
    except ValueError:
        return "Invalid path specified.", 400

@app.route('/download/<uid>/<path:filename>')
def download_file(uid, filename):
    base_user_dir = os.path.join(app.config['USER_FILES_DIR'], uid)
    try:
        file_dir = os.path.dirname(filename)
        actual_filename = os.path.basename(filename)
        directory_to_serve = get_safe_path(base_user_dir, file_dir)
        return send_from_directory(directory_to_serve, actual_filename, as_attachment=True)
    except ValueError:
        return "Invalid path specified.", 400

if __name__ == '__main__':
    if not os.path.exists(DATABASE):
        init_db()
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
