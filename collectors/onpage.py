"""
온페이지 SEO 크롤러
- 지정한 도메인의 페이지들을 크롤링해 SEO 지표를 수집한다.
- 외부 의존: requests, beautifulsoup4  (렌더링이 필요하면 Playwright 버전 주석 참고)
"""

import re
import time
import json
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

USER_AGENT = "InternalSEOBot/1.0 (+internal use)"
TIMEOUT = 15


def _clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def analyze_page(url, html, status_code, elapsed_ms):
    """단일 페이지 HTML에서 온페이지 지표를 뽑아낸다."""
    soup = BeautifulSoup(html, "html.parser")

    title = _clean(soup.title.string if soup.title else "")
    meta_desc = ""
    md = soup.find("meta", attrs={"name": "description"})
    if md:
        meta_desc = _clean(md.get("content", ""))

    canonical = ""
    cn = soup.find("link", attrs={"rel": "canonical"})
    if cn:
        canonical = cn.get("href", "")

    robots = ""
    rb = soup.find("meta", attrs={"name": "robots"})
    if rb:
        robots = _clean(rb.get("content", ""))

    h1s = [_clean(h.get_text()) for h in soup.find_all("h1")]
    h2s = [_clean(h.get_text()) for h in soup.find_all("h2")]

    imgs = soup.find_all("img")
    imgs_missing_alt = sum(1 for i in imgs if not i.get("alt"))

    # 스키마(JSON-LD) 타입 수집
    schema_types = []
    for s in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(s.string or "{}")
            items = data if isinstance(data, list) else [data]
            for it in items:
                t = it.get("@type")
                if t:
                    schema_types.append(t if isinstance(t, str) else ",".join(t))
        except Exception:
            pass

    # 링크 분류
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    internal, external = set(), set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        full = urljoin(url, href)
        if urlparse(full).netloc == parsed.netloc:
            internal.add(full)
        else:
            external.add(full)

    word_count = len(_clean(soup.get_text()).split())

    # 간단한 이슈 룰
    issues = []
    if not title:
        issues.append(("error", "title 태그 없음"))
    elif len(title) > 60:
        issues.append(("warn", f"title 60자 초과 ({len(title)}자)"))
    elif len(title) < 15:
        issues.append(("warn", f"title 너무 짧음 ({len(title)}자)"))
    if not meta_desc:
        issues.append(("warn", "meta description 없음"))
    elif len(meta_desc) > 160:
        issues.append(("warn", f"meta description 160자 초과 ({len(meta_desc)}자)"))
    if len(h1s) == 0:
        issues.append(("error", "H1 없음"))
    elif len(h1s) > 1:
        issues.append(("warn", f"H1 여러 개 ({len(h1s)}개)"))
    if imgs and imgs_missing_alt:
        issues.append(("warn", f"alt 없는 이미지 {imgs_missing_alt}/{len(imgs)}"))
    if not canonical:
        issues.append(("warn", "canonical 없음"))
    if word_count < 300:
        issues.append(("warn", f"콘텐츠 빈약 ({word_count} 단어)"))
    if status_code >= 400:
        issues.append(("error", f"HTTP {status_code}"))

    return {
        "url": url,
        "status_code": status_code,
        "load_ms": round(elapsed_ms),
        "title": title,
        "title_len": len(title),
        "meta_description": meta_desc,
        "meta_desc_len": len(meta_desc),
        "canonical": canonical,
        "robots": robots,
        "h1": h1s,
        "h2_count": len(h2s),
        "img_count": len(imgs),
        "img_missing_alt": imgs_missing_alt,
        "schema_types": sorted(set(schema_types)),
        "internal_links": len(internal),
        "external_links": len(external),
        "word_count": word_count,
        "issues": issues,
        "_internal_urls": list(internal),  # 크롤 큐용, 리포트에선 제거
    }


def _fetch_page(session, url):
    try:
        t0 = time.time()
        resp = session.get(url, timeout=TIMEOUT, allow_redirects=True)
        elapsed = (time.time() - t0) * 1000
        ctype = resp.headers.get("Content-Type", "")
        if "text/html" not in ctype:
            return None
        return analyze_page(url, resp.text, resp.status_code, elapsed)
    except Exception as e:
        return {
            "url": url, "status_code": 0, "load_ms": 0, "title": "",
            "issues": [("error", f"요청 실패: {type(e).__name__}")],
            "_internal_urls": [], "h1": [], "schema_types": [],
            "title_len": 0, "meta_desc_len": 0, "word_count": 0,
            "img_count": 0, "img_missing_alt": 0, "h2_count": 0,
            "internal_links": 0, "external_links": 0, "meta_description": "",
            "canonical": "", "robots": "",
        }


def crawl_site(start_url, max_pages=25, delay=0.2, max_workers=5):
    """같은 도메인 내에서 BFS로 크롤링. 한 번에 한 페이지씩 순서대로 받아오면 대상
    사이트 하나 crawl하는 데만 페이지당 왕복시간 x 개수가 그대로 걸려서, 레벨(웨이브)
    단위로 여러 페이지를 동시에 가져온다 — 15페이지면 순서대로 15번 왕복하는 대신
    5개씩 3번 왕복하는 정도로 끝난다. delay는 다음 웨이브로 넘어가기 전에만 적용해서
    대상 서버에 순간적으로 너무 많은 요청이 몰리는 걸 조금 눅여준다."""
    parsed = urlparse(start_url)
    root_netloc = parsed.netloc

    seen = {start_url}
    frontier = [start_url]
    results = []
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        while frontier and len(results) < max_pages:
            batch = frontier[:max_pages - len(results)]
            frontier = frontier[len(batch):]
            pages = list(ex.map(lambda u: _fetch_page(session, u), batch))

            next_frontier = []
            for page in pages:
                if page is None:
                    continue
                for link in page.pop("_internal_urls", []):
                    if link not in seen and urlparse(link).netloc == root_netloc:
                        seen.add(link)
                        next_frontier.append(link)
                results.append(page)
            frontier.extend(next_frontier)

            if frontier and len(results) < max_pages and delay:
                time.sleep(delay)

    return {
        "domain": root_netloc,
        "crawled_at": datetime.now(timezone.utc).isoformat(),
        "pages": results[:max_pages],
    }


# ---- Playwright(JS 렌더링) 버전이 필요하면 아래를 참고 ----
# from playwright.sync_api import sync_playwright
# def fetch_rendered(url):
#     with sync_playwright() as p:
#         browser = p.chromium.launch()
#         page = browser.new_page(user_agent=USER_AGENT)
#         page.goto(url, wait_until="networkidle", timeout=30000)
#         html = page.content()
#         browser.close()
#     return html
