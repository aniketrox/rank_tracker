from datetime import date, datetime, timedelta
from pathlib import Path
import math
import os

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

BASE = Path(__file__).resolve().parent.parent
START_DATE = date(2026, 8, 19)
END_DATE = date(2027, 1, 18)
START_RANK = 100000

SUBJECT_WEIGHTS = {
    "Probability & Statistics": 17.95,
    "Programming, DS & Algorithms": 15.90,
    "Machine Learning": 14.36,
    "DBMS & Warehousing": 11.28,
    "Linear Algebra": 9.74,
    "AI": 8.72,
    "Calculus & Optimization": 6.67,
    "General Aptitude": 15.38,
}

DIFFICULTY_MULTIPLIER = {
    "easy": 0.75,
    "medium": 1.00,
    "hard": 1.35,
    "pyq": 1.50,
}

AVG_WEIGHT = sum(SUBJECT_WEIGHTS.values()) / len(SUBJECT_WEIGHTS)

app = FastAPI(title="GATE DA 2027 Tracker API", version="3.0.0")


def db():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not configured. Add your Neon PostgreSQL connection string "
            "to Vercel Environment Variables."
        )
    return psycopg.connect(url, row_factory=dict_row)


def init_db():
    with db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id BIGSERIAL PRIMARY KEY,
                entry_date DATE NOT NULL,
                subject TEXT NOT NULL,
                difficulty TEXT NOT NULL,
                questions INTEGER NOT NULL,
                points DOUBLE PRECISION NOT NULL,
                created_at TIMESTAMP NOT NULL
            );

            CREATE TABLE IF NOT EXISTS daily_snapshots (
                snapshot_date DATE PRIMARY KEY,
                rank INTEGER NOT NULL,
                total_questions INTEGER NOT NULL,
                points DOUBLE PRECISION NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_entries_entry_date
            ON entries(entry_date);
        """)


def ensure_db():
    init_db()


class EntryIn(BaseModel):
    subject: str
    difficulty: str
    questions: int = Field(gt=0, le=10000)
    entry_date: str | None = None


def validate_date(value: str | None, allow_future: bool = False) -> date:
    try:
        d = date.today() if not value else date.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, "Invalid date. Use YYYY-MM-DD.")

    if d < START_DATE or d > END_DATE:
        raise HTTPException(400, f"Date must be between {START_DATE} and {END_DATE}.")
    if not allow_future and d > date.today():
        raise HTTPException(400, "You can only log problems for today or a past date.")
    return d


def points_for(subject: str, difficulty: str, questions: int) -> float:
    if subject not in SUBJECT_WEIGHTS:
        raise HTTPException(400, "Unknown subject.")
    if difficulty not in DIFFICULTY_MULTIPLIER:
        raise HTTPException(400, "Unknown difficulty.")
    subject_factor = SUBJECT_WEIGHTS[subject] / AVG_WEIGHT
    return questions * subject_factor * DIFFICULTY_MULTIPLIER[difficulty]


def totals(as_of: date | None = None):
    with db() as con:
        if as_of:
            row = con.execute(
                """
                SELECT COALESCE(SUM(questions),0) AS q,
                       COALESCE(SUM(points),0) AS p
                FROM entries
                WHERE entry_date <= %s
                """,
                (as_of,),
            ).fetchone()
        else:
            row = con.execute(
                """
                SELECT COALESCE(SUM(questions),0) AS q,
                       COALESCE(SUM(points),0) AS p
                FROM entries
                """
            ).fetchone()
    return int(row["q"]), float(row["p"])


def rank_from_points(points: float) -> int:
    reduction = (START_RANK - 1) * (1 - math.exp(-points / 2600.0))
    return max(1, round(START_RANK - reduction))


def rebuild_snapshots():
    """Build one cumulative snapshot for every date from START_DATE through today."""
    today = date.today()
    last_date = min(today, END_DATE)
    if last_date < START_DATE:
        return

    with db() as con:
        rows = con.execute(
            """
            SELECT entry_date,
                   COALESCE(SUM(questions),0) AS questions,
                   COALESCE(SUM(points),0) AS points
            FROM entries
            WHERE entry_date >= %s AND entry_date <= %s
            GROUP BY entry_date
            ORDER BY entry_date
            """,
            (START_DATE, last_date),
        ).fetchall()

        by_date = {
            row["entry_date"].isoformat():
                (int(row["questions"]), float(row["points"]))
            for row in rows
        }

        cumulative_q = 0
        cumulative_points = 0.0
        current = START_DATE
        snapshots = []

        while current <= last_date:
            q, p = by_date.get(current.isoformat(), (0, 0.0))
            cumulative_q += q
            cumulative_points += p
            snapshots.append((
                current,
                rank_from_points(cumulative_points),
                cumulative_q,
                cumulative_points,
            ))
            current += timedelta(days=1)

        con.execute("DELETE FROM daily_snapshots")
        if snapshots:
            con.executemany(
                """
                INSERT INTO daily_snapshots(
                    snapshot_date, rank, total_questions, points
                )
                VALUES (%s, %s, %s, %s)
                """,
                snapshots,
            )


def ensure_snapshots():
    rebuild_snapshots()


@app.get("/")
def home():
    return FileResponse(BASE / "public" / "index.html")


@app.get("/api/config")
def config():
    ensure_db()
    return {
        "start_date": START_DATE.isoformat(),
        "end_date": END_DATE.isoformat(),
        "today": date.today().isoformat(),
        "start_rank": START_RANK,
        "subjects": SUBJECT_WEIGHTS,
        "difficulty": DIFFICULTY_MULTIPLIER,
    }


@app.get("/api/dashboard")
def dashboard():
    ensure_db()
    today = date.today()
    q, p = totals(as_of=today)
    r = rank_from_points(p)

    with db() as con:
        today_row = con.execute(
            """
            SELECT COALESCE(SUM(questions),0) AS q,
                   COALESCE(SUM(points),0) AS p
            FROM entries
            WHERE entry_date=%s
            """,
            (today,),
        ).fetchone()

        since = today - timedelta(days=6)
        week_row = con.execute(
            """
            SELECT COALESCE(SUM(questions),0) AS q
            FROM entries
            WHERE entry_date >= %s AND entry_date <= %s
            """,
            (since, today),
        ).fetchone()

        active = con.execute(
            """
            SELECT COUNT(DISTINCT entry_date) AS n
            FROM entries
            WHERE entry_date >= %s AND entry_date <= %s
            """,
            (START_DATE, today),
        ).fetchone()["n"]

        subject_rows = con.execute(
            """
            SELECT subject, SUM(questions) AS questions, SUM(points) AS points
            FROM entries
            WHERE entry_date <= %s
            GROUP BY subject
            ORDER BY points DESC
            """,
            (today,),
        ).fetchall()

        difficulty_rows = con.execute(
            """
            SELECT difficulty, SUM(questions) AS questions, SUM(points) AS points
            FROM entries
            WHERE entry_date <= %s
            GROUP BY difficulty
            """,
            (today,),
        ).fetchall()

    ensure_snapshots()
    with db() as con:
        history_rows = con.execute(
            """
            SELECT snapshot_date, rank, total_questions, points
            FROM daily_snapshots
            ORDER BY snapshot_date
            """
        ).fetchall()

    remaining = max(0, (END_DATE - today).days) if today <= END_DATE else 0
    elapsed = max(1, (min(today, END_DATE) - START_DATE).days + 1)
    consistency = round(100 * int(active) / elapsed, 1)

    return {
        "rank": r,
        "rank_improvement": START_RANK - r,
        "total_questions": q,
        "points": round(p, 2),
        "today_questions": int(today_row["q"]),
        "today_points": round(float(today_row["p"]), 2),
        "seven_day_questions": int(week_row["q"]),
        "active_days": int(active),
        "consistency": consistency,
        "days_remaining": remaining,
        "subjects": [dict(x) for x in subject_rows],
        "difficulties": [dict(x) for x in difficulty_rows],
        "history": [
            {
                "snapshot_date": x["snapshot_date"].isoformat(),
                "rank": x["rank"],
                "total_questions": x["total_questions"],
                "points": float(x["points"]),
            }
            for x in history_rows
        ],
    }


@app.get("/api/history")
def history():
    ensure_db()
    today = date.today()
    ensure_snapshots()

    with db() as con:
        daily_rows = con.execute(
            """
            SELECT entry_date,
                   SUM(questions) AS questions,
                   SUM(points) AS points,
                   COUNT(*) AS entries
            FROM entries
            WHERE entry_date >= %s AND entry_date <= %s
            GROUP BY entry_date
            ORDER BY entry_date
            """,
            (START_DATE, END_DATE),
        ).fetchall()

        snapshot_rows = con.execute(
            """
            SELECT snapshot_date, rank, total_questions, points
            FROM daily_snapshots
            ORDER BY snapshot_date
            """
        ).fetchall()

    daily = {row["entry_date"].isoformat(): row for row in daily_rows}
    snapshots = {row["snapshot_date"].isoformat(): row for row in snapshot_rows}

    result = []
    current = START_DATE

    while current <= END_DATE:
        key = current.isoformat()
        day = daily.get(key)
        snap = snapshots.get(key)
        is_future = current > today

        result.append({
            "date": key,
            "questions": int(day["questions"]) if day else 0,
            "points": round(float(day["points"]), 2) if day else 0.0,
            "entries": int(day["entries"]) if day else 0,
            "rank": int(snap["rank"]) if snap else None,
            "cumulative_questions": int(snap["total_questions"]) if snap else None,
            "cumulative_points": round(float(snap["points"]), 2) if snap else None,
            "status": "future" if is_future else ("active" if day else "zero"),
        })
        current += timedelta(days=1)

    return {
        "start_date": START_DATE.isoformat(),
        "end_date": END_DATE.isoformat(),
        "today": today.isoformat(),
        "days": result,
    }


@app.post("/api/entries")
def add_entry(item: EntryIn):
    ensure_db()
    d = validate_date(item.entry_date)
    pts = points_for(item.subject, item.difficulty, item.questions)

    with db() as con:
        row = con.execute(
            """
            INSERT INTO entries(
                entry_date, subject, difficulty, questions, points, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                d,
                item.subject,
                item.difficulty,
                item.questions,
                pts,
                datetime.now(),
            ),
        ).fetchone()
        entry_id = row["id"]

    ensure_snapshots()
    _, current_points = totals(as_of=date.today())
    new_rank = rank_from_points(current_points)

    return {
        "id": int(entry_id),
        "entry_date": d.isoformat(),
        "points_added": round(pts, 2),
        "rank": new_rank,
    }


@app.get("/api/entries")
def entries(entry_date: str | None = None):
    ensure_db()

    with db() as con:
        if entry_date:
            try:
                requested = date.fromisoformat(entry_date)
            except ValueError:
                raise HTTPException(400, "Invalid date. Use YYYY-MM-DD.")

            rows = con.execute(
                """
                SELECT id, entry_date, subject, difficulty, questions, points,
                       created_at
                FROM entries
                WHERE entry_date=%s
                ORDER BY id DESC
                """,
                (requested,),
            ).fetchall()
        else:
            rows = con.execute(
                """
                SELECT id, entry_date, subject, difficulty, questions, points,
                       created_at
                FROM entries
                ORDER BY entry_date DESC, id DESC
                LIMIT 100
                """
            ).fetchall()

    return [
        {
            **dict(x),
            "entry_date": x["entry_date"].isoformat(),
            "created_at": x["created_at"].isoformat(),
            "points": float(x["points"]),
        }
        for x in rows
    ]


@app.delete("/api/entries/{entry_id}")
def delete_entry(entry_id: int):
    ensure_db()

    with db() as con:
        row = con.execute(
            "SELECT id FROM entries WHERE id=%s", (entry_id,)
        ).fetchone()

        if not row:
            raise HTTPException(404, "Entry not found.")

        con.execute("DELETE FROM entries WHERE id=%s", (entry_id,))

    ensure_snapshots()
    _, current_points = totals(as_of=date.today())
    rank = rank_from_points(current_points)

    return {"ok": True, "rank": rank}


@app.post("/api/snapshot")
def snapshot():
    ensure_db()
    ensure_snapshots()
    _, p = totals(as_of=date.today())
    return {"rank": rank_from_points(p)}


@app.get("/api/health")
def health():
    ensure_db()
    return {"status": "ok", "date": date.today().isoformat()}
