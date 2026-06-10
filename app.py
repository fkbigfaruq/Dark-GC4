"""
DARK_GC — Flask + SocketIO chat with Rooms, locked rooms, request-to-join DM,
and admin moderation. Render-ready.

Database:
  - Set DATABASE_URL (Neon / Supabase / Render Postgres). REQUIRED in production.
  - Local dev (no DATABASE_URL): falls back to SQLite. To disable this safety net
    and force production behavior, set REQUIRE_DATABASE_URL=1.

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
import re
import json
import base64
import threading
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, jsonify, send_from_directory, abort, flash
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_socketio import SocketIO, emit, join_room, leave_room

# Web Push (real background notifications)
try:
    from pywebpush import webpush, WebPushException
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    WEBPUSH_OK = True
except Exception as _e:
    print("[push] pywebpush/cryptography not available:", _e)
    WEBPUSH_OK = False

# Auto-delete: keep messages only this many seconds (24h)
MESSAGE_TTL_SECONDS = int(os.environ.get("MESSAGE_TTL_SECONDS", 5 * 60 * 60))
CLEANUP_INTERVAL_SECONDS = int(os.environ.get("CLEANUP_INTERVAL_SECONDS", 60 * 60))  # hourly
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:admin@darkghc.app")

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
ALLOWED_VIDEO_EXT = {"mp4", "webm", "mov", "m4v", "ogg"}
VIDEO_TTL_SECONDS = int(os.environ.get("VIDEO_TTL_SECONDS", 3 * 60 * 60))   # 3 hours
MAX_VIDEO_BYTES = int(os.environ.get("MAX_VIDEO_BYTES", 25 * 1024 * 1024))  # 25 MB
ADMIN_USERNAME = "fkbigfaruq"
SYSTEM_USER_ID = 0

os.makedirs(AVATAR_DIR, exist_ok=True)
os.makedirs(CHAT_IMG_DIR, exist_ok=True)

# ---------- pick database backend ----------
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]
USE_PG = bool(DATABASE_URL)
ALLOW_SQLITE_DEV = os.environ.get("ALLOW_SQLITE_DEV", "").lower() in ("1", "true", "yes")

if not USE_PG and not ALLOW_SQLITE_DEV:
    raise SystemExit(
        "\n[FATAL] Permanent database is not connected.\n"
        "  DATABASE_URL is missing, so this server would use temporary SQLite storage.\n"
        "  That is what makes messages/users disappear after deploys or restarts.\n"
        "  Add your permanent PostgreSQL DATABASE_URL on Render and redeploy.\n"
        "  For local testing only, set ALLOW_SQLITE_DEV=1.\n"
    )

if USE_PG:
    import psycopg2
    import psycopg2.extras
    import psycopg2.errors
    INTEGRITY_ERRORS = (sqlite3.IntegrityError, psycopg2.IntegrityError)
    print("[db] Using PostgreSQL (permanent storage)")
else:
    INTEGRITY_ERRORS = (sqlite3.IntegrityError,)
    print(f"[db] Using SQLite at {DB_PATH} (LOCAL DEV ONLY — NOT permanent)")


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-me-please-dark-gc-secret")
app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024
socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")


@app.context_processor
def inject_globals():
    return {
        "ADMIN_USERNAME": ADMIN_USERNAME,
        "BOT_NAME": getattr(bot_plugin, "BOT_NAME", "DarkBot") if bot_plugin else "DarkBot",
    }


# ---------- db helpers ----------
class DB:
    def __init__(self):
        if USE_PG:
            # keepalives so Neon/Render don't drop us silently
            self._conn = psycopg2.connect(
                DATABASE_URL, sslmode="require",
                connect_timeout=10,
                keepalives=1, keepalives_idle=30,
                keepalives_interval=10, keepalives_count=3,
            )
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
        if USE_PG:
            cur = self._conn.cursor()
            cur.execute(sql.replace("?", "%s") + " RETURNING id", params)
            return cur.fetchone()[0]
        else:
            return self._conn.execute(sql, params).lastrowid

    def commit(self): self._conn.commit()
    def rollback(self):
        try: self._conn.rollback()
        except Exception: pass
    def close(self):
        try: self._conn.close()
        except Exception: pass
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None: self.rollback()
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
            conn.execute("""CREATE TABLE IF NOT EXISTS rooms (
                id SERIAL PRIMARY KEY,
                slug TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                is_locked INTEGER DEFAULT 0,
                created_at BIGINT
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                room_id INTEGER NOT NULL DEFAULT 1,
                user_id INTEGER NOT NULL,
                content TEXT,
                image TEXT,
                reply_to INTEGER,
                created_at BIGINT,
                is_system INTEGER DEFAULT 0
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS room_access (
                id SERIAL PRIMARY KEY,
                room_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at BIGINT,
                UNIQUE(room_id, user_id)
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS dm_messages (
                id SERIAL PRIMARY KEY,
                from_user_id INTEGER NOT NULL,
                to_user_id INTEGER NOT NULL,
                room_id INTEGER,
                content TEXT NOT NULL,
                created_at BIGINT,
                is_read INTEGER DEFAULT 0,
                is_bot INTEGER DEFAULT 0
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS room_reads (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                room_id INTEGER NOT NULL,
                last_message_id INTEGER NOT NULL DEFAULT 0,
                updated_at BIGINT,
                UNIQUE(user_id, room_id)
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS bot_sessions (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                admin_id INTEGER NOT NULL,
                room_id INTEGER,
                state_json TEXT DEFAULT '{}',
                updated_at BIGINT,
                UNIQUE(user_id, admin_id, room_id)
            )""")
            conn.commit()
            for sql in (
                "ALTER TABLE messages ADD COLUMN IF NOT EXISTS room_id INTEGER NOT NULL DEFAULT 1",
                "ALTER TABLE messages ADD COLUMN IF NOT EXISTS is_system INTEGER DEFAULT 0",
                "ALTER TABLE dm_messages ADD COLUMN IF NOT EXISTS room_id INTEGER",
                "ALTER TABLE dm_messages ADD COLUMN IF NOT EXISTS is_read INTEGER DEFAULT 0",
                "ALTER TABLE dm_messages ADD COLUMN IF NOT EXISTS is_bot INTEGER DEFAULT 0",
                "ALTER TABLE dm_messages ADD COLUMN IF NOT EXISTS image TEXT",
                "ALTER TABLE dm_messages ALTER COLUMN content DROP NOT NULL",
            ):
                try:
                    conn.execute(sql)
                except Exception:
                    conn.rollback()
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
            conn.execute("""CREATE TABLE IF NOT EXISTS rooms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                is_locked INTEGER DEFAULT 0,
                created_at INTEGER
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id INTEGER NOT NULL DEFAULT 1,
                user_id INTEGER NOT NULL,
                content TEXT,
                image TEXT,
                reply_to INTEGER,
                created_at INTEGER,
                is_system INTEGER DEFAULT 0
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS room_access (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at INTEGER,
                UNIQUE(room_id, user_id)
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS dm_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_user_id INTEGER NOT NULL,
                to_user_id INTEGER NOT NULL,
                room_id INTEGER,
                content TEXT NOT NULL,
                created_at INTEGER,
                is_read INTEGER DEFAULT 0,
                is_bot INTEGER DEFAULT 0
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS room_reads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                room_id INTEGER NOT NULL,
                last_message_id INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER,
                UNIQUE(user_id, room_id)
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS bot_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                admin_id INTEGER NOT NULL,
                room_id INTEGER,
                state_json TEXT DEFAULT '{}',
                updated_at INTEGER,
                UNIQUE(user_id, admin_id, room_id)
            )""")
            conn.commit()
            for sql in (
                "ALTER TABLE messages ADD COLUMN room_id INTEGER NOT NULL DEFAULT 1",
                "ALTER TABLE messages ADD COLUMN is_system INTEGER DEFAULT 0",
                "ALTER TABLE dm_messages ADD COLUMN room_id INTEGER",
                "ALTER TABLE dm_messages ADD COLUMN is_read INTEGER DEFAULT 0",
                "ALTER TABLE dm_messages ADD COLUMN is_bot INTEGER DEFAULT 0",
                "ALTER TABLE dm_messages ADD COLUMN image TEXT",
            ):
                try:
                    conn.execute(sql)
                except sqlite3.OperationalError:
                    pass
        conn.commit()

        # push subscriptions + app config (for VAPID keys)
        if USE_PG:
            conn.execute("""CREATE TABLE IF NOT EXISTS push_subscriptions (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                endpoint TEXT UNIQUE NOT NULL,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                created_at BIGINT
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS app_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )""")
        else:
            conn.execute("""CREATE TABLE IF NOT EXISTS push_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                endpoint TEXT UNIQUE NOT NULL,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                created_at INTEGER
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS app_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )""")
        conn.commit()


        existing = conn.execute("SELECT slug FROM rooms").fetchall()
        slugs = {r["slug"] for r in existing}
        ts = int(time.time())
        defaults = [
            ("main",    "Main",    "Open chat for everyone.",       0),
            ("hackers", "Hackers", "Locked room. Request access.",  1),
            ("coding",  "Coding",  "Locked room. Request access.",  1),
        ]
        for slug, name, desc, lock in defaults:
            if slug not in slugs:
                conn.execute(
                    "INSERT INTO rooms (slug, name, description, is_locked, created_at) VALUES (?,?,?,?,?)",
                    (slug, name, desc, lock, ts)
                )
        conn.commit()


init_db()


# ---------- VAPID / Web Push ----------
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _get_config(key):
    with db() as conn:
        r = conn.execute("SELECT value FROM app_config WHERE key=?", (key,)).fetchone()
    return r["value"] if r else None


def _set_config(key, value):
    with db() as conn:
        existing = conn.execute("SELECT key FROM app_config WHERE key=?", (key,)).fetchone()
        if existing:
            conn.execute("UPDATE app_config SET value=? WHERE key=?", (value, key))
        else:
            conn.execute("INSERT INTO app_config (key, value) VALUES (?,?)", (key, value))
        conn.commit()


def _ensure_vapid_keys():
    """Generate VAPID keys once and persist them in app_config."""
    if not WEBPUSH_OK:
        return None, None
    pub = _get_config("vapid_public_key")
    priv = _get_config("vapid_private_key")
    if pub and priv:
        return pub, priv
    # generate new P-256 keypair
    private_key = ec.generate_private_key(ec.SECP256R1())
    priv_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_numbers = private_key.public_key().public_numbers()
    # uncompressed point: 0x04 || X(32) || Y(32)
    pub_raw = b"\x04" + public_numbers.x.to_bytes(32, "big") + public_numbers.y.to_bytes(32, "big")
    pub_b64 = _b64url(pub_raw)
    _set_config("vapid_public_key", pub_b64)
    _set_config("vapid_private_key", priv_pem)
    print("[push] generated new VAPID keypair")
    return pub_b64, priv_pem


VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY = _ensure_vapid_keys()


def send_web_push(user_id, title, body, url="/"):
    """Send a real OS-level push to every device that subscribed for this user."""
    if not WEBPUSH_OK or not VAPID_PRIVATE_KEY:
        return
    payload = json.dumps({"title": title, "body": body, "url": url})
    dead = []
    with db() as conn:
        subs = conn.execute(
            "SELECT id, endpoint, p256dh, auth FROM push_subscriptions WHERE user_id=?",
            (user_id,)
        ).fetchall()
    for s in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": s["endpoint"],
                    "keys": {"p256dh": s["p256dh"], "auth": s["auth"]},
                },
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_SUBJECT},
                ttl=60,
            )
        except WebPushException as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (404, 410):
                dead.append(s["id"])
            else:
                print("[push] webpush error:", e)
        except Exception as e:
            print("[push] unexpected error:", e)
    if dead:
        with db() as conn:
            for sid in dead:
                conn.execute("DELETE FROM push_subscriptions WHERE id=?", (sid,))
            conn.commit()


# ---------- Auto-delete old messages (every room) ----------
def cleanup_old_messages():
    now = int(time.time())
    cutoff = now - MESSAGE_TTL_SECONDS
    try:
        with db() as conn:
            conn.execute("DELETE FROM messages WHERE created_at < ?", (cutoff,))
            conn.execute("DELETE FROM dm_messages WHERE created_at < ?", (cutoff,))
            # videos expire on their own schedule (default 3h)
            try:
                conn.execute("DELETE FROM video_comments WHERE video_id IN (SELECT id FROM videos WHERE expires_at < ?)", (now,))
                conn.execute("DELETE FROM video_reactions WHERE video_id IN (SELECT id FROM videos WHERE expires_at < ?)", (now,))
                conn.execute("DELETE FROM videos WHERE expires_at < ?", (now,))
            except Exception:
                pass
            conn.commit()
        print(f"[cleanup] deleted messages older than {MESSAGE_TTL_SECONDS}s and expired videos")
    except Exception as e:
        print("[cleanup] error:", e)


def _cleanup_loop():
    # run once shortly after boot, then every interval
    time.sleep(10)
    while True:
        cleanup_old_messages()
        time.sleep(CLEANUP_INTERVAL_SECONDS)


threading.Thread(target=_cleanup_loop, name="darkgc-cleanup", daemon=True).start()




def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT


def slugify(s):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (s or "").strip().lower()).strip("-")
    return s[:40] or f"room-{int(time.time())}"


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


def get_admin_id():
    with db() as conn:
        r = conn.execute("SELECT id FROM users WHERE username=?", (ADMIN_USERNAME,)).fetchone()
    return r["id"] if r else None


def user_can_enter(user, room):
    if not user or not room: return False
    if user["is_admin"]: return True
    if not room["is_locked"]: return True
    with db() as conn:
        r = conn.execute(
            "SELECT status FROM room_access WHERE room_id=? AND user_id=?",
            (room["id"], user["id"])
        ).fetchone()
    return bool(r and r["status"] == "approved")


# ---------- unread / notification helpers ----------
def max_message_id(conn, room_id):
    r = conn.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM messages WHERE room_id=?", (room_id,)).fetchone()
    return int(r["max_id"] or 0)


def mark_room_read(user_id, room_id):
    ts = int(time.time())
    with db() as conn:
        last_id = max_message_id(conn, room_id)
        existing = conn.execute(
            "SELECT id FROM room_reads WHERE user_id=? AND room_id=?",
            (user_id, room_id)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE room_reads SET last_message_id=?, updated_at=? WHERE id=?",
                (last_id, ts, existing["id"])
            )
        else:
            conn.execute(
                "INSERT INTO room_reads (user_id, room_id, last_message_id, updated_at) VALUES (?,?,?,?)",
                (user_id, room_id, last_id, ts)
            )
        conn.commit()
    return last_id


def unread_count_for(conn, user_id, room_id):
    read = conn.execute(
        "SELECT last_message_id FROM room_reads WHERE user_id=? AND room_id=?",
        (user_id, room_id)
    ).fetchone()
    last_id = int(read["last_message_id"] or 0) if read else 0
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM messages WHERE room_id=? AND id>? AND user_id<>?",
        (room_id, last_id, user_id)
    ).fetchone()
    return int(row["c"] or 0)


def accessible_user_ids_for_room(conn, room):
    if not room:
        return []
    if room["is_locked"]:
        rows = conn.execute("""
            SELECT id FROM users WHERE is_admin=1 AND is_banned=0
            UNION
            SELECT u.id
            FROM room_access ra
            JOIN users u ON u.id=ra.user_id
            WHERE ra.room_id=? AND ra.status='approved' AND u.is_banned=0
        """, (room["id"],)).fetchall()
    else:
        rows = conn.execute("SELECT id FROM users WHERE is_banned=0").fetchall()
    return [r["id"] for r in rows]


def notify_room_unread(room_id, sender_id, sender_name, content, image=None):
    with db() as conn:
        room = conn.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()
        targets = accessible_user_ids_for_room(conn, room)
        room_name = room["name"] if room else "room"
        for target_id in targets:
            if target_id == sender_id:
                continue
            target_url = url_for("chat", slug=room["slug"]) if room else "/rooms"
            socketio.emit("room_unread", {
                "room_id": room_id,
                "room_name": room_name,
                "from": sender_name,
                "text": content or ("sent an image" if image else "new message"),
                "count": unread_count_for(conn, target_id, room_id),
                "url": target_url,
            }, to=f"user:{target_id}")
            # real OS push (works when app is closed)
            send_web_push(
                target_id,
                f"{sender_name} in {room_name}",
                (content[:120] if content else "sent an image"),
                target_url,
            )


def emit_dm_unread(conn, from_id, to_id, content, room_id=None, is_bot=0):
    sender = conn.execute("SELECT username FROM users WHERE id=?", (from_id,)).fetchone()
    room = conn.execute("SELECT name FROM rooms WHERE id=?", (room_id,)).fetchone() if room_id else None
    unread = conn.execute(
        "SELECT COUNT(*) AS c FROM dm_messages WHERE to_user_id=? AND is_read=0",
        (to_id,)
    ).fetchone()
    sender_name = sender["username"] if sender else "admin"
    room_name = room["name"] if room else None
    socketio.emit("dm_unread", {
        "from_user_id": from_id,
        "from": sender_name,
        "text": content,
        "room_id": room_id,
        "room_name": room_name,
        "is_bot": bool(is_bot),
        "count": int(unread["c"] or 0),
        "url": "/messages",
    }, to=f"user:{to_id}")
    # real OS push (works when app is closed)
    title = f"Message from {sender_name}" + (f" ({room_name})" if room_name else "")
    send_web_push(to_id, title, (content or "")[:120], "/messages")


def load_bot_state(conn, user_id, admin_id, room_id):
    row = conn.execute(
        "SELECT state_json FROM bot_sessions WHERE user_id=? AND admin_id=? AND COALESCE(room_id,0)=COALESCE(?,0)",
        (user_id, admin_id, room_id)
    ).fetchone()
    if not row:
        return {}
    try:
        return json.loads(row["state_json"] or "{}")
    except Exception:
        return {}


def save_bot_state(conn, user_id, admin_id, room_id, state):
    ts = int(time.time())
    state_json = json.dumps(state or {})[:4000]
    row = conn.execute(
        "SELECT id FROM bot_sessions WHERE user_id=? AND admin_id=? AND COALESCE(room_id,0)=COALESCE(?,0)",
        (user_id, admin_id, room_id)
    ).fetchone()
    if row:
        conn.execute("UPDATE bot_sessions SET state_json=?, updated_at=? WHERE id=?", (state_json, ts, row["id"]))
    else:
        conn.execute(
            "INSERT INTO bot_sessions (user_id, admin_id, room_id, state_json, updated_at) VALUES (?,?,?,?,?)",
            (user_id, admin_id, room_id, state_json, ts)
        )


def admin_last_seen_ts(conn, admin_id):
    row = conn.execute("SELECT COALESCE(MAX(created_at), 0) AS last_seen FROM dm_messages WHERE from_user_id=? AND is_bot=0", (admin_id,)).fetchone()
    return float(row["last_seen"] or 0)


# ---------- system / bot helpers ----------
def post_system_message(text, room_id=1, target_username=None):
    ts = int(time.time())
    with db() as conn:
        mid = conn.insert_id(
            "INSERT INTO messages (room_id, user_id, content, image, reply_to, created_at, is_system) "
            "VALUES (?,?,?,?,?,?,1)",
            (room_id, SYSTEM_USER_ID, text, None, None, ts)
        )
        conn.commit()
    payload = {
        "id": mid, "user_id": SYSTEM_USER_ID,
        "username": "system", "avatar": None,
        "content": text, "image": None, "reply_to": None,
        "created_at": ts, "is_system": True,
        "target_username": target_username,
        "room_id": room_id,
    }
    socketio.emit("new_message", payload, to=f"room:{room_id}")


def post_bot_message(text, room_id=1):
    ts = int(time.time())
    with db() as conn:
        mid = conn.insert_id(
            "INSERT INTO messages (room_id, user_id, content, image, reply_to, created_at, is_system) "
            "VALUES (?,?,?,?,?,?,1)",
            (room_id, SYSTEM_USER_ID, text, None, None, ts)
        )
        conn.commit()
    bot_name = getattr(bot_plugin, "BOT_NAME", "bot") if bot_plugin else "bot"
    payload = {
        "id": mid, "user_id": SYSTEM_USER_ID,
        "username": bot_name, "avatar": None,
        "content": text, "image": None, "reply_to": None,
        "created_at": ts, "is_system": True,
        "target_username": None,
        "room_id": room_id,
    }
    socketio.emit("new_message", payload, to=f"room:{room_id}")


# ---------- routes ----------
@app.route("/")
def index():
    return render_template("index.html", user=current_user())


@app.route("/healthz")
def healthz():
    backend = "postgres" if USE_PG else "sqlite"
    try:
        with db() as conn:
            conn.execute("SELECT 1").fetchone()
        return jsonify({"ok": True, "db": backend, "permanent": USE_PG})
    except Exception as e:
        return jsonify({"ok": False, "db": backend, "error": str(e)}), 500


@app.route("/signup", methods=["GET", "POST"])
def signup():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or not password:
            error = "Username and password required"
        elif len(username) < 4:
            error = "Username too short (min 3)"
        elif len(password) < 6:
            error = "Password too short (min 4)"
        elif len(username) > 6:
            error = "please your username should not exit 6 chars"
        elif len(password) > 15:
            error = "🛑 your password should not exit 15 chars"
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
                return redirect(url_for("rooms"))
        else:
            error = "Invalid credentials"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# ---------- ROOMS ----------
@app.route("/rooms")
@login_required
def rooms():
    user = current_user()
    with db() as conn:
        rs = conn.execute("SELECT * FROM rooms ORDER BY id ASC").fetchall()
        access = conn.execute(
            "SELECT room_id, status FROM room_access WHERE user_id=?",
            (user["id"],)
        ).fetchall()
        dm_unread = conn.execute(
            "SELECT COUNT(*) AS c FROM dm_messages WHERE to_user_id=? AND is_read=0",
            (user["id"],)
        ).fetchone()
        access_map = {a["room_id"]: a["status"] for a in access}
        rooms_view = []
        for r in rs:
            st = "approved" if (not r["is_locked"] or user["is_admin"]) else access_map.get(r["id"], "none")
            rooms_view.append({
                "id": r["id"], "slug": r["slug"], "name": r["name"],
                "description": r["description"], "is_locked": bool(r["is_locked"]),
                "status": st,
                "unread": unread_count_for(conn, user["id"], r["id"]) if st == "approved" else 0,
            })
    return render_template("rooms.html", user=user, rooms=rooms_view, dm_unread=int(dm_unread["c"] or 0))


@app.route("/chat")
@login_required
def chat_root():
    return redirect(url_for("rooms"))


@app.route("/chat/<slug>")
@login_required
def chat(slug):
    user = current_user()
    with db() as conn:
        room = conn.execute("SELECT * FROM rooms WHERE slug=?", (slug,)).fetchone()
    if not room:
        abort(404)
    if not user_can_enter(user, room):
        return redirect(url_for("rooms"))
    with db() as conn:
        rows = conn.execute("""
            SELECT m.id, m.user_id, m.content, m.image, m.reply_to,
                   m.created_at, m.is_system, m.room_id,
                   COALESCE(u.username, 'system') AS username,
                   u.avatar AS avatar,
                   COALESCE(u.is_admin, 0) AS is_admin
            FROM messages m
            LEFT JOIN users u ON u.id = m.user_id
            WHERE m.room_id = ?
            ORDER BY m.id ASC LIMIT 300
        """, (room["id"],)).fetchall()
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
            "is_admin": bool(r["is_admin"]),
        })
    mark_room_read(user["id"], room["id"])
    return render_template("chat.html", user=user, messages=msgs, room=room)


@app.route("/rooms/<int:room_id>/request", methods=["POST"])
@login_required
def request_access(room_id):
    user = current_user()
    message = (request.form.get("message") or "").strip()[:1000]
    if not message:
        return redirect(url_for("rooms"))
    admin_id = get_admin_id()
    ts = int(time.time())
    with db() as conn:
        room = conn.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()
        if not room or not room["is_locked"]:
            return redirect(url_for("rooms"))
        existing = conn.execute(
            "SELECT id, status FROM room_access WHERE room_id=? AND user_id=?",
            (room_id, user["id"])
        ).fetchone()
        if existing and existing["status"] == "approved":
            return redirect(url_for("chat", slug=room["slug"]))
        if not existing:
            conn.execute(
                "INSERT INTO room_access (room_id, user_id, status, created_at) VALUES (?,?,?,?)",
                (room_id, user["id"], "pending", ts)
            )
        else:
            conn.execute(
                "UPDATE room_access SET status='pending', created_at=? WHERE id=?",
                (ts, existing["id"])
            )
        if admin_id:
            conn.execute(
                "INSERT INTO dm_messages (from_user_id, to_user_id, room_id, content, created_at, is_bot) "
                "VALUES (?,?,?,?,?,0)",
                (user["id"], admin_id, room_id, message, ts)
            )
        conn.commit()
        if admin_id:
            emit_dm_unread(conn, user["id"], admin_id, message, room_id)
    return redirect(url_for("messages_with", username=ADMIN_USERNAME))


# ---------- DMs ----------
@app.route("/messages")
@login_required
def messages_inbox():
    user = current_user()
    with db() as conn:
        rows = conn.execute("""
            SELECT u.id, u.username, u.avatar,
                   MAX(d.created_at) AS last_at,
                   SUM(CASE WHEN d.to_user_id=? AND d.is_read=0 THEN 1 ELSE 0 END) AS unread_count
            FROM dm_messages d
            JOIN users u ON u.id = CASE WHEN d.from_user_id=? THEN d.to_user_id ELSE d.from_user_id END
            WHERE d.from_user_id=? OR d.to_user_id=?
            GROUP BY u.id, u.username, u.avatar
            ORDER BY last_at DESC
        """, (user["id"], user["id"], user["id"], user["id"])).fetchall()
    return render_template("dm_inbox.html", user=user, threads=rows)


@app.route("/messages/<username>", methods=["GET", "POST"])
@login_required
def messages_with(username):
    user = current_user()
    with db() as conn:
        other = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if not other:
            abort(404)
        if request.method == "POST":
            content = (request.form.get("content") or "").strip()[:2000]
            # optional image attachment (e.g. payment receipt)
            image_url = None
            f = request.files.get("image")
            if f and f.filename and allowed_file(f.filename):
                ext = f.filename.rsplit(".", 1)[1].lower()
                fname = f"dm_{uuid.uuid4().hex}.{ext}"
                f.save(os.path.join(CHAT_IMG_DIR, fname))
                image_url = url_for("static", filename=f"chat_images/{fname}")
            if content or image_url:
                ts = int(time.time())
                room_row = conn.execute("""
                    SELECT d.room_id, r.name AS room_name
                    FROM dm_messages d
                    LEFT JOIN rooms r ON r.id=d.room_id
                    WHERE d.room_id IS NOT NULL AND ((d.from_user_id=? AND d.to_user_id=?) OR (d.from_user_id=? AND d.to_user_id=?))
                    ORDER BY d.id DESC LIMIT 1
                """, (user["id"], other["id"], other["id"], user["id"])).fetchone()
                room_id = room_row["room_id"] if room_row else None
                room_name = room_row["room_name"] if room_row else "any room"
                conn.execute(
                    "INSERT INTO dm_messages (from_user_id, to_user_id, room_id, content, image, created_at, is_bot) "
                    "VALUES (?,?,?,?,?,?,0)",
                    (user["id"], other["id"], room_id, content or "", image_url, ts)
                )
                conn.commit()
                emit_dm_unread(conn, user["id"], other["id"], content or ("[image]" if image_url else ""), room_id)

                if content and bot_plugin and other["username"] == ADMIN_USERNAME and not user["is_admin"] and hasattr(bot_plugin, "maybe_bot_reply"):
                    try:
                        state = load_bot_state(conn, user["id"], other["id"], room_id)
                        reply, new_state = bot_plugin.maybe_bot_reply(
                            user_msg=content,
                            room_name=room_name or "any room",
                            admin_last_seen_ts=admin_last_seen_ts(conn, other["id"]),
                            session_state=state,
                        )
                        save_bot_state(conn, user["id"], other["id"], room_id, new_state)
                        conn.commit()
                        if reply:
                            conn.execute(
                                "INSERT INTO dm_messages (from_user_id, to_user_id, room_id, content, created_at, is_bot) "
                                "VALUES (?,?,?,?,?,1)",
                                (other["id"], user["id"], room_id, str(reply)[:2000], int(time.time()))
                            )
                            conn.commit()
                            emit_dm_unread(conn, other["id"], user["id"], str(reply), room_id, is_bot=1)
                    except Exception as e:
                        print("dm bot error:", e)
            return redirect(url_for("messages_with", username=username))
        rows = conn.execute("""
            SELECT d.*, u.username AS from_name, COALESCE(u.is_admin, 0) AS from_is_admin
            FROM dm_messages d
            JOIN users u ON u.id = d.from_user_id
            WHERE (d.from_user_id=? AND d.to_user_id=?)
               OR (d.from_user_id=? AND d.to_user_id=?)
            ORDER BY d.id ASC
        """, (user["id"], other["id"], other["id"], user["id"])).fetchall()
        conn.execute(
            "UPDATE dm_messages SET is_read=1 WHERE to_user_id=? AND from_user_id=?",
            (user["id"], other["id"])
        )
        conn.commit()
    return render_template("dm.html", user=user, other=other, msgs=rows)


# ---------- notification APIs ----------
@app.route("/api/rooms/<int:room_id>/read", methods=["POST"])
@login_required
def api_mark_room_read(room_id):
    user = current_user()
    with db() as conn:
        room = conn.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()
    if not room or not user_can_enter(user, room):
        return jsonify({"ok": False}), 403
    last_id = mark_room_read(user["id"], room_id)
    return jsonify({"ok": True, "last_message_id": last_id})


@app.route("/api/unread_counts")
@login_required
def api_unread_counts():
    user = current_user()
    counts = {}
    with db() as conn:
        rooms_rows = conn.execute("SELECT * FROM rooms ORDER BY id ASC").fetchall()
        for room in rooms_rows:
            if user_can_enter(user, room):
                counts[str(room["id"])] = unread_count_for(conn, user["id"], room["id"])
        dm_unread = conn.execute(
            "SELECT COUNT(*) AS c FROM dm_messages WHERE to_user_id=? AND is_read=0",
            (user["id"],)
        ).fetchone()
    return jsonify({"ok": True, "rooms": counts, "dms": int(dm_unread["c"] or 0)})


@app.get('/bot/status')
def bot_status():
    return jsonify({
        "enabled": bool(getattr(bot_plugin, "BOT_ENABLED", False)) if bot_plugin else False,
        "name": getattr(bot_plugin, "BOT_NAME", "DarkBot") if bot_plugin else None,
        "default_room_price": getattr(bot_plugin, "DEFAULT_ROOM_PRICE", 500) if bot_plugin else 500,
    })


# ---------- push subscription APIs ----------
@app.get("/api/push/vapid_public_key")
def api_vapid_public_key():
    return jsonify({"key": VAPID_PUBLIC_KEY or "", "enabled": bool(VAPID_PUBLIC_KEY)})


@app.post("/api/push/subscribe")
@login_required
def api_push_subscribe():
    data = request.get_json(silent=True) or {}
    endpoint = (data.get("endpoint") or "").strip()
    keys = data.get("keys") or {}
    p256dh = (keys.get("p256dh") or "").strip()
    auth_k = (keys.get("auth") or "").strip()
    if not endpoint or not p256dh or not auth_k:
        return jsonify({"ok": False, "error": "invalid subscription"}), 400
    ts = int(time.time())
    with db() as conn:
        existing = conn.execute("SELECT id FROM push_subscriptions WHERE endpoint=?", (endpoint,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE push_subscriptions SET user_id=?, p256dh=?, auth=? WHERE id=?",
                (session["user_id"], p256dh, auth_k, existing["id"])
            )
        else:
            conn.execute(
                "INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, created_at) VALUES (?,?,?,?,?)",
                (session["user_id"], endpoint, p256dh, auth_k, ts)
            )
        conn.commit()
    return jsonify({"ok": True})


@app.post("/api/push/unsubscribe")
@login_required
def api_push_unsubscribe():
    data = request.get_json(silent=True) or {}
    endpoint = (data.get("endpoint") or "").strip()
    if not endpoint:
        return jsonify({"ok": False}), 400
    with db() as conn:
        conn.execute("DELETE FROM push_subscriptions WHERE endpoint=? AND user_id=?",
                     (endpoint, session["user_id"]))
        conn.commit()
    return jsonify({"ok": True})


@app.post("/api/push/test")
@login_required
def api_push_test():
    send_web_push(session["user_id"], "Dark GHC website",
                  "Test notification — push is working ✅", "/rooms")
    return jsonify({"ok": True})





# ---------- uploads ----------
@app.route("/upload_avatar", methods=["POST"])
@login_required
def upload_avatar():
    f = request.files.get("avatar")
    if not f or not allowed_file(f.filename):
        return redirect(url_for("rooms"))
    ext = f.filename.rsplit(".", 1)[1].lower()
    fname = f"{session['user_id']}_{uuid.uuid4().hex}.{ext}"
    f.save(os.path.join(AVATAR_DIR, fname))
    with db() as conn:
        conn.execute("UPDATE users SET avatar=? WHERE id=?", (fname, session["user_id"]))
        conn.commit()
    return redirect(request.referrer or url_for("rooms"))


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
        rooms_list = conn.execute("SELECT * FROM rooms ORDER BY id").fetchall()
        pending = conn.execute("""
            SELECT ra.id, ra.room_id, ra.user_id, ra.created_at,
                   u.username, u.avatar, r.name AS room_name, r.slug AS room_slug
            FROM room_access ra
            JOIN users u ON u.id = ra.user_id
            JOIN rooms r ON r.id = ra.room_id
            WHERE ra.status='pending'
            ORDER BY ra.created_at DESC
        """).fetchall()
        videos_list = []
        try:
            videos_list = conn.execute("""
                SELECT v.id, v.title, v.mime, v.size_bytes, v.created_at, v.expires_at,
                       u.username AS uploader
                FROM videos v LEFT JOIN users u ON u.id=v.uploader_id
                ORDER BY v.id DESC
            """).fetchall()
        except Exception:
            pass
    online_list = list_online_users()
    return render_template(
        "admin.html",
        user=current_user(), users=users,
        rooms=rooms_list, pending=pending,
        videos=videos_list, online=online_list,
    )


@app.route("/admin/rooms/add", methods=["POST"])
@admin_required
def admin_add_room():
    name = (request.form.get("name") or "").strip()[:60]
    desc = (request.form.get("description") or "").strip()[:200]
    is_locked = 1 if request.form.get("is_locked") else 0
    if not name:
        return redirect(url_for("admin"))
    slug = slugify(name)
    ts = int(time.time())
    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO rooms (slug, name, description, is_locked, created_at) VALUES (?,?,?,?,?)",
                (slug, name, desc, is_locked, ts)
            )
            conn.commit()
    except INTEGRITY_ERRORS:
        pass
    return redirect(url_for("admin"))


@app.route("/admin/rooms/<int:room_id>/delete", methods=["POST"])
@admin_required
def admin_delete_room(room_id):
    if room_id == 1:
        return redirect(url_for("admin"))  # never delete Main
    with db() as conn:
        conn.execute("DELETE FROM messages WHERE room_id=?", (room_id,))
        conn.execute("DELETE FROM room_access WHERE room_id=?", (room_id,))
        conn.execute("DELETE FROM rooms WHERE id=?", (room_id,))
        conn.commit()
    return redirect(url_for("admin"))


@app.route("/admin/access/<int:access_id>/approve", methods=["POST"])
@admin_required
def admin_approve(access_id):
    with db() as conn:
        row = conn.execute("""
            SELECT ra.*, u.username, r.name AS room_name, r.slug AS room_slug
            FROM room_access ra
            JOIN users u ON u.id=ra.user_id
            JOIN rooms r ON r.id=ra.room_id
            WHERE ra.id=?
        """, (access_id,)).fetchone()
        if not row: return redirect(url_for("admin"))
        conn.execute("UPDATE room_access SET status='approved' WHERE id=?", (access_id,))
        # DM the user
        conn.execute(
            "INSERT INTO dm_messages (from_user_id, to_user_id, room_id, content, created_at) VALUES (?,?,?,?,?)",
            (session["user_id"], row["user_id"], row["room_id"],
             f"✅ Access approved for room: {row['room_name']}", int(time.time()))
        )
        conn.commit()
    return redirect(url_for("admin"))


@app.route("/admin/access/<int:access_id>/deny", methods=["POST"])
@admin_required
def admin_deny(access_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM room_access WHERE id=?", (access_id,)).fetchone()
        if not row: return redirect(url_for("admin"))
        conn.execute("UPDATE room_access SET status='denied' WHERE id=?", (access_id,))
        conn.execute(
            "INSERT INTO dm_messages (from_user_id, to_user_id, room_id, content, created_at) VALUES (?,?,?,?,?)",
            (session["user_id"], row["user_id"], row["room_id"],
             "❌ Access denied.", int(time.time()))
        )
        conn.commit()
    return redirect(url_for("admin"))


@app.route("/admin/ban/<int:uid>", methods=["POST"])
@admin_required
def ban(uid):
    with db() as conn:
        target = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
        if not target or target["username"] == ADMIN_USERNAME:
            return redirect(url_for("admin"))
        conn.execute("UPDATE users SET is_banned=1 WHERE id=?", (uid,))
        conn.commit()
    post_system_message(f"⛔ @{target['username']} has been banned.")
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
        if not target: return redirect(url_for("admin"))
        if target["username"] == ADMIN_USERNAME: return redirect(url_for("admin"))
        if target["id"] == session.get("user_id"): return redirect(url_for("admin"))
        conn.execute("UPDATE users SET is_admin=0 WHERE id=?", (uid,))
        conn.commit()
    post_system_message(f"⬇ @{target['username']} is no longer an admin.")
    return redirect(url_for("admin"))


# ---------- socketio ----------
@socketio.on("connect")
def on_connect():
    if "user_id" in session:
        join_room(f"user:{session['user_id']}")


@socketio.on("join_room")
def on_join(data):
    if "user_id" not in session: return
    rid = int(data.get("room_id") or 0)
    if not rid: return
    with db() as conn:
        u = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
        r = conn.execute("SELECT * FROM rooms WHERE id=?", (rid,)).fetchone()
    if not u or not r: return
    if not user_can_enter(u, r): return
    join_room(f"room:{rid}")


@socketio.on("leave_room")
def on_leave(data):
    rid = int(data.get("room_id") or 0)
    if rid: leave_room(f"room:{rid}")


@socketio.on("send_message")
def on_send(data):
    if "user_id" not in session: return
    rid = int(data.get("room_id") or 1)
    with db() as conn:
        u = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
        r = conn.execute("SELECT * FROM rooms WHERE id=?", (rid,)).fetchone()
        if not u or u["is_banned"] or not r: return
        if not user_can_enter(u, r): return
        content = (data.get("content") or "").strip()
        image = data.get("image") or None
        reply_to = data.get("reply_to") or None
        if not content and not image: return
        if len(content) > 2000: content = content[:2000]

        # ---- v2.0 link / code guard (non-admin only) ----
        if content and not u["is_admin"]:
            verdict = inspect_user_text(content)
            if verdict == "link":
                socketio.emit("new_message", {
                    "id": 0, "user_id": SYSTEM_USER_ID,
                    "username": getattr(bot_plugin, "BOT_NAME", "DarkBot") if bot_plugin else "DarkBot",
                    "avatar": None, "content": f"⚠️ @{u['username']} only admins can send links here. Your message was blocked.",
                    "image": None, "reply_to": None, "created_at": int(time.time()),
                    "is_system": True, "target_username": u["username"], "room_id": rid,
                }, to=f"room:{rid}")
                return
            if verdict == "rawcode":
                socketio.emit("new_message", {
                    "id": 0, "user_id": SYSTEM_USER_ID,
                    "username": getattr(bot_plugin, "BOT_NAME", "DarkBot") if bot_plugin else "DarkBot",
                    "avatar": None,
                    "content": ("⚠️ Raw code is not allowed. Wrap it in triple quotes like:\n"
                                "\"\"\"\nyour code here\n\"\"\""),
                    "image": None, "reply_to": None, "created_at": int(time.time()),
                    "is_system": True, "target_username": u["username"], "room_id": rid,
                }, to=f"room:{rid}")
                return
        mid = conn.insert_id(
            "INSERT INTO messages (room_id, user_id, content, image, reply_to, created_at) VALUES (?,?,?,?,?,?)",
            (rid, u["id"], content, image, reply_to, int(time.time()))
        )
        conn.commit()
        username = u["username"]; avatar = u["avatar"]; uid = u["id"]; is_admin = bool(u["is_admin"])

    payload = {
        "id": mid, "user_id": uid, "username": username,
        "avatar": avatar, "content": content, "image": image,
        "reply_to": reply_to, "created_at": int(time.time()),
        "is_system": False, "room_id": rid,
        "is_admin": is_admin,
    }
    emit("new_message", payload, to=f"room:{rid}")
    notify_room_unread(rid, uid, username, content, image)

    if bot_plugin and content:
        try:
            reply = bot_plugin.handle_message(username, content)
        except Exception as e:
            print("bot error:", e); reply = None
        if reply:
            post_bot_message(str(reply), room_id=rid)


@socketio.on("delete_message")
def on_delete(data):
    if "user_id" not in session: return
    mid = data.get("id")
    if not mid: return
    with db() as conn:
        u = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
        m = conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
        if not m or not u: return
        if m["user_id"] != u["id"] and not u["is_admin"]: return
        rid = m["room_id"]
        conn.execute("DELETE FROM messages WHERE id=?", (mid,))
        conn.commit()
    emit("message_deleted", {"id": mid}, to=f"room:{rid}")


# =====================================================================
# ============== DARK_GC v2.0 — Videos, Typing, Online ================
# =====================================================================

from flask import Response, stream_with_context

# ---------- Schema v2 (videos + reactions + comments) ----------
def init_db_v2():
    with db() as conn:
        if USE_PG:
            conn.execute("""CREATE TABLE IF NOT EXISTS videos (
                id SERIAL PRIMARY KEY,
                uploader_id INTEGER NOT NULL,
                title TEXT,
                mime TEXT,
                size_bytes BIGINT,
                data BYTEA,
                created_at BIGINT,
                expires_at BIGINT
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS video_reactions (
                id SERIAL PRIMARY KEY,
                video_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                created_at BIGINT,
                UNIQUE(video_id, user_id)
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS video_comments (
                id SERIAL PRIMARY KEY,
                video_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at BIGINT
            )""")
        else:
            conn.execute("""CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uploader_id INTEGER NOT NULL,
                title TEXT,
                mime TEXT,
                size_bytes INTEGER,
                data BLOB,
                created_at INTEGER,
                expires_at INTEGER
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS video_reactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                created_at INTEGER,
                UNIQUE(video_id, user_id)
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS video_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at INTEGER
            )""")
        conn.commit()


init_db_v2()


# ---------- text inspection (link / raw-code guard) ----------
_LINK_RE = re.compile(r"https?://|www\.[a-z0-9]", re.I)
_CODE_HINTS = (
    "function ", "def ", "import ", "class ", "console.log", "print(",
    "</", "<script", "<?php", "#include", "{\n", ";\n", "=>", "():",
    "sudo ", "curl ", "rm -rf", "select * from", "DROP TABLE",
)

def inspect_user_text(text):
    """Returns 'link' / 'rawcode' / None."""
    if not text: return None
    if _LINK_RE.search(text):
        return "link"
    # If user wrapped code in triple quotes, allow it
    if '"""' in text or "'''" in text or text.startswith("```"):
        return None
    lines = text.splitlines()
    if len(lines) >= 3:
        hits = sum(1 for h in _CODE_HINTS if h.lower() in text.lower())
        if hits >= 2:
            return "rawcode"
    return None


# ---------- online presence ----------
_online_lock = threading.Lock()
_online = {}   # user_id -> {"username":..., "sids": set(), "last": ts}

def _add_online(user_id, username, sid):
    with _online_lock:
        rec = _online.setdefault(user_id, {"username": username, "sids": set(), "last": 0})
        rec["sids"].add(sid)
        rec["last"] = int(time.time())

def _remove_online(sid):
    with _online_lock:
        gone = []
        for uid, rec in _online.items():
            rec["sids"].discard(sid)
            if not rec["sids"]:
                gone.append(uid)
        for uid in gone:
            _online.pop(uid, None)

def list_online_users():
    with _online_lock:
        return [{"user_id": uid, "username": r["username"], "last_seen": r["last"]}
                for uid, r in sorted(_online.items())]


@app.get("/api/admin/online")
@admin_required
def api_admin_online():
    return jsonify({"ok": True, "online": list_online_users(),
                    "count": len(list_online_users())})


@app.get("/api/admin/stats")
@admin_required
def api_admin_stats():
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        banned = conn.execute("SELECT COUNT(*) AS c FROM users WHERE is_banned=1").fetchone()["c"]
        pending = conn.execute("SELECT COUNT(*) AS c FROM room_access WHERE status='pending'").fetchone()["c"]
        try:
            videos = conn.execute("SELECT COUNT(*) AS c FROM videos").fetchone()["c"]
        except Exception:
            videos = 0
    return jsonify({"ok": True, "users": int(total or 0), "banned": int(banned or 0),
                    "pending": int(pending or 0), "videos": int(videos or 0),
                    "online": len(list_online_users())})


# ---------- admin: permanently delete user ----------
@app.post("/admin/delete_user/<int:uid>")
@admin_required
def admin_delete_user(uid):
    with db() as conn:
        target = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
        if not target or target["username"] == ADMIN_USERNAME:
            return redirect(url_for("admin"))
        # wipe all traces
        conn.execute("DELETE FROM messages WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM dm_messages WHERE from_user_id=? OR to_user_id=?", (uid, uid))
        conn.execute("DELETE FROM room_access WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM room_reads WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM bot_sessions WHERE user_id=? OR admin_id=?", (uid, uid))
        conn.execute("DELETE FROM push_subscriptions WHERE user_id=?", (uid,))
        try:
            conn.execute("DELETE FROM video_reactions WHERE user_id=?", (uid,))
            conn.execute("DELETE FROM video_comments WHERE user_id=?", (uid,))
            conn.execute("DELETE FROM videos WHERE uploader_id=?", (uid,))
        except Exception:
            pass
        conn.execute("DELETE FROM users WHERE id=?", (uid,))
        conn.commit()
    return redirect(url_for("admin"))


# ---------- VIDEOS ----------
@app.get("/videos")
@login_required
def videos_index():
    user = current_user()
    with db() as conn:
        rows = conn.execute("""
            SELECT v.id, v.title, v.mime, v.size_bytes, v.created_at, v.expires_at,
                   COALESCE(u.username,'system') AS uploader,
                   (SELECT COUNT(*) FROM video_reactions r WHERE r.video_id=v.id AND r.kind='like') AS likes,
                   (SELECT COUNT(*) FROM video_reactions r WHERE r.video_id=v.id AND r.kind='dislike') AS dislikes,
                   (SELECT COUNT(*) FROM video_comments c WHERE c.video_id=v.id) AS comments
            FROM videos v LEFT JOIN users u ON u.id=v.uploader_id
            WHERE v.expires_at > ?
            ORDER BY v.id DESC
        """, (int(time.time()),)).fetchall()
    return render_template("videos.html", user=user, videos=rows)


@app.route("/videos/upload", methods=["GET", "POST"])
@login_required
def videos_upload():
    user = current_user()
    if not user["is_admin"]:
        # only admins upload videos
        return redirect(url_for("videos_index"))
    error = None
    if request.method == "POST":
        f = request.files.get("video")
        title = (request.form.get("title") or "").strip()[:120] or "untitled"
        if not f or not f.filename:
            error = "Pick a video file."
        else:
            ext = f.filename.rsplit(".", 1)[-1].lower()
            if ext not in ALLOWED_VIDEO_EXT:
                error = f"Only {', '.join(sorted(ALLOWED_VIDEO_EXT))} allowed."
            else:
                data = f.read()
                if len(data) > MAX_VIDEO_BYTES:
                    error = f"File too big (max {MAX_VIDEO_BYTES // (1024*1024)} MB)."
                else:
                    mime = f.mimetype or f"video/{ext if ext != 'mov' else 'quicktime'}"
                    now = int(time.time())
                    blob = psycopg2.Binary(data) if USE_PG else sqlite3.Binary(data)
                    with db() as conn:
                        vid = conn.insert_id(
                            "INSERT INTO videos (uploader_id, title, mime, size_bytes, data, created_at, expires_at) "
                            "VALUES (?,?,?,?,?,?,?)",
                            (user["id"], title, mime, len(data), blob, now, now + VIDEO_TTL_SECONDS)
                        )
                        conn.commit()
                    return redirect(url_for("video_detail", vid=vid))
    return render_template("video_upload.html", user=user, error=error,
                           max_mb=MAX_VIDEO_BYTES // (1024*1024),
                           ttl_hours=round(VIDEO_TTL_SECONDS/3600, 1))


@app.get("/videos/<int:vid>")
@login_required
def video_detail(vid):
    user = current_user()
    now = int(time.time())
    with db() as conn:
        v = conn.execute("""
            SELECT v.id, v.title, v.mime, v.size_bytes, v.created_at, v.expires_at,
                   v.uploader_id, COALESCE(u.username,'system') AS uploader
            FROM videos v LEFT JOIN users u ON u.id=v.uploader_id
            WHERE v.id=?
        """, (vid,)).fetchone()
        if not v or v["expires_at"] <= now:
            abort(404)
        likes = conn.execute("SELECT COUNT(*) AS c FROM video_reactions WHERE video_id=? AND kind='like'", (vid,)).fetchone()["c"]
        dislikes = conn.execute("SELECT COUNT(*) AS c FROM video_reactions WHERE video_id=? AND kind='dislike'", (vid,)).fetchone()["c"]
        my_react = conn.execute("SELECT kind FROM video_reactions WHERE video_id=? AND user_id=?", (vid, user["id"])).fetchone()
        comments = conn.execute("""
            SELECT c.id, c.content, c.created_at, u.username, COALESCE(u.is_admin,0) AS is_admin
            FROM video_comments c JOIN users u ON u.id=c.user_id
            WHERE c.video_id=? ORDER BY c.id ASC
        """, (vid,)).fetchall()
    return render_template("video_detail.html", user=user, v=v,
                           likes=int(likes or 0), dislikes=int(dislikes or 0),
                           my_react=(my_react["kind"] if my_react else None),
                           comments=comments)


@app.get("/videos/<int:vid>/file")
@login_required
def video_file(vid):
    now = int(time.time())
    with db() as conn:
        v = conn.execute("SELECT mime, data, expires_at FROM videos WHERE id=?", (vid,)).fetchone()
    if not v or v["expires_at"] <= now:
        abort(404)
    data = bytes(v["data"]) if v["data"] is not None else b""
    resp = Response(data, mimetype=v["mime"] or "video/mp4")
    resp.headers["Accept-Ranges"] = "bytes"
    resp.headers["Cache-Control"] = "private, max-age=600"
    return resp


@app.post("/videos/<int:vid>/react")
@login_required
def video_react(vid):
    kind = (request.form.get("kind") or "").strip().lower()
    if kind not in ("like", "dislike"):
        return redirect(url_for("video_detail", vid=vid))
    uid = session["user_id"]
    ts = int(time.time())
    with db() as conn:
        existing = conn.execute(
            "SELECT id, kind FROM video_reactions WHERE video_id=? AND user_id=?", (vid, uid)
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO video_reactions (video_id, user_id, kind, created_at) VALUES (?,?,?,?)",
                (vid, uid, kind, ts)
            )
        elif existing["kind"] == kind:
            conn.execute("DELETE FROM video_reactions WHERE id=?", (existing["id"],))
        else:
            conn.execute("UPDATE video_reactions SET kind=?, created_at=? WHERE id=?", (kind, ts, existing["id"]))
        conn.commit()
    return redirect(url_for("video_detail", vid=vid))


@app.post("/videos/<int:vid>/comment")
@login_required
def video_comment(vid):
    text = (request.form.get("content") or "").strip()[:1000]
    if not text:
        return redirect(url_for("video_detail", vid=vid))
    with db() as conn:
        conn.execute(
            "INSERT INTO video_comments (video_id, user_id, content, created_at) VALUES (?,?,?,?)",
            (vid, session["user_id"], text, int(time.time()))
        )
        conn.commit()
    return redirect(url_for("video_detail", vid=vid))


@app.post("/videos/<int:vid>/delete")
@admin_required
def video_delete(vid):
    with db() as conn:
        conn.execute("DELETE FROM video_comments WHERE video_id=?", (vid,))
        conn.execute("DELETE FROM video_reactions WHERE video_id=?", (vid,))
        conn.execute("DELETE FROM videos WHERE id=?", (vid,))
        conn.commit()
    return redirect(url_for("videos_index"))


# ---------- socket: presence + typing ----------
@socketio.on("connect")
def on_connect_v2():
    if "user_id" in session:
        join_room(f"user:{session['user_id']}")
        _add_online(session["user_id"], session.get("username", "?"), request.sid)
        socketio.emit("presence", {"online": len(list_online_users())}, to="admins")


@socketio.on("disconnect")
def on_disconnect_v2():
    _remove_online(request.sid)
    socketio.emit("presence", {"online": len(list_online_users())}, to="admins")


@socketio.on("admin_watch")
def on_admin_watch():
    if "user_id" not in session: return
    with db() as conn:
        u = conn.execute("SELECT is_admin FROM users WHERE id=?", (session["user_id"],)).fetchone()
    if u and u["is_admin"]:
        join_room("admins")
        emit("presence", {"online": len(list_online_users()),
                          "users": list_online_users()})


@socketio.on("typing")
def on_typing(data):
    if "user_id" not in session: return
    rid = int(data.get("room_id") or 0)
    if not rid: return
    typing = bool(data.get("typing"))
    emit("typing", {
        "room_id": rid,
        "user_id": session["user_id"],
        "username": session.get("username", "?"),
        "typing": typing,
    }, to=f"room:{rid}", include_self=False)


if __name__ == "__main__":
    print("=" * 50)
    print(" DARK_GC server running")
    print(f" DB backend: {'PostgreSQL (permanent)' if USE_PG else 'SQLite (DEV ONLY)'}")
    print(" Local:  http://localhost:5000")
    print("=" * 50)
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)
