# DARK_GC Render-ready version

This version removes `eventlet` and `gunicorn` and uses Flask-SocketIO threading mode, so Render can run it with:

```bash
python app.py
```

## Upload to GitHub

Upload the files inside this folder to a new GitHub repository. Keep this structure:

```text
app.py
bot.py
dark_gc.db
requirements.txt
render.yaml
static/
templates/
```

## Render setup

1. Create a new Web Service from the GitHub repo.
2. Render should detect `render.yaml` automatically.
3. If Render asks manually, use:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python app.py`

## Database note

The app now always looks for `dark_gc.db` beside `app.py` unless you set `DATABASE_PATH`.
If the file is missing, the app creates the required tables automatically so `/chat` will not crash from a missing database.

For permanent data across Render redeploys, add a Render Disk and set:

```text
DATABASE_PATH=/var/data/dark_gc.db
```
