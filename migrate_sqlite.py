import os
import sqlite3
from pathlib import Path
import psycopg

SQLITE_DB = Path(os.getenv("SQLITE_DB", "gate_da_tracker.db"))
DATABASE_URL = os.environ["DATABASE_URL"]


def main():
    if not SQLITE_DB.exists():
        raise SystemExit(f"SQLite database not found: {SQLITE_DB}")

    sqlite = sqlite3.connect(SQLITE_DB)
    sqlite.row_factory = sqlite3.Row

    with psycopg.connect(DATABASE_URL) as pg:
        pg.execute("""
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

        rows = sqlite.execute("""
            SELECT id, entry_date, subject, difficulty, questions, points, created_at
            FROM entries
            ORDER BY id
        """).fetchall()

        for row in rows:
            pg.execute("""
                INSERT INTO entries(
                    id, entry_date, subject, difficulty, questions, points, created_at
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (id) DO NOTHING
            """, tuple(row))

        pg.execute("""
            SELECT setval(
                pg_get_serial_sequence('entries','id'),
                COALESCE((SELECT MAX(id) FROM entries), 1),
                true
            )
        """)

    sqlite.close()
    print(f"Migrated {len(rows)} entries.")


if __name__ == "__main__":
    main()
