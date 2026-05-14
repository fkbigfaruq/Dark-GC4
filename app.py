"""
DARK_GC — Flask + SocketIO chat, Render-ready.

Database:
  - If env var DATABASE_URL is set (e.g. on Render with a Postgres add-on
    or Neon/Supabase), the app uses PostgreSQL — data is PERMANENT.
  - Otherwise it falls back to a local SQLite file (good for local dev).

Run locally:
    pip install -r requirements.txt
    python app.py

Render start command:
    python app.py
"""

import os
import sqlite3
import uuid
import time
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, jsonify, send_from_directory, abort
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_socketio import SocketIO, emit

# bot plugin (edit bot.py to add your own rules)
try:
    import bot as bot_plugin
except Exception as e:
    print("bot.py not loaded:", e)
    bot_plugin = None

# ---------- config ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)
DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(DATA_DIR, "dark_gc.db"))
AVATAR_DIR = os.environ.get("AVATAR_DIR", os.path.join(BASE_DIR, "static", "uploads"))
CHAT_IMG_DIR = os.environ.get("CHAT_IMG_DIR", os.path.join(BASE_DIR, "static", "chat_images"))
ALLOWED_EXT = {"png", "jpg", "jpeg", "gif", "webp"}
ADMIN_USERNAME = "fkbigfaruq"
SYSTEM_USER_ID = 0  # virtual user id for system / bot messages

os.makedirs(AVATAR_DIR, exist_ok=True)
os.makedirs(CHAT_IMG_DIR, exist_ok=True)

# ---------- pick database backend ----------
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_PG = bool(DATABASE_URL)

if USE_PG:
    import psycopg2
    import psycopg2.extras
    import psycopg2.errors
    # Render sometimes provides "postgres://" — psycopg2 wants "postgresql://"
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]
    INTEGRITY_ERRORS = (sqlite3.IntegrityError, psycopg2.IntegrityError)
    print(f"[db] Using PostgreSQL (permanent storage)")
else:
    INTEGRITY_ERRORS = (sqlite3.IntegrityError,)
    print(f"[db] Using SQLite at {DB_PATH} (NOT permanent on Render free tier)")


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-me-please-dark-gc-secret")
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")


# ---------- db helpers ----------
class DB:
    """Tiny adapter so the rest of the code can stay (mostly) the same."""
    def __init__(self):
        if USE_PG:
            self._conn = psycopg2.connect(DATABASE_URL, sslmode="require")
        else:
            d = os.path.dirname(DB_PATH)
            if d:
                os.makedirs(d, exist_ok=True)
            self._conn = sqlite3.connect(DB_PATH)
            self._conn.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        if USE_PG:
            cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql.replace("?", "%s"), params)
        else:
            cur = self._conn.execute(sql, params)
        return cur

    def insert_id(self, sql, params=()):
        """Run an INSERT and return the new row's id."""
        if USE_PG:
            cur = self._conn.cursor()
            cur.execute(sql.replace("?", "%s") + " RETURNING id", params)
            return cur.fetchone()[0]
        else:
            return self._conn.execute(sql, params).lastrowid

    def commit(self):
        self._conn.commit()

    def rollback(self):
        try:
            self._conn.rollback()
        except Exception:
            pass

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self.rollback()
        self.close()


def db():
    return DB()


def init_db():
    with db() as conn:
        if USE_PG:
            conn.execute("""CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                avatar TEXT,
                is_admin INTEGER DEFAULT 0,
                is_banned INTEGER DEFAULT 0,
                created_at BIGINT
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                content TEXT,
                image TEXT,
                reply_to INTEGER,
                created_at BIGINT,
                is_system INTEGER DEFAULT 0
            )""")
        else:
            conn.execute("""CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                avatar TEXT,
                is_admin INTEGER DEFAULT 0,
                is_banned INTEGER DEFAULT 0,
                created_at INTEGER
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                content TEXT,
                image TEXT,
                reply_to INTEGER,
                created_at INTEGER,
                is_system INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )""")
            try:
                conn.execute("ALTER TABLE messages ADD COLUMN is_system INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass

            conn.execute("""CREATE TABLE IF NOT EXISTS rooms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT,
                is_locked INTEGER DEFAULT 0
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS room_access (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                room_id INTEGER,
                approved INTEGER DEFAULT 0
            )""")
            try:
                conn.execute("ALTER TABLE messages ADD COLUMN room_id INTEGER DEFAULT 1")
            except Exception:
                pass

        conn.commit()


init_db()


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT


# ---------- auth helpers ----------
def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*a, **kw)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if "user_id" not in session:
            return redirect(url_for("login"))
        with db() as conn:
            u = conn.execute("SELECT is_admin FROM users WHERE id=?", (session["user_id"],)).fetchone()
        if not u or not u["is_admin"]:
            abort(403)
        return f(*a, **kw)
    return wrapper


def current_user():
    if "user_id" not in session:
        return None
    with db() as conn:
        return conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()


# ---------- system / bot helpers ----------
def post_system_message(text, target_username=None):
    ts = int(time.time())
    with db() as conn:
        mid = conn.insert_id(
            "INSERT INTO messages (user_id, content, image, reply_to, created_at, is_system) "
            "VALUES (?,?,?,?,?,1)",
            (SYSTEM_USER_ID, text, None, None, ts)
        )
        conn.commit()
    payload = {
        "id": mid, "user_id": SYSTEM_USER_ID,
        "username": "system", "avatar": None,
        "content": text, "image": None, "reply_to": None,
        "created_at": ts, "is_system": True,
        "target_username": target_username,
    }
    socketio.emit("new_message", payload)


def post_bot_message(text):
    ts = int(time.time())
    with db() as conn:
        mid = conn.insert_id(
            "INSERT INTO messages (user_id, content, image, reply_to, created_at, is_system) "
            "VALUES (?,?,?,?,?,1)",
            (SYSTEM_USER_ID, text, None, None, ts)
        )
        conn.commit()
    bot_name = getattr(bot_plugin, "BOT_NAME", "bot") if bot_plugin else "bot"
    payload = {
        "id": mid, "user_id": SYSTEM_USER_ID,
        "username": bot_name, "avatar": None,
        "content": text, "image": None, "reply_to": None,
        "created_at": ts, "is_system": True,
        "target_username": None,
    }
    socketio.emit("new_message", payload)


# ---------- routes ----------
@app.route("/")
def index():
    return render_template("index.html", user=current_user())


@app.route("/signup", methods=["GET", "POST"])
def signup():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or not password:
            error = "Username and password required"
        elif len(username) < 3:
            error = "Username too short (min 3)"
        elif len(password) < 4:
            error = "Password too short (min 4)"
        else:
            try:
                with db() as conn:
                    is_admin = 1 if username == ADMIN_USERNAME else 0
                    conn.execute(
                        "INSERT INTO users (username, password, is_admin, created_at) VALUES (?,?,?,?)",
                        (username, generate_password_hash(password), is_admin, int(time.time()))
                    )
                    conn.commit()
                return redirect(url_for("login"))
            except INTEGRITY_ERRORS:
                error = "Username already taken"
    return render_template("signup.html", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with db() as conn:
            u = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if u and check_password_hash(u["password"], password):
            if u["is_banned"]:
                error = "You are banned"
            else:
                session["user_id"] = u["id"]
                session["username"] = u["username"]
                return redirect(url_for("dashboard"))
        else:
            error = "Invalid credentials"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))




@app.route("/dashboard")
@login_required
def dashboard():
    with db() as conn:
        rooms = conn.execute("SELECT * FROM rooms ORDER BY id ASC").fetchall()
        if not rooms:
            conn.execute("INSERT INTO rooms(name,description,is_locked) VALUES (?,?,?)",("Main Room","Public chat",0))
            conn.execute("INSERT INTO rooms(name,description,is_locked) VALUES (?,?,?)",("Hacker Room","Premium hacker room",1))
            conn.execute("INSERT INTO rooms(name,description,is_locked) VALUES (?,?,?)",("Coding Room","Premium coding room",1))
            conn.commit()
            rooms = conn.execute("SELECT * FROM rooms ORDER BY id ASC").fetchall()
    return render_template("dashboard.html", rooms=rooms, user=current_user())

@app.route("/room/<int:room_id>")
@login_required
def room(room_id):
    user=current_user()
    with db() as conn:
        room = conn.execute("SELECT * FROM rooms WHERE id=?",(room_id,)).fetchone()
        access = conn.execute("SELECT * FROM room_access WHERE user_id=? AND room_id=? AND approved=1",(session["user_id"],room_id)).fetchone()
        if room["is_locked"] and not access and not user["is_admin"]:
            return render_template("request_access.html", room=room, user=user)
        rows = conn.execute("""
            SELECT m.id, m.user_id, m.content, m.image, m.reply_to,
                   m.created_at, m.is_system,
                   COALESCE(u.username, 'system') AS username,
                   u.avatar AS avatar
            FROM messages m
            LEFT JOIN users u ON u.id = m.user_id
            WHERE m.room_id=?
            ORDER BY m.id ASC LIMIT 200
        """,(room_id,)).fetchall()
    msgs=[]
    for r in rows:
        msgs.append(dict(r))
    return render_template("chat.html", user=user, messages=msgs, room=room)

@app.route("/request-room/<int:room_id>", methods=["POST"])
@login_required
def request_room(room_id):
    with db() as conn:
        existing=conn.execute("SELECT * FROM room_access WHERE user_id=? AND room_id=?",(session["user_id"],room_id)).fetchone()
        if not existing:
            conn.execute("INSERT INTO room_access(user_id,room_id,approved) VALUES (?,?,0)",(session["user_id"],room_id))
            conn.commit()
    return redirect(url_for("dashboard"))

@app.route("/chat")
@login_required
def chat():
    user = current_user()
    with db() as conn:
        rows = conn.execute("""
            SELECT m.id, m.user_id, m.content, m.image, m.reply_to,
                   m.created_at, m.is_system,
                   COALESCE(u.username, 'system') AS username,
                   u.avatar AS avatar
            FROM messages m
            LEFT JOIN users u ON u.id = m.user_id
            ORDER BY m.id ASC LIMIT 200
        """).fetchall()
    msgs = []
    for r in rows:
        msgs.append({
            "id": r["id"], "user_id": r["user_id"],
            "username": r["username"] if not r["is_system"] else (
                getattr(bot_plugin, "BOT_NAME", "dark_bot") if bot_plugin else "system"
            ),
            "avatar": r["avatar"], "content": r["content"], "image": r["image"],
            "reply_to": r["reply_to"], "created_at": r["created_at"],
            "is_system": bool(r["is_system"]),
        })
    return render_template("chat.html", user=user, messages=msgs)


@app.route("/upload_avatar", methods=["POST"])
@login_required
def upload_avatar():
    f = request.files.get("avatar")
    if not f or not allowed_file(f.filename):
        return redirect(url_for("dashboard"))
    ext = f.filename.rsplit(".", 1)[1].lower()
    fname = f"{session['user_id']}_{uuid.uuid4().hex}.{ext}"
    f.save(os.path.join(AVATAR_DIR, fname))
    with db() as conn:
        conn.execute("UPDATE users SET avatar=? WHERE id=?", (fname, session["user_id"]))
        conn.commit()
    return redirect(url_for("dashboard"))


@app.route("/upload_chat_image", methods=["POST"])
@login_required
def upload_chat_image():
    f = request.files.get("image")
    if not f or not allowed_file(f.filename):
        return jsonify({"error": "invalid file"}), 400
    ext = f.filename.rsplit(".", 1)[1].lower()
    fname = f"{uuid.uuid4().hex}.{ext}"
    f.save(os.path.join(CHAT_IMG_DIR, fname))
    return jsonify({"url": url_for("static", filename=f"chat_images/{fname}")})


# ---------- admin ----------
@app.route("/admin")
@admin_required
def admin():
    with db() as conn:
        users = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
    return render_template("admin.html", user=current_user(), users=users)


@app.route("/admin/ban/<int:uid>", methods=["POST"])
@admin_required
def ban(uid):
    with db() as conn:
        target = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
        if not target or target["username"] == ADMIN_USERNAME:
            return redirect(url_for("admin"))
        conn.execute("UPDATE users SET is_banned=1 WHERE id=?", (uid,))
        conn.commit()
    post_system_message(f"⛔ @{target['username']} has been banned from the group.")
    post_system_message(
        f"😡You have been banned from this group.😡",
        target_username=target["username"],
    )
    return redirect(url_for("admin"))


@app.route("/admin/unban/<int:uid>", methods=["POST"])
@admin_required
def unban(uid):
    with db() as conn:
        target = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
        conn.execute("UPDATE users SET is_banned=0 WHERE id=?", (uid,))
        conn.commit()
    if target:
        post_system_message(f"✅ @{target['username']} has been unbanned.")
    return redirect(url_for("admin"))


@app.route("/admin/promote/<int:uid>", methods=["POST"])
@admin_required
def promote(uid):
    with db() as conn:
        target = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
        conn.execute("UPDATE users SET is_admin=1 WHERE id=?", (uid,))
        conn.commit()
    if target:
        post_system_message(f"⭐ @{target['username']} is now an admin.")
    return redirect(url_for("admin"))


@app.route("/admin/demote/<int:uid>", methods=["POST"])
@admin_required
def demote(uid):
    with db() as conn:
        target = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not target:
            return redirect(url_for("admin"))
        if target["username"] == ADMIN_USERNAME:
            return redirect(url_for("admin"))
        if target["id"] == session.get("user_id"):
            return redirect(url_for("admin"))
        conn.execute("UPDATE users SET is_admin=0 WHERE id=?", (uid,))
        conn.commit()
    post_system_message(f"⬇ @{target['username']} is no longer an admin.")
    return redirect(url_for("admin"))


# ---------- socketio ----------
@socketio.on("send_message")
def on_send(data):
    if "user_id" not in session:
        return
    with db() as conn:
        u = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
        if not u or u["is_banned"]:
            return
        content = (data.get("content") or "").strip()
        image = data.get("image") or None
        reply_to = data.get("reply_to") or None
        if not content and not image:
            return
        if len(content) > 2000:
            content = content[:2000]
        mid = conn.insert_id(
            "INSERT INTO messages (user_id, content, image, reply_to, created_at) VALUES (?,?,?,?,?)",
            (u["id"], content, image, reply_to, int(time.time()))
        )
        conn.commit()
        username = u["username"]
        avatar = u["avatar"]
        uid = u["id"]

    payload = {
        "id": mid, "user_id": uid, "username": username,
        "avatar": avatar, "content": content, "image": image,
        "reply_to": reply_to, "created_at": int(time.time()),
        "is_system": False,
    }
    emit("new_message", payload, broadcast=True)

    if bot_plugin and content:
        try:
            reply = bot_plugin.handle_message(username, content)
        except Exception as e:
            print("bot error:", e)
            reply = None
        if reply:
            post_bot_message(str(reply))


@socketio.on("delete_message")
def on_delete(data):
    if "user_id" not in session:
        return
    mid = data.get("id")
    if not mid:
        return
    with db() as conn:
        u = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
        m = conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
        if not m or not u:
            return
        if m["user_id"] != u["id"] and not u["is_admin"]:
            return
        conn.execute("DELETE FROM messages WHERE id=?", (mid,))
        conn.commit()
    emit("message_deleted", {"id": mid}, broadcast=True)


if __name__ == "__main__":
    print("=" * 50)
    print(" DARK_GC server running")
    print(" Local:  http://localhost:5000")
    print("=" * 50)
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)
