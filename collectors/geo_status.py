"""
"지금 이 사이트에 실제로 뭐가 있는지" 진단.
generators/artifacts.py는 우리가 처음부터 만드는 권장안이고, 이 파일은 그 반대 —
robots.txt/llms.txt/sitemap.xml을 실제로 가져와서 있는지, AI 크롤러를 막고 있진 않은지,
JSON-LD가 실제로 박혀 있는지를 있는 그대로 확인한다. mock 없음 — 실패하면 명확히 표시.
"""

import json
import xml.etree.ElementTree as ET
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from generators.artifacts import AI_CRAWLERS

USER_AGENT = "InternalSEOBot/1.0 (+internal use)"
TIMEOUT = 15


def _origin(url):
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def check_robots_txt(url):
    origin = _origin(url)
    robots_url = f"{origin}/robots.txt"
    try:
        resp = requests.get(robots_url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    except Exception as e:
        return {"exists": None, "url": robots_url, "error": str(e), "bots": [], "raw": ""}

    if resp.status_code == 404:
        return {"exists": False, "url": robots_url, "bots": [], "raw": ""}
    if not resp.ok:
        return {"exists": None, "url": robots_url, "error": f"HTTP {resp.status_code}", "bots": [], "raw": ""}

    raw = resp.text
    rp = RobotFileParser()
    rp.parse(raw.splitlines())

    bots = []
    for bot, desc in AI_CRAWLERS:
        allowed = rp.can_fetch(bot, origin + "/")
        bots.append({"bot": bot, "desc": desc, "allowed": allowed})

    return {"exists": True, "url": robots_url, "bots": bots, "raw": raw}


def check_llms_txt(url):
    origin = _origin(url)
    llms_url = f"{origin}/llms.txt"
    try:
        resp = requests.get(llms_url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    except Exception as e:
        return {"exists": None, "url": llms_url, "error": str(e), "content": ""}

    if resp.status_code == 404:
        return {"exists": False, "url": llms_url, "content": ""}
    if not resp.ok:
        return {"exists": None, "url": llms_url, "error": f"HTTP {resp.status_code}", "content": ""}

    return {"exists": True, "url": llms_url, "content": resp.text}


def check_sitemap(url):
    origin = _origin(url)
    sitemap_url = f"{origin}/sitemap.xml"
    try:
        resp = requests.get(sitemap_url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    except Exception as e:
        return {"exists": None, "url": sitemap_url, "error": str(e), "url_count": None, "is_index": False}

    if resp.status_code == 404:
        return {"exists": False, "url": sitemap_url, "url_count": None, "is_index": False}
    if not resp.ok:
        return {"exists": None, "url": sitemap_url, "error": f"HTTP {resp.status_code}", "url_count": None, "is_index": False}

    try:
        root = ET.fromstring(resp.content)
        root_tag = root.tag.rsplit("}", 1)[-1]
        is_index = root_tag == "sitemapindex"
        child_tag = "sitemap" if is_index else "url"
        url_count = sum(1 for child in root if child.tag.rsplit("}", 1)[-1] == child_tag)
    except Exception:
        url_count = None
        is_index = False

    return {"exists": True, "url": sitemap_url, "url_count": url_count, "is_index": is_index}


def extract_existing_jsonld(html):
    """이미 크롤링해둔 페이지 HTML에서 실제로 박혀 있는 JSON-LD를 그대로 뽑는다."""
    soup = BeautifulSoup(html, "html.parser")
    blocks = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = (tag.string or "").strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
            types = []
            items = parsed if isinstance(parsed, list) else [parsed]
            for it in items:
                if isinstance(it, dict) and it.get("@type"):
                    t = it["@type"]
                    types.append(t if isinstance(t, str) else ",".join(t))
            blocks.append({"raw": raw, "types": types, "valid": True})
        except Exception:
            blocks.append({"raw": raw, "types": [], "valid": False})
    return blocks


def check_current_geo_status(url, html):
    """robots/llms/sitemap/JSON-LD 실제 상태를 한 번에 모아서 반환."""
    return {
        "robots": check_robots_txt(url),
        "llms": check_llms_txt(url),
        "sitemap": check_sitemap(url),
        "jsonld": extract_existing_jsonld(html),
    }
