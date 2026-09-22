"""
타겟 키워드 성장 추적기.
실행할 때마다(python run_gsc.py) 지정한 타겟 키워드들의 GSC 노출·순위, SERP 순위를
data/keyword_tracker.json에 한 줄씩 누적 기록한다. 여러 번 쌓이면 '진짜 자라고 있는지'
시계열로 확인할 수 있다.

이건 스냅샷(data/gsc-*.json)과 다르다 — 스냅샷은 실행 시점 전체 데이터, 이건
'내가 키우고 싶다고 지정한 키워드'만 뽑아 누적하는 전용 로그다.
"""

import os
import json
from datetime import datetime, timezone

TRACKER_PATH_DEFAULT = "data/keyword_tracker.json"


def _load(path):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _find_gsc_match(keyword, gsc_queries):
    """GSC 검색어 목록에서 정확히(대소문자 무시) 일치하는 항목 찾기."""
    low = keyword.strip().lower()
    for q in gsc_queries:
        if q["key"].strip().lower() == low:
            return q
    return None


def _find_serp_match(keyword, serp_items):
    """SERP 조회 결과에서 해당 키워드 항목 찾기."""
    if not serp_items:
        return None
    for it in serp_items:
        if it["keyword"].strip().lower() == keyword.strip().lower():
            return it
    return None


def record_snapshot(target_keywords, gsc_queries, serp_items=None,
                     path=TRACKER_PATH_DEFAULT, today=None):
    """
    실행 시점 데이터를 타겟 키워드별로 기록에 추가.
    같은 날짜에 이미 기록이 있으면 그날 기록을 덮어씀(하루 여러 번 돌려도 중복 안 쌓임).
    """
    if not target_keywords:
        return _load(path)

    history = _load(path)
    today = today or datetime.now(timezone.utc).date().isoformat()

    for kw in target_keywords:
        gsc_match = _find_gsc_match(kw, gsc_queries)
        serp_match = _find_serp_match(kw, serp_items or [])

        point = {
            "date": today,
            "impressions": gsc_match["impressions"] if gsc_match else 0,
            "clicks": gsc_match["clicks"] if gsc_match else 0,
            "gsc_position": gsc_match["position"] if gsc_match else None,
            "serp_rank": serp_match["my_rank"] if serp_match else None,
        }

        history.setdefault(kw, [])
        # 같은 날짜 기록이 있으면 교체, 없으면 추가
        history[kw] = [p for p in history[kw] if p["date"] != today]
        history[kw].append(point)
        history[kw].sort(key=lambda p: p["date"])

    _save(path, history)
    return history


def growth_summary(history, target_keywords):
    """각 타겟 키워드의 추세 요약 — 첫 기록 대비 최신 기록 변화."""
    out = []
    for kw in target_keywords:
        points = history.get(kw, [])
        if not points:
            out.append({"keyword": kw, "points": [], "status": "no_data"})
            continue

        latest = points[-1]
        first = points[0]
        n = len(points)

        imp_delta = latest["impressions"] - first["impressions"]
        pos_delta = None
        if latest["gsc_position"] is not None and first["gsc_position"] is not None:
            pos_delta = round(first["gsc_position"] - latest["gsc_position"], 1)  # 양수=개선(순위 낮아짐)

        out.append({
            "keyword": kw,
            "points": points,
            "n_records": n,
            "latest": latest,
            "first": first,
            "imp_delta": imp_delta,
            "pos_delta": pos_delta,
            "status": "tracking" if n >= 2 else "started",
        })
    return out
