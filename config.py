"""
환경변수 기반 설정.
비밀키를 코드에 절대 넣지 않고, Render의 Environment 탭에서 설정한다.

이 앱은 "임의의 URL을 입력하면 실데이터 리포트가 나오는" 즉석분석 도구라서,
계정 소유권 인증이 필요한 서비스(GSC/GA4/네이버 광고 등)는 다루지 않는다 —
그런 데이터는 사이트 소유자 본인이 아니면 누구도 받아올 수 없기 때문이다.
여기 있는 값들은 전부 "누구의 URL이든 키 하나로 조회 가능한" 공개 API용이다.
"""

import os


def _env(key, default=None):
    return os.environ.get(key, default)


# ---- 로그인 ----
APP_USERNAME = _env("APP_USERNAME", "admin")
APP_PASSWORD = _env("APP_PASSWORD", "changeme")   # 반드시 Render에서 재설정할 것
SESSION_SECRET = _env("SESSION_SECRET", "dev-secret-change-in-production")

# ---- PageSpeed Insights (선택) ----
# 없어도 동작하지만 쿼터가 매우 낮아 429 에러가 잦음. 키를 넣으면 더 안정적.
PAGESPEED_API_KEY = _env("PAGESPEED_API_KEY", "")

# ---- Gemini AI 노출 추적 (무료 티어) ----
# console: aistudio.google.com/apikey 에서 무료로 발급 (신용카드 불필요)
# /analyze에 입력된 URL을 크롤링한 정보를 바탕으로 질문을 자동 생성해서 즉석으로 확인한다.
GEMINI_API_KEY = _env("GEMINI_API_KEY", "")
GEMINI_MODEL = _env("GEMINI_MODEL", "gemini-2.0-flash")
