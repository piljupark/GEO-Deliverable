"""
GEO 산출물 생성기: robots.txt / llms.txt / JSON-LD
전부 규칙 기반(결정론적) 조립 — LLM 호출 없음. 문법이 정확해야 하는 산출물이라
tech_audit.py가 크롤해둔 실제 데이터(title, meta_desc, h1, schema 등)를 재료로 조립만 한다.
"""

from datetime import datetime, timezone
from urllib.parse import urlparse

# 주요 AI 크롤러 — 정적 목록(2026년 기준 알려진 것들). 주기적으로 갱신 필요.
AI_CRAWLERS = [
    ("GPTBot", "OpenAI 학습/검색용"),
    ("OAI-SearchBot", "OpenAI 검색 색인용"),
    ("ChatGPT-User", "ChatGPT 브라우징 기능용"),
    ("ClaudeBot", "Anthropic 학습용"),
    ("Claude-Web", "Claude 브라우징 기능용"),
    ("anthropic-ai", "Anthropic 학습용(구버전 표기)"),
    ("PerplexityBot", "Perplexity 검색용"),
    ("Google-Extended", "Google Gemini/AI Overview 학습용"),
    ("Googlebot", "구글 일반 색인용"),
    ("Bingbot", "빙/코파일럿용"),
    ("CCBot", "Common Crawl (여러 LLM 학습 데이터 소스)"),
]


def generate_robots_txt(site_url, allow_all_ai=True, sitemap_url=None):
    """AI 크롤러를 명시적으로 허용하는 robots.txt. allow_all_ai=False면 차단 버전."""
    domain = urlparse(site_url).netloc or site_url
    lines = [f"# {domain} — AI 크롤러 노출 설정", "# 생성: Signal (자동 생성, 필요시 직접 수정)", ""]

    directive = "Allow: /" if allow_all_ai else "Disallow: /"
    for bot, desc in AI_CRAWLERS:
        lines.append(f"# {desc}")
        lines.append(f"User-agent: {bot}")
        lines.append(directive)
        lines.append("")

    lines.append("# 기타 모든 봇")
    lines.append("User-agent: *")
    lines.append("Allow: /")
    lines.append("")

    if sitemap_url:
        lines.append(f"Sitemap: {sitemap_url}")
    else:
        lines.append(f"Sitemap: https://{domain}/sitemap.xml")

    return "\n".join(lines)


def generate_llms_txt(tech, brand_name=None):
    """
    llms.txt — LLM이 사이트를 빠르게 이해하도록 돕는 마크다운 요약.
    tech: tech_audit.audit_technical() 결과 (title, meta_desc, h1_texts, url 등)
    """
    domain = urlparse(tech["url"]).netloc
    name = brand_name or domain
    title = tech.get("title") or name
    desc = tech.get("meta_desc") or "설명이 아직 없습니다 — meta description을 채우면 여기 반영됩니다."
    h1s = tech.get("h1_texts") or []

    lines = [f"# {name}", "", f"> {desc}", ""]

    if h1s:
        lines.append("## 핵심 페이지 주제")
        for h in h1s:
            lines.append(f"- {h}")
        lines.append("")

    lines.append("## 사이트 정보")
    lines.append(f"- 공식 URL: {tech['url']}")
    lines.append(f"- 페이지 제목: {title}")
    if tech.get("schema_types"):
        lines.append(f"- 구조화 데이터: {', '.join(tech['schema_types'])}")
    lines.append("")
    lines.append("<!-- Signal이 크롤 데이터를 기반으로 자동 생성. 페이지가 늘어나면 섹션을 추가하세요. -->")

    return "\n".join(lines)


def generate_json_ld(tech, brand_name=None, social_urls=None):
    """
    Organization + WebSite 구조화 데이터. schema.org 문법을 지키기 위해 dict를 직접 조립.
    social_urls: sameAs에 넣을 SNS 링크 리스트 (없으면 생략)
    """
    domain = urlparse(tech["url"]).netloc
    name = brand_name or domain
    org_id = f"{tech['url'].rstrip('/')}/#organization"

    org = {
        "@type": "Organization",
        "@id": org_id,
        "name": name,
        "url": tech["url"],
    }
    if tech.get("meta_desc"):
        org["description"] = tech["meta_desc"]
    if social_urls:
        org["sameAs"] = social_urls

    website = {
        "@type": "WebSite",
        "@id": f"{tech['url'].rstrip('/')}/#website",
        "url": tech["url"],
        "name": name,
        "publisher": {"@id": org_id},
    }

    graph = {"@context": "https://schema.org", "@graph": [org, website]}
    return graph


def generate_all(tech, brand_name=None, social_urls=None, allow_all_ai=True):
    """세 산출물을 한 번에 생성."""
    import json
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "robots_txt": generate_robots_txt(tech["url"], allow_all_ai=allow_all_ai),
        "llms_txt": generate_llms_txt(tech, brand_name=brand_name),
        "json_ld": json.dumps(generate_json_ld(tech, brand_name=brand_name, social_urls=social_urls),
                               ensure_ascii=False, indent=2),
    }
