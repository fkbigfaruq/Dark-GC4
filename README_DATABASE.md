# Permanent Database Setup (PostgreSQL)

Your app now reads `DATABASE_URL` from environment variables.
- If `DATABASE_URL` is set → uses **PostgreSQL** (data is permanent ✅)
- If not set → falls back to local SQLite (good for testing on your computer)

## Easiest option: free Neon PostgreSQL

1. Go to **https://neon.tech** → Sign up (free, no card needed)
2. Create a new project (any name, any region)
3. After it's created, click **Connection Details / Connection string**
4. Copy the string — it looks like:
   `postgresql://user:password@ep-xxxx.region.aws.neon.tech/neondb?sslmode=require`

## Add it to Render

1. Open your service on **render.com**
2. Go to **Environment** (left sidebar) → **Add Environment Variable**
3. Key: `DATABASE_URL`
   Value: *(paste the Neon connection string)*
4. Click **Save Changes** → Render will redeploy automatically

That's it. The first time it boots it will auto-create the `users` and
`messages` tables in Neon. Users and chat history will now survive forever
(no more resets when Render restarts).

## Alternative: Render PostgreSQL
On Render → **New +** → **PostgreSQL** → Free plan → after it's ready,
copy the **Internal Database URL** and paste it into `DATABASE_URL` on
your web service.
