"""
외부 API 호출 공통 재시도 헬퍼.
타임아웃/연결오류/5xx(서버 쪽 일시 장애)처럼 "다시 하면 될 수도 있는" 실패만
짧게 재시도한다. 429(쿼터 초과)나 4xx(요청 자체가 잘못됨)는 재시도해도
의미가 없으니 바로 올린다 — 가짜로 성공한 척하지 않는다.
"""

import time
import requests


def request_with_retry(method, url, attempts=3, backoff=2, **kwargs):
    last_exc = None
    for i in range(attempts):
        try:
            resp = requests.request(method, url, **kwargs)
            if resp.status_code >= 500:
                raise requests.exceptions.HTTPError(
                    f"{resp.status_code} Server Error: {resp.reason} for url: {url}", response=resp
                )
            resp.raise_for_status()
            return resp
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_exc = e
        except requests.exceptions.HTTPError as e:
            resp = getattr(e, "response", None)
            if resp is not None and resp.status_code < 500:
                raise
            last_exc = e
        if i < attempts - 1:
            time.sleep(backoff)
    raise last_exc
