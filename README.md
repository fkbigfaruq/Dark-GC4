# DARK_GC — Render-ready chat

Simple Flask + SocketIO chat app. This version is ready for Render and does **not** use eventlet or gunicorn.

## Features

- Username + password auth
- Real-time messaging with Flask-SocketIO threading mode
- Emoji picker
- Image sharing
- Reply to messages
- Delete messages
- Avatars
- Admin panel with ban / unban / promote / demote
- Bot replies from `bot.py`

## Run locally

```bash
pip install -r requirements.txt
python app.py
```

Open `http://localhost:5000`.

## Deploy on Render

Upload this whole folder to GitHub, then create a Render Web Service.

If Render asks for commands, use:

```text
Build Command: pip install -r requirements.txt
Start Command: python app.py
```

`render.yaml` is included, so Render may fill those automatically.

## Database

The app uses `dark_gc.db` beside `app.py` by default and creates missing tables automatically.

If you later add a Render Disk for permanent storage, set:

```text
DATABASE_PATH=/var/data/dark_gc.db
```

## Admin

The user named `fkbigfaruq` is admin. Edit `ADMIN_USERNAME` in `app.py` to change it.
