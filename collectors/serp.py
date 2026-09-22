"""
SerpApi 연동 — 실제 구글 검색결과(SERP)에서 특정 키워드의 순위를 조회한다.
월 250회 무료(재충전). https://serpapi.com 에서 API 키 발급.

GSC는 '내 사이트'의 노출/클릭만 알려주고 경쟁사는 절대 못 보여준다.
이건 그 빈틈을 메우는 용도 — 실제 검색결과 화면에 지금 누가 몇 위인지.

무료 쿼터가 작으므로(월 250회) 호출은 필요한 키워드에만, 결과는 캐싱해서 재사용.
"""

import os
import json
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

CACHE_PATH_DEFAULT = "data/serp_cache.json"
CACHE_TTL_DAYS = 7  # 같은 키워드는 7일 이내 재조회 안 함 (쿼터 절약)


def _load_cache(path):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_cache(path, cache):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _is_fresh(entry):
    try:
        ts = datetime.fromisoformat(entry["fetched_at"])
        age_days = (datetime.now(timezone.utc) - ts).total_seconds() / 86400
        return age_days < CACHE_TTL_DAYS
    except Exception:
        return False


def _domain(url):
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return url


def _search_one(keyword, api_key, gl="kr", hl="ko", num=10):
    """SerpApi로 키워드 하나 조회 → 상위 num개 결과의 [순위, 도메인, 제목, url]."""
    import requests
    params = {
        "engine": "google",
        "q": keyword,
        "api_key": api_key,
        "gl": gl,        # 국가 (kr=한국)
        "hl": hl,        # 언어
        "num": num,
    }
    resp = requests.get("https://serpapi.com/search", params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    results = []
    for item in data.get("organic_results", [])[:num]:
        results.append({
            "rank": item.get("position", len(results) + 1),
            "domain": _domain(item.get("link", "")),
            "title": item.get("title", ""),
            "url": item.get("link", ""),
        })
    return results


def rank_keywords(keywords, my_domain, api_key=None, gl="kr", hl="ko",
                   cache_path=CACHE_PATH_DEFAULT, max_calls=50):
    """
    keywords: 조회할 검색어 리스트 (보통 '기회 키워드' 목록)
    my_domain: 내 도메인 (결과에서 내 위치 찾기용). 예: "studio.kma.or.kr"
    max_calls: 이번 실행에서 쓸 최대 API 호출 수 (쿼터 보호)

    반환: [{keyword, results:[...], my_rank, competitors:[...], source:"LIVE"|"CACHE"|"NO_KEY"}]
    """
    if not api_key:
        api_key = os.environ.get("SERPAPI_KEY")

    cache = _load_cache(cache_path)
    out = []
    calls_used = 0

    for kw in keywords:
        cached = cache.get(kw)
        if cached and _is_fresh(cached):
            entry = {**cached, "source": "CACHE"}
        elif not api_key:
            entry = {"keyword": kw, "results": [], "source": "NO_KEY",
                      "fetched_at": None}
        elif calls_used >= max_calls:
            entry = {"keyword": kw, "results": [], "source": "QUOTA_SKIPPED",
                      "fetched_at": None}
        else:
            try:
                results = _search_one(kw, api_key, gl=gl, hl=hl)
                entry = {
                    "keyword": kw,
                    "results": results,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "source": "LIVE",
                }
                cache[kw] = {k: v for k, v in entry.items() if k != "source"}
                calls_used += 1
                time.sleep(1)  # 레이트리밋 여유
            except Exception as e:
                entry = {"keyword": kw, "results": [], "source": f"ERROR:{type(e).__name__}",
                          "fetched_at": None}

        # 내 순위 / 경쟁사 도메인 정리
        my_rank = None
        competitors = []
        for r in entry.get("results", []):
            if my_domain in r["domain"]:
                my_rank = r["rank"]
            else:
                competitors.append(r)

        out.append({
            "keyword": kw,
            "results": entry.get("results", []),
            "my_rank": my_rank,
            "competitors": competitors[:5],
            "source": entry["source"],
        })

    _save_cache(cache_path, cache)
    return {
        "items": out,
        "calls_used": calls_used,
        "quota_note": f"이번 실행 API 호출 {calls_used}회 (무료 월 250회 중)",
    }
