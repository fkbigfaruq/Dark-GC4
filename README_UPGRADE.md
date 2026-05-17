# DARK_GC — Rooms Upgrade

## What's new
- **Rooms page after login** (`/rooms`) — Main is open, Hackers & Coding are locked.
- **Locked rooms** show a "request" button → opens a small message box → DM goes to admin.
- **Admin panel** (`/admin`):
  - Pending access requests with Approve / Deny.
  - Add new rooms (locked or open) + delete rooms.
  - Existing ban / unban / promote / demote.
- **DMs** (`/messages`) — talk to admin (or anyone you've messaged).
- **Per-room chat** at `/chat/<slug>` — sockets are scoped per room.
- **Health check**: visit `/healthz` → JSON tells you which DB is active.

## The disappearing-messages fix
Your old app silently fell back to **SQLite** when `DATABASE_URL` was missing,
and Render wipes SQLite on every restart. That's why messages vanished after
a day or two.

This version **refuses to start on Render without `DATABASE_URL`**, so you'll
notice immediately instead of losing data silently. It also adds Postgres
keepalives so Neon's idle connections don't drop you.

### Make sure DATABASE_URL is set
On Render → your service → **Environment** tab → check that `DATABASE_URL`
exists and points to your Neon connection string (the long one ending in
`?sslmode=require`). If it's missing, deploy will fail with a clear message —
add it and redeploy.

### Verify after deploy
Open `https://your-app.onrender.com/healthz`
You should see: `{"ok": true, "db": "postgres", "permanent": true}`
If it says `"db": "sqlite"`, your DATABASE_URL is wrong — fix it.

## How to deploy
1. Replace **all files** in your GitHub repo with the ones in this zip.
2. Commit.
3. Render auto-redeploys. (If you removed DATABASE_URL, add it back first.)
4. Existing users and messages are kept. New `rooms`, `room_access`, and
   `dm_messages` tables are created automatically on first boot. Old
   messages land in the "Main" room.

## Admin account
Your admin username is still `fkbigfaruq` (hardcoded). Sign up with that
username if you haven't — it auto-becomes admin.
