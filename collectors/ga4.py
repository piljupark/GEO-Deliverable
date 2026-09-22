"""
GA4(Google Analytics 4) Data API 수집기.
소스/매체별 조회수·세션수·활성사용자·참여시간·이벤트수 + 오전/오후 트래픽 추이.

인증은 GSC와 동일한 서비스계정/OAuth 패턴. 다른 점은 스코프와 property_id.

필요 패키지:
  pip install google-analytics-data google-auth google-auth-oauthlib
"""

import os
from datetime import datetime, timedelta, timezone

SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]


def _build_client(auth):
    from google.analytics.data_v1beta import BetaAnalyticsDataClient

    method = auth.get("method")
    if method == "service_account":
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(
            auth["key_file"], scopes=SCOPES
        )
        return BetaAnalyticsDataClient(credentials=creds)

    elif method == "service_account_info":
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_info(
            auth["info"], scopes=SCOPES
        )
        return BetaAnalyticsDataClient(credentials=creds)

    elif method == "oauth":
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request

        token_path = auth.get("token", "token_ga4.json")
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
        return BetaAnalyticsDataClient(credentials=creds)

    raise ValueError(f"알 수 없는 인증 방식: {method}")


def collect_ga4(property_id, auth=None, days_back=7, mock=False):
    """
    property_id: GA4 속성 ID (숫자만, 'properties/' 접두어 없이). GA4 관리자 화면
                 우측 하단 '속성 세부정보'에서 확인 가능.
    """
    if mock or auth is None:
        return _mock_ga4(property_id, days_back)

    from google.analytics.data_v1beta.types import (
        RunReportRequest, DateRange, Dimension, Metric,
    )
    client = _build_client(auth)
    prop = f"properties/{property_id}"

    # 1) 소스/매체별 표
    req1 = RunReportRequest(
        property=prop,
        date_ranges=[DateRange(start_date=f"{days_back}daysAgo", end_date="today")],
        dimensions=[Dimension(name="sessionSourceMedium")],
        metrics=[
            Metric(name="screenPageViews"),
            Metric(name="sessions"),
            Metric(name="activeUsers"),
            Metric(name="averageSessionDuration"),
            Metric(name="eventCount"),
        ],
        limit=15,
    )
    resp1 = client.run_report(req1)
    source_rows = []
    for row in resp1.rows:
        source_rows.append({
            "source_medium": row.dimension_values[0].value,
            "views": int(float(row.metric_values[0].value)),
            "sessions": int(float(row.metric_values[1].value)),
            "active_users": int(float(row.metric_values[2].value)),
            "avg_engagement_sec": round(float(row.metric_values[3].value), 2),
            "events": int(float(row.metric_values[4].value)),
        })

    # 2) 날짜+시간별 (오전/오후 구분용)
    req2 = RunReportRequest(
        property=prop,
        date_ranges=[DateRange(start_date=f"{days_back}daysAgo", end_date="today")],
        dimensions=[Dimension(name="date"), Dimension(name="hour")],
        metrics=[Metric(name="screenPageViews"), Metric(name="sessions")],
    )
    resp2 = client.run_report(req2)
    half_day = {}  # (date, "오전"/"오후") -> {views, sessions}
    for row in resp2.rows:
        d = row.dimension_values[0].value  # YYYYMMDD
        h = int(row.dimension_values[1].value)
        period = "오전" if h < 12 else "오후"
        key = (d, period)
        half_day.setdefault(key, {"views": 0, "sessions": 0})
        half_day[key]["views"] += int(float(row.metric_values[0].value))
        half_day[key]["sessions"] += int(float(row.metric_values[1].value))

    timeline = []
    for (d, period), v in sorted(half_day.items()):
        date_fmt = f"{d[4:6]}월 {d[6:8]}일"
        timeline.append({"date": date_fmt, "period": period,
                          "views": v["views"], "sessions": v["sessions"]})

    totals = {
        "views": sum(r["views"] for r in source_rows),
        "sessions": sum(r["sessions"] for r in source_rows),
        "active_users": sum(r["active_users"] for r in source_rows),
        "events": sum(r["events"] for r in source_rows),
    }

    return {
        "property_id": property_id,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "LIVE",
        "totals": totals,
        "source_rows": sorted(source_rows, key=lambda x: -x["views"]),
        "timeline": timeline,
    }


def _mock_ga4(property_id, days_back):
    import random
    random.seed(hash(property_id) % 10000)

    sources = [
        "(direct) / (none)", "(not set)", "m.search.naver.com / referral",
        "padlet.com / referral", "google / organic", "naver / brandad",
        "email / ceo", "(data not available)", "meta / ad", "google / cpc",
    ]
    source_rows = []
    for s in sources:
        views = random.randint(0, 120)
        source_rows.append({
            "source_medium": s,
            "views": views,
            "sessions": random.randint(0, 120),
            "active_users": random.randint(0, 90),
            "avg_engagement_sec": round(random.uniform(0, 6), 2),
            "events": random.randint(0, 400),
        })

    timeline = []
    d0 = datetime.now(timezone.utc).date() - timedelta(days=days_back)
    for i in range(days_back + 1):
        day = d0 + timedelta(days=i)
        date_fmt = f"{day.month}월 {day.day}일"
        for period in ["오전", "오후"]:
            timeline.append({
                "date": date_fmt, "period": period,
                "views": random.randint(2000, 170000),
                "sessions": random.randint(5, 1600),
            })

    totals = {
        "views": sum(r["views"] for r in source_rows),
        "sessions": sum(r["sessions"] for r in source_rows),
        "active_users": sum(r["active_users"] for r in source_rows),
        "events": sum(r["events"] for r in source_rows),
    }

    return {
        "property_id": property_id,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "MOCK",
        "totals": totals,
        "source_rows": sorted(source_rows, key=lambda x: -x["views"]),
        "timeline": timeline,
    }
