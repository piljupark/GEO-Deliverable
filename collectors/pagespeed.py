"""
Google PageSpeed Insights API 연동.
우리가 임의로 계산하지 않고, 구글이 실제로 검색 랭킹에 반영하는 Lighthouse 점수를
그대로 가져온다. API 키 없이도 낮은 쿼터로 작동하지만, 키가 있으면 더 안정적이다.

발급(선택): console.cloud.google.com → API 및 서비스 → 라이브러리 →
"PageSpeed Insights API" 사용 설정 → API 키 만들기 (기존 프로젝트 재사용 가능)
"""

from datetime import datetime, timezone

from collectors._http import request_with_retry

ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


def collect_pagespeed(url, api_key=None, strategy="mobile"):
    """
    url: 분석할 페이지 주소
    strategy: "mobile" 또는 "desktop" — 모바일 우선이 기본(구글 검색도 모바일 우선 색인)
    항상 실제 API를 호출한다. api_key가 없어도 쿼터만 낮을 뿐 호출은 그대로 시도한다.
    """
    # performance 카테고리만 요청한다 — 실제로 화면엔 성능 점수/LCP/CLS/TBT만 쓰는데
    # accessibility/best-practices/seo까지 같이 시키면 Lighthouse가 그만큼 더 오래 걸린다.
    params = {
        "url": url,
        "strategy": strategy,
        "category": "performance",
    }
    if api_key:
        params["key"] = api_key

    try:
        # 실제 서버에서 풀 Lighthouse 감사를 돌리는 API라 느릴 때가 많고, 타임아웃/5xx가
        # 종종 일시적으로 난다 — 짧게 재시도한다.
        # 60초씩 3번 재시도하면 최악의 경우 3분 걸린다 — 2번으로 제한.
        resp = request_with_retry("GET", ENDPOINT, params=params, timeout=60, attempts=2)
        data = resp.json()
    except Exception as e:
        return {
            "source": f"ERROR:{type(e).__name__}",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "performance": None, "accessibility": None,
            "best_practices": None, "seo": None,
            "lcp": None, "cls": None, "tbt": None,
            "detail": str(e),
        }

    lh = data.get("lighthouseResult", {})
    categories = lh.get("categories", {})
    audits = lh.get("audits", {})

    def _cat_score(key):
        c = categories.get(key)
        if not c or c.get("score") is None:
            return None
        return round(c["score"] * 100)

    def _audit_value(key):
        a = audits.get(key)
        return a.get("displayValue") if a else None

    return {
        "source": "LIVE",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "strategy": strategy,
        "performance": _cat_score("performance"),
        "accessibility": _cat_score("accessibility"),
        "best_practices": _cat_score("best-practices"),
        "seo": _cat_score("seo"),
        "lcp": _audit_value("largest-contentful-paint"),
        "cls": _audit_value("cumulative-layout-shift"),
        "tbt": _audit_value("total-blocking-time"),
    }
