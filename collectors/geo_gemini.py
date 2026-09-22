"""
Gemini API(Google Search grounding) 기반 AI 노출 추적.
정해둔 프롬프트를 Gemini에 실제로 던지고, 응답 텍스트에서 브랜드/경쟁사 언급을,
grounding 메타데이터(구조화된 인용 출처)에서 실제 인용 URL을 뽑아낸다.
mock 없음 — 실패하면 가짜 값 대신 명확한 ERROR 상태를 반환한다.

키 발급(무료, 카드 불필요): aistudio.google.com/apikey
"""

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlparse

from collectors._http import request_with_retry

ENDPOINT_TMPL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _domain(url):
    return (urlparse(url).netloc or url).lower().lstrip("www.")


def guess_brand_name(tech):
    """
    <title>은 보통 "브랜드 | 부가설명 | ..." 형태라 전체를 그대로 브랜드명으로 쓰면
    AI 응답 텍스트와 거의 매칭이 안 된다. 구분자 앞부분만 브랜드로 추정한다.
    """
    title = (tech.get("title") or "").strip()
    for sep in ("|", " - ", "–", "·", ":"):
        if sep in title:
            return title.split(sep)[0].strip()
    return title


def query_gemini(prompt_text, api_key, model="gemini-flash-latest"):
    """
    Gemini에 프롬프트 1개를 실제로 던지고 (Google Search grounding 활성화),
    응답 텍스트와 인용 URL 목록을 반환한다. 실패 시 예외를 그대로 올린다.
    """
    url = ENDPOINT_TMPL.format(model=model)
    body = {
        "contents": [{"parts": [{"text": prompt_text}]}],
        "tools": [{"google_search": {}}],
    }
    # 쿼리파라미터(?key=)가 아니라 헤더로 인증해야 한다 — 안 그러면 401/404가 난다.
    headers = {"Content-Type": "application/json", "X-goog-api-key": api_key}
    # 503(서버 일시 장애)이 종종 나서 짧게 재시도한다.
    resp = request_with_retry("POST", url, headers=headers, json=body, timeout=30)
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


def _name_variants(name, domain):
    """
    브랜드명 하나만 정확히 일치시키면 재현율이 너무 낮다(제목 전체 vs AI가 짧게 부르는 이름).
    이름 전체, 이름의 첫 단어, 도메인의 대표 이름(예: kma.or.kr -> kma)까지 후보로 본다.
    """
    variants = set()
    name = (name or "").strip()
    if name:
        variants.add(name.lower())
        first_word = name.split()[0] if " " in name else name
        if len(first_word) >= 2:
            variants.add(first_word.lower())
    if domain:
        bare = _domain(domain).split(".")[0]
        if len(bare) >= 2:
            variants.add(bare.lower())
    return variants


def detect_mentions(text, cited_urls, brand_name, brand_domain, competitors=None):
    """
    text: Gemini 응답 텍스트
    cited_urls: grounding에서 뽑힌 인용 URL 목록
    competitors: [{"name": str, "domain": str}, ...]
    """
    competitors = competitors or []
    text_low = text.lower()
    cited_domains = [_domain(u) for u in cited_urls]

    brand_terms = _name_variants(brand_name, brand_domain)
    mentioned = any(t in text_low for t in brand_terms)
    cited = bool(brand_domain) and _domain(brand_domain) in cited_domains

    competitor_mentions = {}
    competitor_citations = {}
    for c in competitors:
        name, domain = c.get("name", ""), c.get("domain", "")
        terms = _name_variants(name, domain)
        competitor_mentions[name or domain] = any(t in text_low for t in terms)
        competitor_citations[name or domain] = bool(domain) and _domain(domain) in cited_domains

    return {
        "mentioned": mentioned,
        "cited": cited,
        "competitor_mentions": competitor_mentions,
        "competitor_citations": competitor_citations,
    }


def generate_prompts(tech, api_key, model="gemini-flash-latest", count=5):
    """
    크롤링된 사이트 정보(title/meta_desc/h1_texts)를 바탕으로, 이 사이트의 잠재 고객이
    AI 챗봇에게 물어볼 법한 자연어 질문을 Gemini로 자동 생성한다 (grounding 없이 순수 생성).
    실패 시 예외를 그대로 올린다 — 가짜 프롬프트로 대체하지 않는다.
    """
    site_desc = (
        f"제목: {tech.get('title', '')}\n"
        f"설명: {tech.get('meta_desc', '')}\n"
        f"주요 페이지 제목: {', '.join(tech.get('h1_texts') or [])}"
    )
    ask = (
        f"다음은 한 웹사이트 정보입니다.\n{site_desc}\n\n"
        f"이 사이트의 잠재 고객이 AI 챗봇에게 물어볼 법한 자연어 질문을 정확히 {count}개 만들어줘. "
        "브랜드명이나 회사명은 절대 포함하지 말고, 일반적인 니즈·비교·추천 요청 형태로 만들어줘. "
        "각 질문을 한 줄에 하나씩, 번호나 다른 텍스트 없이 질문 문장만 출력해."
    )
    url = ENDPOINT_TMPL.format(model=model)
    body = {"contents": [{"parts": [{"text": ask}]}]}
    headers = {"Content-Type": "application/json", "X-goog-api-key": api_key}
    resp = request_with_retry("POST", url, headers=headers, json=body, timeout=30)
    data = resp.json()

    candidates = data.get("candidates") or []
    if not candidates:
        raise ValueError("프롬프트 생성 응답이 비어 있습니다 (안전 필터 차단 가능성)")

    parts = candidates[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts if "text" in p)

    lines = []
    for ln in text.split("\n"):
        ln = re.sub(r"^[\d\.\-\)\s]+", "", ln).strip()
        if ln:
            lines.append(ln)
    if not lines:
        raise ValueError("생성된 프롬프트를 파싱하지 못했습니다")
    return lines[:count]


def _run_one(prompt, api_key, model, brand_name, brand_domain, competitors):
    try:
        text, cited_urls = query_gemini(prompt, api_key, model=model)
        m = detect_mentions(text, cited_urls, brand_name, brand_domain, competitors)
        return {
            "prompt": prompt,
            "status": "LIVE",
            "mentioned": m["mentioned"],
            "cited": m["cited"],
            "cited_urls": cited_urls,
            "competitor_mentions": m["competitor_mentions"],
            "competitor_citations": m["competitor_citations"],
            "answer_preview": text[:300],
            "detail": "",
        }
    except Exception as e:
        return {
            "prompt": prompt,
            "status": f"ERROR:{type(e).__name__}",
            "mentioned": None,
            "cited": None,
            "cited_urls": [],
            "competitor_mentions": {},
            "competitor_citations": {},
            "answer_preview": "",
            "detail": str(e),
        }


def run_geo_visibility(prompts, api_key, model, brand_name, brand_domain, competitors=None):
    """
    prompts: 추적할 질문 문자열 리스트
    반환: 프롬프트별 결과 레코드 리스트. 실패한 프롬프트는 mentioned/cited가 모두 None이고
    status가 "ERROR:..."로 시작한다 — 가짜 값으로 채우지 않는다.
    각 프롬프트는 서로 독립적인 API 호출이라 동시에 실행해서 대기시간을 줄인다.
    """
    if not prompts:
        records = []
    elif len(prompts) == 1:
        records = [_run_one(prompts[0], api_key, model, brand_name, brand_domain, competitors)]
    else:
        with ThreadPoolExecutor(max_workers=len(prompts)) as ex:
            records = list(ex.map(
                lambda p: _run_one(p, api_key, model, brand_name, brand_domain, competitors), prompts
            ))
    return {
        "platform": "gemini",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "records": records,
    }
