"""
Google Search Console 데이터 수집기.

가져오는 것:
  1) 검색 성과 시계열 (날짜별 노출/클릭/CTR/평균순위)
  2) 인기 검색어 Top N (query 차원)
  3) 인기 페이지 Top N (page 차원)
  4) 디바이스/국가 분포
  5) (선택) URL 색인 상태 — URL Inspection API

인증 방식 2가지:
  A) 서비스 계정 (service_account.json) — 서버/크론에 적합, 추천
  B) OAuth 클라이언트 (client_secret.json) — 브라우저로 1회 로그인

키/인증이 없으면 mock=True 로 데모 데이터를 만들어 대시보드를 미리 볼 수 있다.

필요 패키지(실제 연동 시):
  pip install google-api-python-client google-auth google-auth-oauthlib
"""

import os
import json
import random
from datetime import datetime, timedelta, timezone

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
API_LAG_DAYS = 3   # GSC는 2~3일 지연. 안전하게 3일 전까지만 조회.


# ------------------------------------------------------------------
# 인증
# ------------------------------------------------------------------
def _build_service(auth):
    """
    auth 딕셔너리 예:
      {"method": "service_account", "key_file": "service_account.json"}
      {"method": "oauth", "client_secret": "client_secret.json", "token": "token.json"}
    """
    from googleapiclient.discovery import build

    method = auth.get("method")
    if method == "service_account":
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(
            auth["key_file"], scopes=SCOPES
        )
        return build("searchconsole", "v1", credentials=creds)

    elif method == "service_account_info":
        # 서버 배포용: JSON 파일이 아니라 dict(환경변수에서 파싱된 것)를 바로 받음
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_info(
            auth["info"], scopes=SCOPES
        )
        return build("searchconsole", "v1", credentials=creds)

    elif method == "oauth":
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request

        token_path = auth.get("token", "token.json")
        creds = None
        if os.path.exists(token_path):
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    auth["client_secret"], SCOPES
                )
                creds = flow.run_local_server(port=0)
            with open(token_path, "w") as f:
                f.write(creds.to_json())
        return build("searchconsole", "v1", credentials=creds)

    raise ValueError(f"알 수 없는 인증 방식: {method}")


# ------------------------------------------------------------------
# 쿼리 헬퍼
# ------------------------------------------------------------------
def _query(service, site_url, body):
    return service.searchanalytics().query(siteUrl=site_url, body=body).execute()


def _daterange(days_back):
    end = datetime.now(timezone.utc).date() - timedelta(days=API_LAG_DAYS)
    start = end - timedelta(days=days_back)
    return start.isoformat(), end.isoformat()


def _prev_daterange(days_back):
    """직전 동일 길이 기간 (기간 비교용)."""
    cur_end = datetime.now(timezone.utc).date() - timedelta(days=API_LAG_DAYS)
    cur_start = cur_end - timedelta(days=days_back)
    prev_end = cur_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days_back)
    return prev_start.isoformat(), prev_end.isoformat()


def _totals_for(service, site_url, start, end):
    """지정 기간의 합계 지표만 계산."""
    body = {"startDate": start, "endDate": end, "dimensions": ["date"], "rowLimit": 1000}
    resp = _query(service, site_url, body)
    rows = resp.get("rows", [])
    clicks = sum(r.get("clicks", 0) for r in rows)
    impr = sum(r.get("impressions", 0) for r in rows)
    pos = [round(r.get("position", 0), 1) for r in rows]
    return {
        "clicks": clicks,
        "impressions": impr,
        "ctr": round(clicks / impr * 100, 2) if impr else 0,
        "position": round(sum(pos) / len(pos), 1) if pos else 0,
    }


def _fetch_dimension(service, site_url, start, end, dimension, limit):
    body = {
        "startDate": start,
        "endDate": end,
        "dimensions": [dimension],
        "rowLimit": limit,
    }
    resp = _query(service, site_url, body)
    rows = []
    for r in resp.get("rows", []):
        rows.append({
            "key": r["keys"][0],
            "clicks": r.get("clicks", 0),
            "impressions": r.get("impressions", 0),
            "ctr": round(r.get("ctr", 0) * 100, 2),
            "position": round(r.get("position", 0), 1),
        })
    return rows


# ------------------------------------------------------------------
# 메인 수집
# ------------------------------------------------------------------
def collect_gsc(site_url, auth=None, days_back=28, top_n=15, mock=False,
                check_index_urls=None):
    """
    site_url: GSC 등록된 속성. 도메인 속성이면 'sc-domain:studio.kma.or.kr',
              URL 접두어 속성이면 'https://studio.kma.or.kr/'
    """
    if mock or auth is None:
        return _mock_gsc(site_url, days_back, top_n)

    service = _build_service(auth)
    start, end = _daterange(days_back)

    # 1) 날짜별 시계열
    ts_body = {"startDate": start, "endDate": end, "dimensions": ["date"], "rowLimit": 1000}
    ts_resp = _query(service, site_url, ts_body)
    timeseries = [{
        "date": r["keys"][0],
        "clicks": r.get("clicks", 0),
        "impressions": r.get("impressions", 0),
        "ctr": round(r.get("ctr", 0) * 100, 2),
        "position": round(r.get("position", 0), 1),
    } for r in ts_resp.get("rows", [])]

    # 2) 인기 검색어 / 3) 인기 페이지 / 4) 디바이스
    # 심화 분석(기회 키워드 등)을 위해 검색어는 넉넉히 가져온다 (표시용 top_n과 별개)
    analysis_n = max(top_n, 200)
    top_queries = _fetch_dimension(service, site_url, start, end, "query", analysis_n)
    top_pages = _fetch_dimension(service, site_url, start, end, "page", top_n)
    devices = _fetch_dimension(service, site_url, start, end, "device", 10)

    # 직전 동일 기간 합계 (기간 비교용)
    ps, pe = _prev_daterange(days_back)
    try:
        prev_totals = _totals_for(service, site_url, ps, pe)
    except Exception:
        prev_totals = None

    # 합계
    totals = {
        "clicks": sum(t["clicks"] for t in timeseries),
        "impressions": sum(t["impressions"] for t in timeseries),
    }
    totals["ctr"] = round(totals["clicks"] / totals["impressions"] * 100, 2) if totals["impressions"] else 0
    totals["position"] = round(sum(t["position"] for t in timeseries) / len(timeseries), 1) if timeseries else 0

    # 5) 색인 상태 (선택) — URL Inspection API
    index_status = []
    if check_index_urls:
        for u in check_index_urls:
            try:
                resp = service.urlInspection().index().inspect(body={
                    "inspectionUrl": u, "siteUrl": site_url,
                }).execute()
                res = resp.get("inspectionResult", {}).get("indexStatusResult", {})
                index_status.append({
                    "url": u,
                    "verdict": res.get("verdict", "UNKNOWN"),
                    "coverage": res.get("coverageState", ""),
                    "last_crawl": res.get("lastCrawlTime", ""),
                })
            except Exception as e:
                index_status.append({"url": u, "verdict": "ERROR",
                                     "coverage": type(e).__name__, "last_crawl": ""})

    return {
        "site_url": site_url,
        "date_range": {"start": start, "end": end},
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "LIVE",
        "totals": totals,
        "prev_totals": prev_totals,
        "timeseries": timeseries,
        "top_queries": top_queries,
        "top_pages": top_pages,
        "devices": devices,
        "index_status": index_status,
    }


# ------------------------------------------------------------------
# 데모 데이터 (인증 전 대시보드 미리보기용)
# ------------------------------------------------------------------
def _mock_gsc(site_url, days_back, top_n):
    random.seed(hash(site_url) % 10000)
    start, end = _daterange(days_back)

    timeseries = []
    d0 = datetime.fromisoformat(start)
    base_imp = random.randint(800, 1600)
    for i in range(days_back + 1):
        day = d0 + timedelta(days=i)
        wk = 0.7 if day.weekday() >= 5 else 1.0  # 주말 감소
        imp = int(base_imp * wk * random.uniform(0.8, 1.2))
        ctr = random.uniform(0.03, 0.08)
        clicks = int(imp * ctr)
        timeseries.append({
            "date": day.date().isoformat(),
            "clicks": clicks,
            "impressions": imp,
            "ctr": round(ctr * 100, 2),
            "position": round(random.uniform(8, 22), 1),
        })

    sample_queries = [
        "kma 스튜디오", "기상 교육", "날씨 아카데미", "기상청 교육 신청",
        "기상 강좌 온라인", "예보관 교육", "기후 위기 강의", "기상 자격증",
        "날씨 데이터 분석", "기상 실무 교육", "지진 대응 교육", "태풍 예보 강의",
        "기상 스튜디오 후기", "kma academy", "기상 콘텐츠 제작",
        "기상 온라인 강의", "날씨 예보 배우기", "기후변화 교육", "기상관측 실습",
        "기상 데이터 시각화", "미세먼지 교육", "기상 채용", "날씨 앱 개발",
        "기상 통계 강좌", "해양 기상 교육", "항공 기상 강의", "기상 자료 활용",
    ]
    sample_pages = [
        "/", "/courses", "/courses/forecast", "/about", "/apply",
        "/courses/climate", "/notice", "/courses/data", "/faq",
        "/courses/earthquake", "/gallery", "/courses/typhoon",
        "/login", "/mypage", "/contact",
    ]

    def _mk(keys, base_prefix="", limit=None):
        out = []
        pool_imp = random.randint(300, 900)
        klist = keys if limit is None else keys[:limit]
        for k in klist:
            imp = int(pool_imp * random.uniform(0.2, 1.0))
            ctr = random.uniform(0.02, 0.12)
            clicks = int(imp * ctr)
            out.append({
                "key": base_prefix + k,
                "clicks": clicks,
                "impressions": imp,
                "ctr": round(ctr * 100, 2),
                "position": round(random.uniform(3, 30), 1),
            })
            pool_imp = int(pool_imp * random.uniform(0.6, 0.92))  # 롱테일 감소
        return sorted(out, key=lambda x: -x["clicks"])

    top_queries = _mk(sample_queries)  # 전체 — 심화 분석용
    dom = site_url.replace("sc-domain:", "").rstrip("/")
    top_pages = _mk(sample_pages, base_prefix=f"https://{dom}", limit=top_n)

    devices = []
    for dev, share in [("MOBILE", 0.62), ("DESKTOP", 0.33), ("TABLET", 0.05)]:
        imp = int(sum(t["impressions"] for t in timeseries) * share)
        clicks = int(sum(t["clicks"] for t in timeseries) * share)
        devices.append({
            "key": dev, "clicks": clicks, "impressions": imp,
            "ctr": round(clicks / imp * 100, 2) if imp else 0,
            "position": round(random.uniform(9, 16), 1),
        })

    totals = {
        "clicks": sum(t["clicks"] for t in timeseries),
        "impressions": sum(t["impressions"] for t in timeseries),
    }
    totals["ctr"] = round(totals["clicks"] / totals["impressions"] * 100, 2) if totals["impressions"] else 0
    totals["position"] = round(sum(t["position"] for t in timeseries) / len(timeseries), 1)

    # 색인 상태 데모
    verdicts = ["PASS", "PASS", "PASS", "NEUTRAL", "FAIL"]
    coverages = ["Submitted and indexed", "Submitted and indexed",
                 "Crawled - currently not indexed", "Discovered - currently not indexed",
                 "Excluded by 'noindex' tag"]
    index_status = []
    for i, pg in enumerate(sample_pages[:8]):
        j = min(i, len(verdicts) - 1) if i < 5 else random.randint(0, 4)
        index_status.append({
            "url": f"https://{dom}{pg}",
            "verdict": verdicts[j % len(verdicts)],
            "coverage": coverages[j % len(coverages)],
            "last_crawl": (datetime.now(timezone.utc) - timedelta(days=random.randint(1, 30))).isoformat(),
        })

    # 직전 기간 합계 (데모: 현재 대비 약간 낮게 = 성장 중처럼 보이게)
    prev_totals = {
        "clicks": int(totals["clicks"] * random.uniform(0.78, 0.95)),
        "impressions": int(totals["impressions"] * random.uniform(0.80, 0.97)),
    }
    prev_totals["ctr"] = round(prev_totals["clicks"] / prev_totals["impressions"] * 100, 2) if prev_totals["impressions"] else 0
    prev_totals["position"] = round(totals["position"] + random.uniform(-0.5, 2.0), 1)

    return {
        "site_url": site_url,
        "date_range": {"start": start, "end": end},
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "MOCK",
        "totals": totals,
        "prev_totals": prev_totals,
        "timeseries": timeseries,
        "top_queries": top_queries,
        "top_pages": top_pages,
        "devices": devices,
        "index_status": index_status,
    }
