"""
SQLite 저장소. 로컬 버전의 data/*.json 파일들을 DB로 대체.
스냅샷(gsc/naver/ga4)과 타겟 키워드 성장 기록을 저장한다.
Render 무료 티어는 재배포 시 파일시스템이 초기화될 수 있으므로,
DB 파일 경로를 영속 디스크가 있으면 그쪽으로, 없으면 로컬 파일로 둔다.
"""

import os
import json
import sqlite3
from datetime import datetime, timezone
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", "app.db")


def init_db():
    with _conn() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,           -- 'gsc' | 'naver' | 'ga4' | 'tech' | 'competitors' | 'serp'
            created_at TEXT NOT NULL,
            data TEXT NOT NULL            -- JSON
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS keyword_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT NOT NULL,
            date TEXT NOT NULL,           -- YYYY-MM-DD
            impressions INTEGER, clicks INTEGER,
            gsc_position REAL, serp_rank INTEGER,
            UNIQUE(keyword, date)
        )""")
        c.commit()


@contextmanager
def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
    finally:
        c.close()


def save_snapshot(kind, data):
    with _conn() as c:
        c.execute(
            "INSERT INTO snapshots (kind, created_at, data) VALUES (?, ?, ?)",
            (kind, datetime.now(timezone.utc).isoformat(), json.dumps(data, ensure_ascii=False)),
        )
        c.commit()


def latest_snapshot(kind):
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM snapshots WHERE kind=? ORDER BY id DESC LIMIT 1", (kind,)
        ).fetchone()
        if not row:
            return None
        return {"created_at": row["created_at"], "data": json.loads(row["data"])}


def save_keyword_point(keyword, date, impressions, clicks, gsc_position, serp_rank):
    with _conn() as c:
        c.execute("""
        INSERT INTO keyword_history (keyword, date, impressions, clicks, gsc_position, serp_rank)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(keyword, date) DO UPDATE SET
            impressions=excluded.impressions, clicks=excluded.clicks,
            gsc_position=excluded.gsc_position, serp_rank=excluded.serp_rank
        """, (keyword, date, impressions, clicks, gsc_position, serp_rank))
        c.commit()


def keyword_history(keyword):
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM keyword_history WHERE keyword=? ORDER BY date", (keyword,)
        ).fetchall()
        return [dict(r) for r in rows]
