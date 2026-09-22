"""
Google PageSpeed Insights API 연동.
우리가 임의로 계산하지 않고, 구글이 실제로 검색 랭킹에 반영하는 Lighthouse 점수를
그대로 가져온다. API 키 없이도 낮은 쿼터로 작동하지만, 키가 있으면 더 안정적이다.

발급(선택): console.cloud.google.com → API 및 서비스 → 라이브러리 →
"PageSpeed Insights API" 사용 설정 → API 키 만들기 (기존 프로젝트 재사용 가능)
"""

import requests
from datetime import datetime, timezone

ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


def collect_pagespeed(url, api_key=None, strategy="mobile", mock=False):
    """
    url: 분석할 페이지 주소
    strategy: "mobile" 또는 "desktop" — 모바일 우선이 기본(구글 검색도 모바일 우선 색인)
    mock: True면 API 호출 없이 데모 값 반환
    """
    if mock:
        return _mock_pagespeed(url)

    params = {
        "url": url,
        "strategy": strategy,
        "category": ["performance", "accessibility", "best-practices", "seo"],
    }
    if api_key:
        params["key"] = api_key

    try:
        resp = requests.get(ENDPOINT, params=params, timeout=30)
        resp.raise_for_status()
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


def _mock_pagespeed(url):
    return {
        "source": "MOCK",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "strategy": "mobile",
        "performance": 62, "accessibility": 88,
        "best_practices": 79, "seo": 91,
        "lcp": "3.2 s", "cls": "0.08", "tbt": "310 ms",
    }
