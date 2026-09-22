"""
네이버 검색광고(SearchAd) API 수집기.
캠페인/광고그룹 단위 노출·클릭·비용을 가져와 CPC/CPM/CTR을 계산한다.

인증: API License Key + Secret Key + Customer ID (HMAC-SHA256 서명 방식)
발급: searchad.naver.com 로그인 → 도구 → API 사용 관리
      (사업자 인증이 된 계정이어야 발급 가능)

필요 패키지: requests (이미 있음)
"""

import os
import time
import base64
import hmac
import hashlib
import json
from datetime import datetime, timedelta, timezone

BASE_URL = "https://api.naver.com"


def _signature(timestamp, method, uri, secret_key):
    msg = f"{timestamp}.{method}.{uri}"
    sig = hmac.new(secret_key.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256)
    return base64.b64encode(sig.digest()).decode("utf-8")


def _headers(method, uri, api_key, secret_key, customer_id):
    ts = str(round(time.time() * 1000))
    return {
        "Content-Type": "application/json; charset=UTF-8",
        "X-Timestamp": ts,
        "X-API-KEY": api_key,
        "X-Customer": str(customer_id),
        "X-Signature": _signature(ts, method, uri, secret_key),
    }


def _get(uri, params, api_key, secret_key, customer_id, timeout=20):
    import requests
    headers = _headers("GET", uri, api_key, secret_key, customer_id)
    resp = requests.get(BASE_URL + uri, params=params, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def collect_naver_ads(auth=None, days_back=1, mock=False):
    """
    auth: {"api_key": "...", "secret_key": "...", "customer_id": "..."}
    days_back: 조회 기간(일). 1이면 오늘 하루.

    실제 API는 2단계: 1) 캠페인 ID 목록 조회 2) 그 ID들로 /stats 통계 조회.
    (customer_id 자체는 통계 조회 대상 id가 아님 — 계정 헤더로만 쓰임)

    반환: 캠페인 통합 집계 — 노출/클릭/비용/CPC/CPM/CTR
    """
    if mock or not auth:
        return _mock_naver_ads(days_back)

    api_key = auth["api_key"]
    secret_key = auth["secret_key"]
    customer_id = auth["customer_id"]

    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days_back - 1)

    try:
        # 1) 캠페인 ID 목록 조회
        campaigns = _get("/ncc/campaigns", {}, api_key, secret_key, customer_id)
        if not isinstance(campaigns, list) or not campaigns:
            return {
                "source": "ERROR:NoCampaigns",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "impressions": 0, "clicks": 0, "cost": 0, "cpc": 0, "cpm": 0, "ctr": 0,
                "detail": "등록된 캠페인이 없거나 계정에 접근 권한이 없습니다.",
            }
        campaign_ids = [c["nccCampaignId"] for c in campaigns if c.get("nccCampaignId")]

        # 2) 캠페인 ID들로 통계 조회 (콤마로 join한 ids 파라미터)
        params = {
            "ids": ",".join(campaign_ids),
            "fields": json.dumps(["impCnt", "clkCnt", "salesAmt"]),
            "timeRange": json.dumps({
                "since": start.isoformat(),
                "until": end.isoformat(),
            }),
        }
        data = _get("/stats", params, api_key, secret_key, customer_id)

    except Exception as e:
        import traceback
        return {
            "source": f"ERROR:{type(e).__name__}",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "impressions": 0, "clicks": 0, "cost": 0, "cpc": 0, "cpm": 0, "ctr": 0,
            "detail": str(e),
        }

    rows = data.get("data", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    impressions = sum(int(r.get("impCnt", 0)) for r in rows)
    clicks = sum(int(r.get("clkCnt", 0)) for r in rows)
    cost = sum(int(r.get("salesAmt", 0)) for r in rows)

    cpc = round(cost / clicks) if clicks else 0
    cpm = round(cost / impressions * 1000) if impressions else 0
    ctr = round(clicks / impressions * 100, 2) if impressions else 0

    return {
        "source": "LIVE",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "impressions": impressions, "clicks": clicks, "cost": cost,
        "cpc": cpc, "cpm": cpm, "ctr": ctr,
        "campaign_count": len(campaign_ids),
    }


def _mock_naver_ads(days_back):
    return {
        "source": "MOCK",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "impressions": 4525, "clicks": 15, "cost": 45446,
        "cpc": 3030, "cpm": 9, "ctr": 0.33,
    }
