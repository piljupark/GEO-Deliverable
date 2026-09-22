"""
노출도/인용/언급 지표의 일자별 추이 저장소 (Supabase REST 경유).
Render는 재배포마다 로컬 파일시스템이 초기화되므로, 우리 앱 바깥의 관리형 DB에
저장해야 이력이 유지된다. SUPABASE_URL/SUPABASE_KEY가 없으면 모든 함수가 조용히
아무것도 안 하거나 빈 값을 반환한다 — 추이 그래프만 빠지고 나머지 분석은 그대로
작동해야 하므로, 여기서 나는 에러로 전체 분석을 망가뜨리지 않는다.
"""

import requests
from datetime import date, timedelta

TABLE = "geo_history"


def _headers(api_key):
    return {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def save_snapshot(supabase_url, supabase_key, domain, exposure_score, citation_share, mention_share):
    """오늘 날짜로 한 행을 upsert한다. 실패해도 조용히 넘어간다 — 부가 기능이라
    이게 본 분석 결과에 영향을 주면 안 된다."""
    if not (supabase_url and supabase_key):
        return
    try:
        requests.post(
            f"{supabase_url}/rest/v1/{TABLE}",
            params={"on_conflict": "domain,date"},
            headers={**_headers(supabase_key), "Prefer": "resolution=merge-duplicates,return=minimal"},
            json={
                "domain": domain,
                "date": date.today().isoformat(),
                "exposure_score": exposure_score,
                "citation_share": citation_share,
                "mention_share": mention_share,
            },
            timeout=10,
        )
    except Exception:
        pass


def get_history(supabase_url, supabase_key, domain, days=30):
    """최근 days일치 (date, exposure_score, citation_share, mention_share) 목록.
    설정이 없거나 조회 실패하면 빈 리스트 — 추이 카드가 그냥 안 뜬다."""
    if not (supabase_url and supabase_key):
        return []
    try:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        resp = requests.get(
            f"{supabase_url}/rest/v1/{TABLE}",
            params={
                "domain": f"eq.{domain}",
                "date": f"gte.{cutoff}",
                "select": "date,exposure_score,citation_share,mention_share",
                "order": "date.asc",
            },
            headers=_headers(supabase_key),
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return []
