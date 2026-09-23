"""
분석 결과 캐시 (Supabase REST 경유).
같은 데이터를 페이지 방문할 때마다 새로 계산하지 않기 위해, 각 수집기 결과를
kind별로 저장해두고 TTL 이내면 그대로 재사용한다. PSI 풀 감사·사이트 크롤·
Gemini 호출처럼 느리거나 쿼터가 있는 수집기일수록 이득이 크다.
history_store.py/settings_store.py와 같은 이유로 Supabase 바깥 저장이 필요하다 —
Render는 재배포마다 로컬 파일시스템이 초기화된다.
"""

import requests
from datetime import datetime, timezone

TABLE = "geo_cache"


def _headers(api_key):
    return {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def get_cache(supabase_url, supabase_key, domain, kind):
    """(data, fetched_at, age_seconds) 반환. 없거나 실패하면 (None, None, None)."""
    if not (supabase_url and supabase_key):
        return None, None, None
    try:
        resp = requests.get(
            f"{supabase_url}/rest/v1/{TABLE}",
            params={"domain": f"eq.{domain}", "kind": f"eq.{kind}", "select": "data,fetched_at"},
            headers=_headers(supabase_key),
            timeout=10,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return None, None, None
        row = rows[0]
        fetched_at = datetime.fromisoformat(row["fetched_at"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
        return row["data"], fetched_at, age
    except Exception:
        return None, None, None


def save_cache(supabase_url, supabase_key, domain, kind, data):
    if not (supabase_url and supabase_key):
        return False
    try:
        resp = requests.post(
            f"{supabase_url}/rest/v1/{TABLE}",
            params={"on_conflict": "domain,kind"},
            headers={**_headers(supabase_key), "Prefer": "resolution=merge-duplicates,return=minimal"},
            json={
                "domain": domain, "kind": kind, "data": data,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            },
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception:
        return False
