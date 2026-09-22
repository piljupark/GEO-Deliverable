"""
GSC 데이터에서 '실행 가능한 인사이트'를 도출한다.
원본 API 데이터를 받아 가공만 하므로 추가 API 호출/비용 없음.

도출 항목:
  1) 기회 키워드 — 11~20위 (조금만 밀면 1페이지)
  2) CTR 개선 후보 — 고노출·저CTR (제목/설명 손보면 클릭↑)
  3) 순위 구간 분포 — 1-3 / 4-10 / 11-20 / 21+ 몇 개씩
  4) 기간 비교 — 이번 기간 vs 직전 동일 기간 (상승/하락)
"""


def _expected_ctr(position):
    """게재순위별 기대 CTR(%) 대략치. 위치 대비 실제 CTR 낮으면 개선 여지."""
    table = {
        1: 28.0, 2: 15.0, 3: 11.0, 4: 8.0, 5: 7.0,
        6: 5.5, 7: 4.0, 8: 3.2, 9: 2.8, 10: 2.5,
    }
    p = int(round(position))
    if p <= 10:
        return table.get(p, 2.5)
    if p <= 20:
        return 1.2
    return 0.6


def _is_real_query(q):
    """검색 연산자(site:, intitle: 등)가 섞인 비정상 쿼리 제외 — 관리자 본인 검색일 가능성."""
    low = q.strip().lower()
    operators = ("site:", "intitle:", "inurl:", "related:", "cache:", "filetype:")
    return not any(low.startswith(op) or f" {op}" in low for op in operators)


def opportunity_keywords(queries, pos_min=10.5, pos_max=20.5, min_impressions=20):
    """11~20위 구간 + 노출이 어느 정도 있는 검색어. 우선 공략 대상."""
    out = []
    for q in queries:
        if not _is_real_query(q["key"]):
            continue
        if pos_min <= q["position"] <= pos_max and q["impressions"] >= min_impressions:
            # 잠재 클릭: 5위권으로 올렸을 때 기대 클릭 증가분(대략)
            potential = q["impressions"] * (_expected_ctr(5) / 100)
            gain = max(0, potential - q["clicks"])
            out.append({**q, "potential_gain": round(gain)})
    return sorted(out, key=lambda x: -x["potential_gain"])


def ctr_improvement(queries, min_impressions=50, ctr_gap=1.5):
    """노출은 충분한데 실제 CTR이 기대치보다 크게 낮은 검색어."""
    out = []
    for q in queries:
        if not _is_real_query(q["key"]):
            continue
        if q["impressions"] < min_impressions:
            continue
        exp = _expected_ctr(q["position"])
        if q["ctr"] < exp - ctr_gap:  # 기대보다 유의미하게 낮을 때
            missed = q["impressions"] * ((exp - q["ctr"]) / 100)
            out.append({**q, "expected_ctr": round(exp, 1),
                        "missed_clicks": round(missed)})
    return sorted(out, key=lambda x: -x["missed_clicks"])


def position_buckets(queries):
    """순위 구간별 검색어 개수 + 노출 합계."""
    buckets = {
        "1-3위": {"count": 0, "impressions": 0},
        "4-10위": {"count": 0, "impressions": 0},
        "11-20위": {"count": 0, "impressions": 0},
        "21위+": {"count": 0, "impressions": 0},
    }
    for q in queries:
        if not _is_real_query(q["key"]):
            continue
        p = q["position"]
        if p <= 3:
            k = "1-3위"
        elif p <= 10:
            k = "4-10위"
        elif p <= 20:
            k = "11-20위"
        else:
            k = "21위+"
        buckets[k]["count"] += 1
        buckets[k]["impressions"] += q["impressions"]
    return buckets


def period_compare(current_totals, previous_totals):
    """이번 기간 vs 직전 기간 변화율."""
    def pct(cur, prev):
        if not prev:
            return None
        return round((cur - prev) / prev * 100, 1)

    return {
        "clicks": {
            "current": current_totals["clicks"],
            "previous": previous_totals["clicks"],
            "change_pct": pct(current_totals["clicks"], previous_totals["clicks"]),
        },
        "impressions": {
            "current": current_totals["impressions"],
            "previous": previous_totals["impressions"],
            "change_pct": pct(current_totals["impressions"], previous_totals["impressions"]),
        },
        "ctr": {
            "current": current_totals["ctr"],
            "previous": previous_totals["ctr"],
            "change_pct": pct(current_totals["ctr"], previous_totals["ctr"]),
        },
        "position": {
            "current": current_totals["position"],
            "previous": previous_totals["position"],
            # 순위는 낮을수록 좋으므로 부호 반대로 해석 (개선/악화)
            "change_pct": pct(current_totals["position"], previous_totals["position"]),
        },
    }


def related_queries(queries, contains, min_impressions=1):
    """
    GSC 전체 검색어 중 특정 단어(들)가 포함된 실제 검색어를 전부 찾는다.
    이미 노출된 적 있는 '진짜' 연관 검색어이므로 추측이 아니라 오피셜 데이터.

    contains: 문자열 또는 문자열 리스트. 하나라도 포함되면 매칭.
    """
    if isinstance(contains, str):
        contains = [contains]
    terms = [c.lower().strip() for c in contains if c.strip()]
    if not terms:
        return []

    out = []
    for q in queries:
        if not _is_real_query(q["key"]):
            continue
        low = q["key"].lower()
        if any(t in low for t in terms) and q["impressions"] >= min_impressions:
            out.append(q)
    return sorted(out, key=lambda x: -x["impressions"])



def build_insights(gsc):
    """gsc 딕셔너리(top_queries, totals, prev_totals 포함)에서 인사이트 묶음 생성."""
    queries = gsc.get("top_queries", [])
    insights = {
        "opportunity": opportunity_keywords(queries),
        "ctr_fix": ctr_improvement(queries),
        "buckets": position_buckets(queries),
        "compare": None,
    }
    if gsc.get("prev_totals"):
        insights["compare"] = period_compare(gsc["totals"], gsc["prev_totals"])
    return insights
