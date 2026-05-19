# DARK_GHC permanent database + notifications fix

## What was fixed

- The app now refuses to start without `DATABASE_URL` unless you explicitly set `ALLOW_SQLITE_DEV=1`.
- This prevents Render from silently using temporary SQLite storage, which is what makes messages/users disappear after restarts or redeploys.
- Added room unread badges on the rooms page.
- Added DM unread badges in the inbox and rooms header.
- Added browser/Chrome notifications for new room messages and DMs.
- Added online/offline connection notifications.
- Added PWA files so the site can be installed as an app.
- Updated admin username color and bot bubbles.
- Updated the DM negotiation bot so the default locked-room price is `500`.

## Very important Render settings

Open your Render service → Environment and set:

```txt
DATABASE_URL=your permanent PostgreSQL connection string
SECRET_KEY=any long random secret
BOT_DEFAULT_ROOM_PRICE=500
BOT_ROOM_PRICES={"default":500}
BOT_CURRENCY=₦
BOT_ENABLED=1
```

Do **not** set `ALLOW_SQLITE_DEV=1` on Render. That option is only for testing on your own computer.

## How to confirm the database is permanent

After deploy, open:

```txt
https://your-app.onrender.com/healthz
```

You must see:

```json
{"ok": true, "db": "postgres", "permanent": true}
```

If it says `sqlite`, stop and fix `DATABASE_URL` before users continue chatting.

## Notifications note

Chrome/Android notifications only work after the user taps **Enable notifications** and allows permission. iPhone Safari supports installed web-app notifications only on newer iOS versions.
