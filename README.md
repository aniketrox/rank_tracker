# GATE DA 2027 Tracker — Vercel

This version uses:
- FastAPI
- Vercel Python Functions
- Neon PostgreSQL
- Mobile-responsive HTML frontend

## Environment variable

Set this in Vercel:

`DATABASE_URL`

Use the connection string provided by your Neon database.

## Local run

```bash
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

pip install -r requirements.txt
uvicorn api.index:app --reload
```

Open http://127.0.0.1:8000

## Deploy

Push this folder to GitHub, import it into Vercel, and add `DATABASE_URL`
under Project Settings → Environment Variables.

The application automatically creates the `entries`, `daily_snapshots`,
and index tables on first request.
