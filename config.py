"""
환경변수 기반 설정.
비밀키를 코드에 절대 넣지 않고, Render의 Environment 탭에서 설정한다.

이 앱은 "내 사이트" 설정(Supabase)에 등록해둔 사이트를 기준으로 분석을 실행하는
도구라서, 계정 소유권 인증이 필요한 서비스(GSC/GA4/네이버 광고 등)는 다루지 않는다 —
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
# 등록된 사이트를 크롤링한 정보를 바탕으로 질문을 자동 생성하거나(프롬프트 미저장 시),
# 저장해둔 프롬프트를 그대로 써서 확인한다.
GEMINI_API_KEY = _env("GEMINI_API_KEY", "")
GEMINI_MODEL = _env("GEMINI_MODEL", "gemini-flash-latest")

# ---- Supabase (선택하지만 사실상 필수) — 내 사이트/경쟁사/프롬프트 설정 저장 +
# 노출도·인용·언급 추이 이력 저장용 ----
# Render는 재배포마다 로컬 파일시스템이 초기화되므로, 우리 앱 바깥의 관리형 DB에
# 저장해야 설정과 이력이 유지된다. 이건 남의 계정 데이터가 아니라 우리 앱 자체의
# 설정/분석 이력이라 소유권 인증 문제와 무관하다. 설정 안 하면 "설정되지 않음"
# 안내만 뜨고 분석 실행 자체가 안 된다 — 등록된 사이트가 있어야 뭘 분석할지 알 수 있어서.
SUPABASE_URL = _env("SUPABASE_URL", "")
SUPABASE_KEY = _env("SUPABASE_KEY", "")  # Settings → API → service_role 키 사용

# ---- 예약 갱신용 토큰 (선택) ----
# Render 무료 플랜엔 상시 크론이 없어서, 외부 스케줄러(GitHub Actions 등)가
# 하루 한 번 이 토큰으로 /internal/refresh를 호출해 캐시를 미리 채워둔다.
# 비워두면 이 엔드포인트 자체가 항상 403을 반환해 아무도 못 쓴다.
REFRESH_TOKEN = _env("REFRESH_TOKEN", "")
