"""
프론트엔드 기술 SEO 심화 진단.
onpage.analyze_page 보다 깊게 — 시맨틱 구조, 이미지 최적화, 메타/소셜, 구조화 데이터를
프레임워크(Next/Nuxt 등) 사이트 관점에서 점검한다.

BeautifulSoup 파싱 결과를 받아 '기술 진단 팩트'를 뽑는다. 처방(To-Do)은 prescribe.py 담당.
"""

import re
from urllib.parse import urlparse
from bs4 import BeautifulSoup


SEMANTIC_TAGS = ["header", "nav", "main", "article", "section", "aside", "footer"]


def audit_technical(url, html):
    """단일 페이지의 기술적 SEO 상태를 상세 진단."""
    soup = BeautifulSoup(html, "html.parser")

    # ---- 시맨틱 구조 ----
    div_count = len(soup.find_all("div"))
    semantic_counts = {t: len(soup.find_all(t)) for t in SEMANTIC_TAGS}
    semantic_total = sum(semantic_counts.values())

    headings = {f"h{i}": len(soup.find_all(f"h{i}")) for i in range(1, 7)}
    h1_texts = [h.get_text(strip=True) for h in soup.find_all("h1")]

    # heading 위계 점검: h1 다음 h3로 건너뛰는지 등
    heading_seq = []
    for h in soup.find_all(re.compile(r"^h[1-6]$")):
        heading_seq.append(int(h.name[1]))
    hierarchy_skips = 0
    for i in range(1, len(heading_seq)):
        if heading_seq[i] - heading_seq[i-1] > 1:  # 예: h2 다음 h4
            hierarchy_skips += 1

    # div soup 지표: 시맨틱 태그 대비 div 비율
    div_ratio = round(div_count / semantic_total, 1) if semantic_total else div_count

    # ---- 이미지 ----
    imgs = soup.find_all("img")
    img_total = len(imgs)
    img_no_alt = sum(1 for i in imgs if not i.get("alt", "").strip())
    img_lazy = sum(1 for i in imgs if i.get("loading") == "lazy")
    img_next = sum(1 for i in imgs if "/_next/image" in (i.get("src", "") or ""))  # Next Image 최적화
    # 확장자 기준 포맷
    img_modern = 0
    for i in imgs:
        src = (i.get("src", "") or "").lower()
        if ".webp" in src or ".avif" in src or "/_next/image" in src:
            img_modern += 1

    # ---- 메타 / 소셜 ----
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    meta_desc = ""
    md = soup.find("meta", attrs={"name": "description"})
    if md:
        meta_desc = (md.get("content") or "").strip()

    canonical = ""
    cn = soup.find("link", attrs={"rel": "canonical"})
    if cn:
        canonical = cn.get("href", "")

    robots_meta = ""
    rb = soup.find("meta", attrs={"name": "robots"})
    if rb:
        robots_meta = (rb.get("content") or "").strip()

    og_tags = soup.find_all("meta", attrs={"property": re.compile(r"^og:")})
    twitter_tags = soup.find_all("meta", attrs={"name": re.compile(r"^twitter:")})
    viewport = soup.find("meta", attrs={"name": "viewport"}) is not None
    lang = soup.find("html")
    has_lang = bool(lang and lang.get("lang")) if lang else False

    # ---- 구조화 데이터 (JSON-LD) ----
    import json as _json
    schema_types = []
    for s in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = _json.loads(s.string or "{}")
            items = data if isinstance(data, list) else [data]
            for it in items:
                if isinstance(it, dict) and it.get("@type"):
                    t = it["@type"]
                    schema_types.append(t if isinstance(t, str) else ",".join(t))
        except Exception:
            pass

    # ---- 프레임워크 감지 ----
    framework = "unknown"
    if "/_next/" in html or "__NEXT_DATA__" in html:
        framework = "Next.js"
    elif "__NUXT__" in html or "/_nuxt/" in html:
        framework = "Nuxt"
    elif "data-reactroot" in html or 'id="root"' in html:
        framework = "React (CSR 가능성)"
    elif 'id="app"' in html:
        framework = "Vue/SPA 가능성"

    return {
        "url": url,
        "framework": framework,
        # 시맨틱
        "div_count": div_count,
        "semantic_counts": semantic_counts,
        "semantic_total": semantic_total,
        "div_ratio": div_ratio,
        "headings": headings,
        "h1_count": headings["h1"],
        "h1_texts": h1_texts[:5],
        "hierarchy_skips": hierarchy_skips,
        # 이미지
        "img_total": img_total,
        "img_no_alt": img_no_alt,
        "img_no_alt_pct": round(img_no_alt / img_total * 100) if img_total else 0,
        "img_lazy": img_lazy,
        "img_modern": img_modern,
        "img_modern_pct": round(img_modern / img_total * 100) if img_total else 0,
        # 메타/소셜
        "title": title,
        "title_len": len(title),
        "meta_desc": meta_desc,
        "meta_desc_len": len(meta_desc),
        "canonical": canonical,
        "has_canonical": bool(canonical),
        "robots_meta": robots_meta,
        "og_count": len(og_tags),
        "twitter_count": len(twitter_tags),
        "has_viewport": viewport,
        "has_lang": has_lang,
        # 구조화 데이터
        "schema_types": sorted(set(schema_types)),
        "schema_count": len(set(schema_types)),
    }
