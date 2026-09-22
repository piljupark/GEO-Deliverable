"""
경쟁사 온페이지 기술 비교.
공개된 각 사이트의 대표 페이지(홈 등)를 크롤해 SEO 기술 지표를 나란히 비교한다.
검색 트래픽·키워드 순위는 상대 GSC가 필요하므로 여기서 다루지 않음 (유료 서드파티 영역).
비교하는 것은 '공개 HTML로 확인 가능한 기술적 요소'뿐.
"""

import time
import requests
from urllib.parse import urlparse

from .onpage import analyze_page, USER_AGENT, TIMEOUT


def _score_page(p):
    """페이지 하나의 기술 점수 (100 - 감점). onpage 이슈 기반."""
    pen = 0
    for sev, _ in p.get("issues", []):
        pen += 8 if sev == "error" else 3
    return max(0, min(100, 100 - pen))


def audit_one(url):
    """단일 사이트 대표 페이지 크롤 → 비교용 지표 추출."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    try:
        t0 = time.time()
        resp = session.get(url, timeout=TIMEOUT, allow_redirects=True)
        elapsed = (time.time() - t0) * 1000
        p = analyze_page(resp.url, resp.text, resp.status_code, elapsed)
        p.pop("_internal_urls", None)
    except Exception as e:
        return {
            "url": url, "domain": urlparse(url).netloc, "reachable": False,
            "error": type(e).__name__, "score": 0,
        }

    return {
        "url": url,
        "domain": urlparse(url).netloc,
        "reachable": True,
        "score": _score_page(p),
        "load_ms": p.get("load_ms", 0),
        "title_len": p.get("title_len", 0),
        "meta_desc_len": p.get("meta_desc_len", 0),
        "h1_count": len(p.get("h1", [])),
        "word_count": p.get("word_count", 0),
        "img_count": p.get("img_count", 0),
        "img_missing_alt": p.get("img_missing_alt", 0),
        "schema_count": len(p.get("schema_types", [])),
        "schema_types": p.get("schema_types", []),
        "internal_links": p.get("internal_links", 0),
        "has_canonical": bool(p.get("canonical")),
        "issue_count": len(p.get("issues", [])),
    }


def compare_sites(my_url, competitor_urls, delay=0.5):
    """
    my_url: 내 사이트 대표 URL
    competitor_urls: 경쟁사 대표 URL 리스트
    반환: [{...my...}, {...comp1...}, ...]  (첫 항목이 항상 내 사이트, is_me=True)
    """
    results = []
    me = audit_one(my_url)
    me["is_me"] = True
    me["label"] = "내 사이트"
    results.append(me)
    time.sleep(delay)

    for i, u in enumerate(competitor_urls, 1):
        r = audit_one(u)
        r["is_me"] = False
        r["label"] = f"경쟁사 {i}"
        results.append(r)
        time.sleep(delay)

    return results
