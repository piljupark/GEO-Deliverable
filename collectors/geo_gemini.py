"""
Gemini API(Google Search grounding) 기반 AI 노출 추적.
정해둔 프롬프트를 Gemini에 실제로 던지고, 응답 텍스트에서 브랜드/경쟁사 언급을,
grounding 메타데이터(구조화된 인용 출처)에서 실제 인용 URL을 뽑아낸다.
mock 없음 — 실패하면 가짜 값 대신 명확한 ERROR 상태를 반환한다.

키 발급(무료, 카드 불필요): aistudio.google.com/apikey
"""

import re
import requests
from datetime import datetime, timezone
from urllib.parse import urlparse

ENDPOINT_TMPL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _domain(url):
    return (urlparse(url).netloc or url).lower().lstrip("www.")


def query_gemini(prompt_text, api_key, model="gemini-2.5-flash"):
    """
    Gemini에 프롬프트 1개를 실제로 던지고 (Google Search grounding 활성화),
    응답 텍스트와 인용 URL 목록을 반환한다. 실패 시 예외를 그대로 올린다.
    """
    url = ENDPOINT_TMPL.format(model=model)
    body = {
        "contents": [{"parts": [{"text": prompt_text}]}],
        "tools": [{"google_search": {}}],
    }
    resp = requests.post(url, params={"key": api_key}, json=body, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    candidates = data.get("candidates") or []
    if not candidates:
        raise ValueError("응답에 candidates가 없습니다 (안전 필터 차단 가능성)")

    candidate = candidates[0]
    parts = candidate.get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts if "text" in p)

    grounding = candidate.get("groundingMetadata", {}) or {}
    chunks = grounding.get("groundingChunks", []) or []
    cited_urls = [c["web"]["uri"] for c in chunks if c.get("web", {}).get("uri")]

    return text, cited_urls


def detect_mentions(text, cited_urls, brand_name, brand_domain, competitors=None):
    """
    text: Gemini 응답 텍스트
    cited_urls: grounding에서 뽑힌 인용 URL 목록
    competitors: [{"name": str, "domain": str}, ...]
    """
    competitors = competitors or []
    text_low = text.lower()
    cited_domains = [_domain(u) for u in cited_urls]

    mentioned = bool(brand_name) and brand_name.lower() in text_low
    cited = bool(brand_domain) and _domain(brand_domain) in cited_domains

    competitor_mentions = {}
    competitor_citations = {}
    for c in competitors:
        name, domain = c.get("name", ""), c.get("domain", "")
        competitor_mentions[name or domain] = bool(name) and name.lower() in text_low
        competitor_citations[name or domain] = bool(domain) and _domain(domain) in cited_domains

    return {
        "mentioned": mentioned,
        "cited": cited,
        "competitor_mentions": competitor_mentions,
        "competitor_citations": competitor_citations,
    }


def run_geo_visibility(prompts, api_key, model, brand_name, brand_domain, competitors=None):
    """
    prompts: 추적할 질문 문자열 리스트
    반환: 프롬프트별 결과 레코드 리스트. 실패한 프롬프트는 mentioned/cited가 모두 None이고
    status가 "ERROR:..."로 시작한다 — 가짜 값으로 채우지 않는다.
    """
    records = []
    for prompt in prompts:
        try:
            text, cited_urls = query_gemini(prompt, api_key, model=model)
            m = detect_mentions(text, cited_urls, brand_name, brand_domain, competitors)
            records.append({
                "prompt": prompt,
                "status": "LIVE",
                "mentioned": m["mentioned"],
                "cited": m["cited"],
                "cited_urls": cited_urls,
                "competitor_mentions": m["competitor_mentions"],
                "competitor_citations": m["competitor_citations"],
                "answer_preview": text[:300],
                "detail": "",
            })
        except Exception as e:
            records.append({
                "prompt": prompt,
                "status": f"ERROR:{type(e).__name__}",
                "mentioned": None,
                "cited": None,
                "cited_urls": [],
                "competitor_mentions": {},
                "competitor_citations": {},
                "answer_preview": "",
                "detail": str(e),
            })
    return {
        "platform": "gemini",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "records": records,
    }
