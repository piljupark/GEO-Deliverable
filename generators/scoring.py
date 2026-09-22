"""
tech_audit 결과를 카테고리별 0~100 점수로 환산.
규칙 기반(체크리스트 통과 비율) — 판단 근거를 항상 설명할 수 있게 단순하게 유지.
"""


def score_categories(tech):
    access_checks = [
        tech["h1_count"] > 0,
        tech["has_canonical"],
        tech["has_lang"],
        tech["has_viewport"],
        "noindex" not in (tech.get("robots_meta") or "").lower(),
    ]
    access_score = round(sum(access_checks) / len(access_checks) * 100)

    content_checks = [
        10 <= tech["title_len"] <= 60,
        50 <= tech["meta_desc_len"] <= 160,
        (tech["img_no_alt_pct"] < 20) if tech["img_total"] > 0 else True,
        tech["semantic_total"] > 0,
    ]
    content_score = round(sum(content_checks) / len(content_checks) * 100)

    brand_checks = [
        tech["schema_count"] > 0,
        tech["og_count"] > 0,
        tech["twitter_count"] > 0,
    ]
    brand_score = round(sum(brand_checks) / len(brand_checks) * 100)

    return {
        "access": {"score": access_score, "label": "검색·AI 접근"},
        "content": {"score": content_score, "label": "콘텐츠 품질"},
        "brand": {"score": brand_score, "label": "브랜드·구조화"},
    }


def score_tier(score):
    if score >= 80:
        return "양호"
    if score >= 50:
        return "보완 필요"
    return "우선 보완"
