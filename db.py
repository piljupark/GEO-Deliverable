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
        c.execute("""
        CREATE TABLE IF NOT EXISTS geo_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,           -- YYYY-MM-DD
            platform TEXT NOT NULL,       -- 'gemini' (추후 다른 플랫폼 확장 대비)
            prompt TEXT NOT NULL,
            status TEXT NOT NULL,         -- 'LIVE' | 'ERROR:...'
            mentioned INTEGER,            -- 0/1/NULL
            cited INTEGER,                -- 0/1/NULL
            cited_urls TEXT,              -- JSON
            competitor_mentions TEXT,     -- JSON
            competitor_citations TEXT,    -- JSON
            answer_preview TEXT,
            detail TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(date, platform, prompt)
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


def save_geo_run(date, platform, prompt, status, mentioned, cited,
                  cited_urls, competitor_mentions, competitor_citations,
                  answer_preview, detail):
    with _conn() as c:
        c.execute("""
        INSERT INTO geo_runs (date, platform, prompt, status, mentioned, cited,
                               cited_urls, competitor_mentions, competitor_citations,
                               answer_preview, detail, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date, platform, prompt) DO UPDATE SET
            status=excluded.status, mentioned=excluded.mentioned, cited=excluded.cited,
            cited_urls=excluded.cited_urls, competitor_mentions=excluded.competitor_mentions,
            competitor_citations=excluded.competitor_citations,
            answer_preview=excluded.answer_preview, detail=excluded.detail,
            created_at=excluded.created_at
        """, (date, platform, prompt, status, mentioned, cited,
              json.dumps(cited_urls, ensure_ascii=False),
              json.dumps(competitor_mentions, ensure_ascii=False),
              json.dumps(competitor_citations, ensure_ascii=False),
              answer_preview, detail, datetime.now(timezone.utc).isoformat()))
        c.commit()


def geo_runs_on_date(date, platform="gemini"):
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM geo_runs WHERE date=? AND platform=? ORDER BY id", (date, platform)
        ).fetchall()
        return [_geo_row_to_dict(r) for r in rows]


def geo_runs_history(platform="gemini", limit_days=30):
    """최근 limit_days일치 기록을 날짜순으로 반환 (트렌드 차트용)."""
    with _conn() as c:
        rows = c.execute("""
            SELECT * FROM geo_runs WHERE platform=?
            AND date >= date('now', ?)
            ORDER BY date
        """, (platform, f"-{limit_days} days")).fetchall()
        return [_geo_row_to_dict(r) for r in rows]


def _geo_row_to_dict(row):
    d = dict(row)
    for key in ("cited_urls", "competitor_mentions", "competitor_citations"):
        try:
            d[key] = json.loads(d[key]) if d[key] else ({} if "urls" not in key else [])
        except Exception:
            d[key] = {} if "urls" not in key else []
    return d
