"""
Google PageSpeed Insights API 연동.
우리가 임의로 계산하지 않고, 구글이 실제로 검색 랭킹에 반영하는 Lighthouse 점수를
그대로 가져온다. API 키 없이도 낮은 쿼터로 작동하지만, 키가 있으면 더 안정적이다.

발급(선택): console.cloud.google.com → API 및 서비스 → 라이브러리 →
"PageSpeed Insights API" 사용 설정 → API 키 만들기 (기존 프로젝트 재사용 가능)

풀 라이트하우스 감사는 구글 서버가 실제로 헤드리스 크롬을 띄워서 도는 거라
10초~2분 넘게 걸리기도 하고, 그 변동성은 우리가 통제할 수 없다. "최대한 디테일 +
항상 성공"을 둘 다 만족시키려고 3단계로 낮춰가며 시도한다:
  1) 4개 카테고리(성능/SEO/접근성/권장사항) 풀 감사 — 디테일 최대, 제일 느림
  2) 실패하면 performance 카테고리만 — 훨씬 가볍고 빠름, 핵심 점수는 확보
  3) 그것도 실패하면 CrUX 전용 API(실제 크롬 사용자 데이터, 라이트하우스 감사
     없이 거의 즉시 응답) — 랩 점수는 없어도 "실제 방문자 체감 속도"는 표시 가능
캐시/재시도 정책(오래된 성공값을 실패보다 우선 표시)은 main.py 쪽에서 처리한다 —
여기서는 "이번 한 번의 시도로 뭘 얻을 수 있는지"만 최대한 성실하게 알아낸다.
"""

from datetime import datetime, timezone
from urllib.parse import urlparse

from collectors._http import request_with_retry

ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
CRUX_ENDPOINT = "https://chromeuxreport.googleapis.com/v1/records:queryRecord"

# Core Web Vitals 공식 기준값 — CrUX 단독 API는 PSI와 달리 등급(category)을 안 주므로 직접 계산한다.
_CWV_THRESHOLDS = {
    "lcp_ms": (2500, 4000),
    "cls": (0.1, 0.25),
    "inp_ms": (200, 500),
}


def _cwv_category(metric, value):
    if value is None:
        return None
    good, needs_improvement = _CWV_THRESHOLDS[metric]
    if value <= good:
        return "FAST"
    if value <= needs_improvement:
        return "AVERAGE"
    return "SLOW"


def _empty_result(detail):
    return {
        "source": f"ERROR:{detail.__class__.__name__}" if isinstance(detail, Exception) else "ERROR",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "performance": None, "accessibility": None, "best_practices": None, "seo": None,
        "lcp": None, "cls": None, "tbt": None,
        "opportunities": [], "field_data": None,
        "detail": str(detail),
    }


def query_crux(url, api_key):
    """CrUX(실제 크롬 사용자 데이터) 전용 API. 라이트하우스 감사가 아니라 구글이 이미
    집계해둔 실측 데이터를 그냥 읽어오는 거라 보통 1초 안팎으로 끝난다 — PSI 풀 감사가
    전부 실패해도 이건 거의 항상 성공해서, 최소한의 신뢰할 수 있는 성능 신호로 쓴다.
    api_key 없으면 이 API 자체를 호출할 수 없어 None."""
    if not api_key:
        return None
    for body in ({"url": url}, {"origin": f"{urlparse(url).scheme}://{urlparse(url).netloc}"}):
        try:
            resp = request_with_retry(
                "POST", f"{CRUX_ENDPOINT}?key={api_key}", json=body, timeout=10, attempts=1,
            )
            metrics = (resp.json().get("record") or {}).get("metrics") or {}
            if not metrics:
                continue
            lcp = (metrics.get("largest_contentful_paint") or {}).get("percentiles", {}).get("p75")
            cls = (metrics.get("cumulative_layout_shift") or {}).get("percentiles", {}).get("p75")
            inp = (metrics.get("interaction_to_next_paint") or {}).get("percentiles", {}).get("p75")
            return {
                "level": "crux", "overall_category": None,
                "lcp_ms": lcp, "lcp_category": _cwv_category("lcp_ms", lcp),
                "cls": cls, "cls_category": _cwv_category("cls", cls),
                "inp_ms": inp, "inp_category": _cwv_category("inp_ms", inp),
            }
        except Exception:
            continue
    return None


def _run_lighthouse(url, api_key, strategy, categories, timeout, attempts):
    """PSI(라이트하우스) 호출 한 번. 성공하면 파싱된 dict, 실패하면 ERROR dict."""
    params = {"url": url, "strategy": strategy, "category": categories}
    if api_key:
        params["key"] = api_key
    try:
        resp = request_with_retry("GET", ENDPOINT, params=params, timeout=timeout, attempts=attempts)
        data = resp.json()
    except Exception as e:
        return _empty_result(e)

    lh = data.get("lighthouseResult", {})
    cats = lh.get("categories", {})
    audits = lh.get("audits", {})

    def _cat_score(key):
        c = cats.get(key)
        return round(c["score"] * 100) if c and c.get("score") is not None else None

    def _audit_value(key):
        a = audits.get(key)
        return a.get("displayValue") if a else None

    # 랩(시뮬레이션) 데이터 말고 크롬 실제 방문자 데이터(CrUX) — PSI 응답에 이미 같이
    # 들어있으면 그걸 쓴다(별도 호출 불필요). 트래픽이 적은 사이트는 페이지 단위 데이터가
    # 없어서 도메인 전체 집계(originLoadingExperience)로 대체된다.
    field_data = None
    for key, level in (("loadingExperience", "page"), ("originLoadingExperience", "origin")):
        exp = data.get(key)
        if exp and exp.get("metrics"):
            metrics = exp["metrics"]
            field_data = {
                "level": level,
                "overall_category": exp.get("overall_category"),
                "lcp_ms": (metrics.get("LARGEST_CONTENTFUL_PAINT_MS") or {}).get("percentile"),
                "lcp_category": (metrics.get("LARGEST_CONTENTFUL_PAINT_MS") or {}).get("category"),
                "cls": (metrics.get("CUMULATIVE_LAYOUT_SHIFT_SCORE") or {}).get("percentile"),
                "cls_category": (metrics.get("CUMULATIVE_LAYOUT_SHIFT_SCORE") or {}).get("category"),
                "inp_ms": (metrics.get("INTERACTION_TO_NEXT_PAINT") or {}).get("percentile"),
                "inp_category": (metrics.get("INTERACTION_TO_NEXT_PAINT") or {}).get("category"),
            }
            break

    # 개선 여지가 큰 순서로 상위 3개만 — 절감량(ms/byte)이 numericValue에 들어있다.
    opportunities = []
    for a in audits.values():
        details = a.get("details") or {}
        if details.get("type") != "opportunity":
            continue
        if a.get("score") is not None and a.get("score") >= 0.9:
            continue
        numeric_value = a.get("numericValue") or 0
        if numeric_value <= 0:
            continue
        opportunities.append({
            "title": a.get("title"), "display_value": a.get("displayValue") or "",
            "numeric_value": numeric_value,
        })
    opportunities.sort(key=lambda o: o["numeric_value"], reverse=True)

    if _cat_score("performance") is None:
        return _empty_result("응답은 왔지만 성능 점수가 비어 있습니다")

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
        "opportunities": opportunities[:3],
        "field_data": field_data,
    }


def collect_pagespeed(url, api_key=None, strategy="mobile"):
    """
    url: 분석할 페이지 주소
    strategy: "mobile" 또는 "desktop" — 모바일 우선이 기본(구글 검색도 모바일 우선 색인)
    항상 실제 API를 호출한다. api_key가 없어도 쿼터만 낮을 뿐 호출은 그대로 시도한다.

    반환 source: "LIVE"(풀 감사 성공) / "LIVE_LITE"(성능만 성공, 나머지 카테고리는 이번엔
    못 받음) / "LAB_FAILED"(랩 감사는 실패했지만 CrUX 실측 데이터는 확보) / "ERROR:...".
    """
    full = _run_lighthouse(url, api_key, strategy, ["performance", "seo", "accessibility", "best-practices"],
                            timeout=90, attempts=1)
    if full["source"] == "LIVE":
        return full

    lite = _run_lighthouse(url, api_key, strategy, ["performance"], timeout=35, attempts=2)
    if lite["source"] == "LIVE":
        lite["source"] = "LIVE_LITE"
        lite["detail"] = "접근성·권장사항·SEO 세부 점수는 이번엔 측정하지 못해 핵심 성능 점수만 표시합니다."
        return lite

    crux = query_crux(url, api_key)
    if crux:
        result = _empty_result(lite.get("detail") or full.get("detail") or "정밀 감사 실패")
        result["source"] = "LAB_FAILED"
        result["field_data"] = crux
        return result

    return lite
