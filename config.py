"""
환경변수 기반 설정.
로컬 버전의 config_gsc.py / config_ads.py 를 대체.
비밀키를 코드에 절대 넣지 않고, Render의 Environment 탭에서 설정한다.

서비스 계정 JSON은 파일이 아니라 통째로 문자열(환경변수)로 넣는다.
GitHub에 올라가면 안 되는 값들이라 .env.example 에만 형태를 남겨둔다.
"""

import os
import json


def _env(key, default=None):
    return os.environ.get(key, default)


def _parse_json_env(key):
    raw = os.environ.get(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


# ---- 로그인 ----
APP_USERNAME = _env("APP_USERNAME", "admin")
APP_PASSWORD = _env("APP_PASSWORD", "changeme")   # 반드시 Render에서 재설정할 것
SESSION_SECRET = _env("SESSION_SECRET", "dev-secret-change-in-production")

# ---- GSC ----
GSC_SITE_URL = _env("GSC_SITE_URL", "sc-domain:example.com")
GSC_SERVICE_ACCOUNT_JSON = _parse_json_env("GSC_SERVICE_ACCOUNT_JSON")
GSC_MOCK = _env("GSC_MOCK", "true").lower() == "true"

# ---- GA4 ----
GA4_PROPERTY_ID = _env("GA4_PROPERTY_ID", "")
GA4_SERVICE_ACCOUNT_JSON = _parse_json_env("GA4_SERVICE_ACCOUNT_JSON")
GA4_MOCK = _env("GA4_MOCK", "true").lower() == "true"

# ---- 네이버 검색광고 ----
NAVER_API_KEY = _env("NAVER_API_KEY", "")
NAVER_SECRET_KEY = _env("NAVER_SECRET_KEY", "")
NAVER_CUSTOMER_ID = _env("NAVER_CUSTOMER_ID", "")
NAVER_MOCK = _env("NAVER_MOCK", "true").lower() == "true"

# ---- SerpApi ----
SERPAPI_KEY = _env("SERPAPI_KEY", "")

# ---- 내 사이트 / 경쟁사 (콤마로 구분해서 환경변수에 넣음) ----
MY_URL = _env("MY_URL", "https://example.com")
BRAND_NAME = _env("BRAND_NAME", "")
PAGESPEED_API_KEY = _env("PAGESPEED_API_KEY", "")
SOCIAL_URLS = [u.strip() for u in _env("SOCIAL_URLS", "").split(",") if u.strip()]
COMPETITOR_URLS = [u.strip() for u in _env("COMPETITOR_URLS", "").split(",") if u.strip()]
# COMPETITOR_URLS와 같은 순서로 대응하는 경쟁사 "이름"(콤마 구분). 없으면 도메인에서 대충 유추.
COMPETITOR_NAMES = [n.strip() for n in _env("COMPETITOR_NAMES", "").split(",") if n.strip()]
TARGET_KEYWORDS = [k.strip() for k in _env("TARGET_KEYWORDS", "").split(",") if k.strip()]

# ---- Gemini AI 노출 추적 (무료 티어) ----
# console: aistudio.google.com/apikey 에서 무료로 발급 (신용카드 불필요)
GEMINI_API_KEY = _env("GEMINI_API_KEY", "")
GEMINI_MODEL = _env("GEMINI_MODEL", "gemini-2.5-flash")

# 추적할 질문(프롬프트) 고정 목록. 실제 사용자가 물어볼 법한 자연어 질문을 직접 채워 넣는다.
# 줄바꿈으로 구분해도 되고, 줄바꿈 입력이 어려운 환경이면 " | "로 구분해도 된다.
# 예: 기업교육 서비스 잘하는 곳 추천해줘 | 강사 섭외 플랫폼 추천해줘
GEO_PROMPTS = [
    p.strip() for p in _env("GEO_PROMPTS", "").replace("|", "\n").split("\n") if p.strip()
]

# refresh 엔드포인트 보호용 (cron-job.org가 이 키를 같이 보내야 실행됨)
REFRESH_TOKEN = _env("REFRESH_TOKEN", "")


def gsc_auth():
    if GSC_MOCK or not GSC_SERVICE_ACCOUNT_JSON:
        return None
    return {"method": "service_account_info", "info": GSC_SERVICE_ACCOUNT_JSON}


def ga4_auth():
    if GA4_MOCK or not GA4_SERVICE_ACCOUNT_JSON:
        return None
    return {"method": "service_account_info", "info": GA4_SERVICE_ACCOUNT_JSON}


def naver_auth():
    if NAVER_MOCK or not (NAVER_API_KEY and NAVER_SECRET_KEY and NAVER_CUSTOMER_ID):
        return None
    return {"api_key": NAVER_API_KEY, "secret_key": NAVER_SECRET_KEY, "customer_id": NAVER_CUSTOMER_ID}
