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
RUNS_TABLE = "geo_prompt_runs"


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


def save_prompt_runs(supabase_url, supabase_key, domain, records, prompt_topics=None):
    """
    geo_history가 하루치 요약 숫자 3개만 남기는 것과 달리, 이건 그 실행의 프롬프트별
    원본 결과(언급/인용/인용 URL/경쟁사 결과)를 한 행씩 그대로 적재한다. 나중에
    프롬프트별 이력, "경쟁사는 인용됐는데 우리는 안 된 페이지" 같은 걸 만들려면
    요약값만으로는 안 되고 이 원본이 있어야 한다. 하루에 여러 번 실행해도 안 덮어쓰고
    행이 계속 쌓인다 — upsert가 아니라 순수 insert.
    records: run_geo_visibility()가 반환하는 geo["records"] 그대로.
    prompt_topics: {prompt_text: topic|None} — 저장된 프롬프트를 쓴 경우에만 채워짐.
    실패해도 조용히 넘어간다 — 부가 기능이라 본 분석 결과에 영향을 주면 안 된다.
    """
    if not (supabase_url and supabase_key) or not records:
        return
    prompt_topics = prompt_topics or {}
    today = date.today().isoformat()
    rows = [
        {
            "domain": domain,
            "date": today,
            "prompt": rec["prompt"],
            "topic": prompt_topics.get(rec["prompt"]),
            "status": rec["status"],
            "mentioned": rec["mentioned"],
            "cited": rec["cited"],
            "cited_urls": rec["cited_urls"],
            "competitor_mentions": rec["competitor_mentions"],
            "competitor_citations": rec["competitor_citations"],
        }
        for rec in records
    ]
    try:
        requests.post(
            f"{supabase_url}/rest/v1/{RUNS_TABLE}",
            headers={**_headers(supabase_key), "Prefer": "return=minimal"},
            json=rows,
            timeout=10,
        )
    except Exception:
        pass


def get_citation_gaps(supabase_url, supabase_key, domain, days=30, limit=200):
    """최근 days일간의 geo_prompt_runs에서 "경쟁사는 인용됐는데 우리는 안 된" 프롬프트를 찾는다.
    프롬프트별로 집계해서 gap_count(경쟁사만 인용된 횟수) 많은 순으로 정렬한 리스트를 반환.
    각 항목: {prompt, topic, gap_count, total_count, competitors: {name: count}, last_date}.
    설정이 없거나 조회 실패, 데이터가 없으면 빈 리스트 — 카드가 그냥 안 뜬다."""
    if not (supabase_url and supabase_key):
        return []
    try:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        resp = requests.get(
            f"{supabase_url}/rest/v1/{RUNS_TABLE}",
            params={
                "domain": f"eq.{domain}",
                "date": f"gte.{cutoff}",
                "status": "eq.LIVE",
                "select": "prompt,topic,date,cited,competitor_citations",
                "order": "date.desc",
                "limit": str(limit),
            },
            headers=_headers(supabase_key),
            timeout=10,
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception:
        return []

    by_prompt = {}
    for row in rows:
        entry = by_prompt.setdefault(row["prompt"], {
            "prompt": row["prompt"], "topic": row.get("topic"),
            "gap_count": 0, "total_count": 0, "competitors": {}, "last_date": row["date"],
        })
        entry["total_count"] += 1
        entry["last_date"] = max(entry["last_date"], row["date"])
        cited_competitors = [name for name, v in (row.get("competitor_citations") or {}).items() if v]
        if row.get("cited") or not cited_competitors:
            continue  # 우리가 인용됐거나 아무도 인용 안 됐으면 "기회"가 아니다
        entry["gap_count"] += 1
        for name in cited_competitors:
            entry["competitors"][name] = entry["competitors"].get(name, 0) + 1

    gaps = [e for e in by_prompt.values() if e["gap_count"] > 0]
    gaps.sort(key=lambda e: -e["gap_count"])
    return gaps


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
