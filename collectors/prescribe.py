"""
처방 엔진.
기술 진단(tech_audit) + GSC 인사이트(insights)를 받아,
비전문가도 실행 가능한 '우선순위 할 일 목록'을 생성한다.

각 항목:
  category: "tech" (🔧 프론트가 고침) | "content" (📊 콘텐츠/마케팅)
  impact:   1~3 (효과)   effort: 1~3 (난이도, 낮을수록 쉬움)
  priority: impact*2 - effort  로 정렬 (효과 크고 쉬운 것 먼저)
  title / why / how : 무엇을 / 왜 / 어떻게 (평이한 한국어)
"""


def _item(category, impact, effort, title, why, how, evidence=""):
    return {
        "category": category,
        "impact": impact,
        "effort": effort,
        "priority": impact * 2 - effort,
        "title": title,
        "why": why,
        "how": how,
        "evidence": evidence,
    }


def prescribe(tech=None, gsc=None, insights=None):
    """
    tech: audit_technical() 결과 (대표 페이지 1개 기준) 또는 None
    gsc: collect_gsc 결과 또는 None
    insights: build_insights 결과 또는 None
    """
    todos = []

    # ============ 기술 처방 (tech_audit 기반) ============
    if tech:
        # 1) H1 없음 — 가장 기본
        if tech["h1_count"] == 0:
            todos.append(_item(
                "tech", 3, 1,
                "페이지에 H1 제목 태그 추가하기",
                "H1은 검색엔진이 '이 페이지가 무슨 내용인지' 가장 먼저 보는 신호입니다. "
                "지금 H1이 하나도 없어서 페이지 주제가 불명확하게 전달됩니다.",
                "각 페이지의 핵심 제목을 <div>나 <span>이 아니라 <h1>으로 감싸세요. "
                "페이지당 H1은 딱 하나가 이상적입니다. Next.js면 해당 페이지 컴포넌트의 "
                "최상단 제목을 <h1>으로 바꾸면 됩니다.",
                f"현재 H1 {tech['h1_count']}개",
            ))
        elif tech["h1_count"] > 1:
            todos.append(_item(
                "tech", 1, 1,
                f"H1이 {tech['h1_count']}개 — 하나로 정리",
                "페이지당 H1은 하나가 원칙입니다. 여러 개면 주제가 분산돼 보입니다.",
                "가장 중요한 제목만 <h1>으로 두고 나머지는 <h2>로 낮추세요.",
                f"현재 H1 {tech['h1_count']}개",
            ))

        # 2) 시맨틱 태그 부재 (div soup)
        if tech["semantic_total"] == 0 and tech["div_count"] > 50:
            todos.append(_item(
                "tech", 3, 2,
                "시맨틱 태그로 문서 구조 만들기 (div soup 해소)",
                f"현재 div가 {tech['div_count']}개인데 header·main·section·article 같은 "
                "구조 태그는 0개입니다. 검색엔진은 이 태그들로 페이지의 뼈대를 이해하는데, "
                "지금은 전부 div라 구조를 파악하기 어렵습니다.",
                "주요 영역을 의미에 맞는 태그로 바꾸세요: 상단 메뉴 → <nav>, 본문 → <main>, "
                "독립적 콘텐츠 블록 → <section> 또는 <article>, 하단 → <footer>. "
                "레이아웃 컴포넌트(Header/Footer/Layout)부터 바꾸면 전 페이지에 한 번에 적용됩니다.",
                f"div {tech['div_count']}개 vs 시맨틱 {tech['semantic_total']}개",
            ))
        elif tech["div_ratio"] and tech["div_ratio"] > 40:
            todos.append(_item(
                "tech", 2, 2,
                "시맨틱 태그 비중 늘리기",
                f"div가 시맨틱 태그보다 {tech['div_ratio']}배 많습니다. 구조 태그를 더 쓰면 "
                "검색엔진이 콘텐츠 위계를 더 잘 이해합니다.",
                "반복되는 콘텐츠 블록을 <section>/<article>로, 목록성 영역을 <ul>/<li>로 바꾸세요.",
                f"div:시맨틱 = {tech['div_ratio']}:1",
            ))

        # 3) heading 위계 건너뜀
        if tech["hierarchy_skips"] > 0:
            todos.append(_item(
                "tech", 1, 1,
                "제목 태그 위계 정리 (h2 다음 h4 건너뛰기 등)",
                "제목 레벨을 건너뛰면(h2 → h4) 문서 구조가 논리적으로 어긋나 보입니다.",
                "제목은 h1 → h2 → h3 순서로 한 단계씩 내려가게 맞추세요.",
                f"위계 건너뜀 {tech['hierarchy_skips']}곳",
            ))

        # 4) 이미지 alt 누락
        if tech["img_total"] > 0 and tech["img_no_alt_pct"] >= 20:
            todos.append(_item(
                "tech", 2, 2,
                f"이미지 alt 텍스트 채우기 (누락 {tech['img_no_alt_pct']}%)",
                f"이미지 {tech['img_total']}개 중 {tech['img_no_alt']}개에 alt가 없습니다. "
                "alt는 이미지 검색 노출과 접근성(스크린리더)에 쓰이고, 없으면 그 기회를 놓칩니다.",
                "각 <img>에 이미지 내용을 설명하는 alt를 넣으세요. 장식용 이미지는 alt=\"\"로 "
                "명시적으로 비웁니다. Next.js <Image>도 alt는 필수 prop입니다.",
                f"{tech['img_no_alt']}/{tech['img_total']} 누락",
            ))

        # 5) 이미지 최적화(포맷/lazy)
        if tech["img_total"] > 20:
            if tech["img_modern_pct"] < 50:
                todos.append(_item(
                    "tech", 2, 2,
                    "이미지 최신 포맷·최적화 적용",
                    f"이미지가 {tech['img_total']}개로 많은데 webp/avif나 Next 이미지 최적화 적용이 "
                    f"{tech['img_modern_pct']}%에 그칩니다. 이미지가 무거우면 페이지 속도가 느려지고 "
                    "속도는 순위에 영향을 줍니다.",
                    "Next.js면 <img> 대신 next/image의 <Image>를 쓰면 자동으로 webp 변환·크기 최적화·"
                    "lazy loading이 적용됩니다. 이미지가 많은 이 사이트에 효과가 큽니다.",
                    f"최적화 {tech['img_modern_pct']}%",
                ))

        # 6) 구조화 데이터 없음
        if tech["schema_count"] == 0:
            todos.append(_item(
                "tech", 2, 2,
                "구조화 데이터(JSON-LD) 추가",
                "구조화 데이터는 검색 결과에 별점·이벤트·FAQ 같은 리치 결과로 표시될 기회를 만듭니다. "
                "지금은 하나도 없어서 그 기회를 못 쓰고 있습니다.",
                "교육 과정 사이트라면 Organization, Course, BreadcrumbList 스키마가 적합합니다. "
                "Next.js면 <head>나 layout에 JSON-LD <script>를 삽입하세요. "
                "schema.org에서 타입별 예시를 확인할 수 있습니다.",
                "schema 0종",
            ))

        # 7) 메타 description
        if tech["meta_desc_len"] == 0:
            todos.append(_item(
                "tech", 2, 1,
                "meta description 작성",
                "meta description은 검색 결과에 표시되는 요약문입니다. 없으면 구글이 임의로 "
                "발췌해 붙이는데, 직접 쓴 매력적인 문장이 클릭률을 높입니다.",
                "각 페이지에 120~155자 내외로 핵심을 요약한 description을 넣으세요. "
                "Next.js면 metadata API나 next/head로 페이지별 설정 가능합니다.",
            ))
        elif tech["meta_desc_len"] > 160:
            todos.append(_item(
                "tech", 1, 1,
                f"meta description 길이 조정 ({tech['meta_desc_len']}자)",
                "160자를 넘으면 검색 결과에서 뒷부분이 잘립니다.",
                "155자 이내로 핵심을 앞쪽에 배치해 다듬으세요.",
            ))

        # 8) 기본 위생
        if not tech["has_lang"]:
            todos.append(_item(
                "tech", 1, 1,
                "html lang 속성 추가",
                "<html lang=\"ko\">가 있으면 검색엔진과 브라우저가 언어를 정확히 인식합니다.",
                "루트 레이아웃의 <html> 태그에 lang=\"ko\"를 추가하세요.",
            ))
        if tech["og_count"] == 0:
            todos.append(_item(
                "tech", 1, 1,
                "Open Graph 태그 추가",
                "OG 태그는 카카오톡·페이스북 등에 링크 공유 시 제목·이미지 미리보기를 결정합니다.",
                "og:title, og:description, og:image, og:url을 <head>에 추가하세요.",
            ))

    # ============ 데이터 처방 (GSC insights 기반) ============
    if insights:
        # 기회 키워드 → 콘텐츠 보강
        opp = insights.get("opportunity", [])
        if opp:
            top = opp[0]
            names = ", ".join(f"'{o['key']}'" for o in opp[:3])
            todos.append(_item(
                "content", 3, 2,
                f"11~20위 기회 키워드 콘텐츠 보강 ({len(opp)}개)",
                f"{names} 같은 검색어가 지금 2페이지(11~20위)에 있습니다. 노출은 되는데 "
                "1페이지를 못 넘어 클릭을 놓치는 중입니다. 조금만 밀면 트래픽이 크게 늘 수 있습니다.",
                f"가장 유망한 '{top['key']}'(현재 {top['position']}위)부터: 이 검색어를 다루는 "
                "페이지의 내용을 더 깊고 구체적으로 보강하고, 관련 내부링크를 걸고, 제목·본문에 "
                "이 표현을 자연스럽게 반영하세요.",
                f"1페이지 진입 시 예상 클릭 +{top.get('potential_gain', 0)}/기간",
            ))

        # 고노출 저CTR → 제목/설명 개선
        ctr = insights.get("ctr_fix", [])
        if ctr:
            top = ctr[0]
            todos.append(_item(
                "content", 2, 1,
                f"클릭률 낮은 검색어의 제목·설명 손보기 ({len(ctr)}개)",
                f"'{top['key']}' 등은 노출은 충분한데 클릭률이 순위 대비 낮습니다. 순위를 "
                "안 올려도 제목·설명만 매력적으로 바꾸면 클릭을 회복할 수 있습니다.",
                "해당 페이지의 <title>과 meta description을 검색 의도에 맞게, 클릭하고 싶게 "
                "다시 쓰세요. 숫자·혜택·구체성을 넣으면 효과적입니다.",
                f"예상 회복 클릭 +{top.get('missed_clicks', 0)}",
            ))

        # 기간 비교 → 하락 경고
        cmp = insights.get("compare")
        if cmp and cmp["clicks"]["change_pct"] is not None and cmp["clicks"]["change_pct"] < -10:
            todos.append(_item(
                "content", 3, 2,
                f"클릭수 하락 점검 (직전 대비 {cmp['clicks']['change_pct']}%)",
                "최근 기간 클릭이 직전보다 눈에 띄게 줄었습니다. 순위 하락, 색인 이탈, "
                "계절성 중 원인을 찾아야 합니다.",
                "GSC에서 '어떤 검색어/페이지'의 클릭이 빠졌는지 비교해 원인 페이지를 특정하세요.",
                f"클릭 {cmp['clicks']['previous']} → {cmp['clicks']['current']}",
            ))

    # ============ 색인 처방 (GSC 색인 상태 기반) ============
    if gsc and gsc.get("index_status"):
        not_indexed = [r for r in gsc["index_status"] if r["verdict"] in ("FAIL", "NEUTRAL")]
        if not_indexed:
            todos.append(_item(
                "tech", 3, 1,
                f"색인 안 된 페이지 처리 ({len(not_indexed)}개)",
                "색인되지 않은 페이지는 검색에 아예 안 나옵니다. 중요한 페이지가 여기 있다면 "
                "트래픽을 통째로 잃고 있는 겁니다.",
                "각 URL의 색인 제외 사유를 확인하세요. 'noindex' 태그면 제거하고, "
                "'Crawled - not indexed'면 콘텐츠 품질·중복을 점검한 뒤 GSC에서 색인을 요청하세요.",
                f"{len(not_indexed)}개 미색인",
            ))

    # 우선순위 정렬 (효과 크고 쉬운 것 먼저)
    todos.sort(key=lambda x: (-x["priority"], x["effort"]))

    # 요약 신호등
    tech_cnt = sum(1 for t in todos if t["category"] == "tech")
    content_cnt = sum(1 for t in todos if t["category"] == "content")
    high_impact = sum(1 for t in todos if t["impact"] == 3)

    if high_impact >= 3:
        health = ("개선 필요", "warn", "효과 큰 개선 항목이 여러 개 있습니다. 상위 항목부터 처리하면 눈에 띄는 변화를 기대할 수 있습니다.")
    elif high_impact >= 1:
        health = ("주의", "caution", "전반적으로 기본은 갖췄지만 놓치는 기회가 있습니다. 상위 몇 개만 처리해도 효과적입니다.")
    else:
        health = ("양호", "good", "큰 문제는 없습니다. 세부 항목을 다듬어 완성도를 높이세요.")

    return {
        "todos": todos,
        "summary": {
            "total": len(todos),
            "tech": tech_cnt,
            "content": content_cnt,
            "high_impact": high_impact,
            "health_label": health[0],
            "health_class": health[1],
            "health_msg": health[2],
        },
    }
