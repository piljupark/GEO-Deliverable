# Signal 대시보드 — 클라우드 배포 가이드

로컬 스크립트를 실제 URL로 접속 가능한 웹앱으로 만드는 과정.
GitHub(코드 저장) → Render(실행/호스팅) → cron-job.org(자동 새로고침) 순서.

---

## 0. 로컬에서 먼저 확인 (선택, 추천)

```powershell
cd seo-webapp
python -m pip install -r requirements.txt
$env:APP_USERNAME="admin"
$env:APP_PASSWORD="test1234"
$env:SESSION_SECRET="testsecret"
$env:REFRESH_TOKEN="abc123"
uvicorn main:app --reload
```
브라우저로 http://127.0.0.1:8000 접속 → 로그인 → 먼저 http://127.0.0.1:8000/refresh?token=abc123 한 번 호출해서 데모 데이터 채우기.

---

## 1. GitHub에 올리기

```powershell
cd seo-webapp
git init
git add .
git commit -m "Signal dashboard 초기 커밋"
```

GitHub에서 새 저장소 만들기 (Private 추천 — Public이면 코드가 다 공개되니):
- github.com → New repository → 이름 아무거나(예: `signal-dashboard`) → **Private** 선택 → Create

```powershell
git remote add origin https://github.com/본인아이디/signal-dashboard.git
git branch -M main
git push -u origin main
```

`.gitignore`가 `.env`, `*.db`, `*.json` 같은 민감파일을 제외하니, **실제 키 값은 여기 안 올라갑니다.**

---

## 2. Render에 배포

1. [render.com](https://render.com) 가입 (GitHub 계정으로 바로 가입 가능, 카드 등록 불필요)
2. **New → Web Service** 클릭
3. 방금 만든 GitHub 저장소 연결(Connect) — Render가 자동으로 `render.yaml`을 읽어서 설정을 채웁니다
4. Region은 Singapore 등 가까운 곳 선택
5. **Create Web Service** 클릭 → 첫 배포 시작 (몇 분 걸림)

배포되면 `https://signal-dashboard-xxxx.onrender.com` 같은 URL이 생깁니다.

---

## 3. 환경변수(비밀키) 채우기

Render 대시보드 → 방금 만든 서비스 → **Environment** 탭에서 값 입력:

**필수 (지금 바로)**
```
APP_USERNAME = admin
APP_PASSWORD = (본인이 정한 강력한 비밀번호)
REFRESH_TOKEN = (아무 긴 랜덤 문자열, 예: openssl rand -hex 16 결과)
```

**GSC/GA4 실데이터 연결할 때** (서비스 계정 방식 — 아래 4번 참고)
```
GSC_SITE_URL = sc-domain:studio.kma.or.kr
GSC_MOCK = false
GSC_SERVICE_ACCOUNT_JSON = (서비스계정 JSON 파일 내용 전체를 한 줄로)

GA4_PROPERTY_ID = 123456789
GA4_MOCK = false
GA4_SERVICE_ACCOUNT_JSON = (마찬가지)
```

**네이버 실데이터**
```
NAVER_API_KEY = ...
NAVER_SECRET_KEY = ...
NAVER_CUSTOMER_ID = ...
NAVER_MOCK = false
```

**나머지**
```
SERPAPI_KEY = ...
MY_URL = https://studio.kma.or.kr
COMPETITOR_URLS = https://a.com,https://b.com
TARGET_KEYWORDS = 강사 섭외,강사 매칭
```

저장하면 자동으로 재배포됩니다.

---

## 4. 구글 인증을 "서비스 계정" 방식으로 전환

로컬에서 쓰던 OAuth(브라우저 팝업 로그인)는 서버에서 못 씁니다. **서비스 계정**으로 바꿔야 해요.

### GSC용 서비스 계정
1. [console.cloud.google.com](https://console.cloud.google.com) → 기존 프로젝트(KMA-studio-SEO)
2. API 및 서비스 → 사용자 인증 정보 → **사용자 인증 정보 만들기 → 서비스 계정**
3. 이름 아무거나(예: `signal-server`) → 만들기
4. 만들어진 서비스 계정 → **키 → 키 추가 → JSON** → 다운로드
5. **Search Console** 사이트 설정 → 사용자 추가 → 방금 만든 서비스 계정 이메일(`...@...iam.gserviceaccount.com`)을 **소유자 또는 전체 권한**으로 추가
6. 다운로드한 JSON 파일을 **텍스트 에디터로 열어서 전체 내용을 복사** → Render의 `GSC_SERVICE_ACCOUNT_JSON`에 한 줄로 붙여넣기

### GA4용 서비스 계정
- 같은 서비스 계정을 재사용해도 됩니다 (스코프만 다름)
- **GA4 관리자 → 속성 액세스 관리**에서 같은 서비스 계정 이메일을 **뷰어**로 추가
- JSON 내용을 `GA4_SERVICE_ACCOUNT_JSON`에 붙여넣기 (GSC와 같은 파일 그대로 써도 됩니다)

---

## 5. 자동 새로고침 (cron-job.org)

1. [cron-job.org](https://cron-job.org) 무료 가입
2. **Create cronjob**
3. URL: `https://본인앱주소.onrender.com/refresh?token=REFRESH_TOKEN에_넣은_값`
4. 실행 시각: 09:00 하나, 16:00 하나 — 총 2개 등록
5. 저장

이후로는 사람이 손 안 대도 매일 자동으로 데이터가 갱신됩니다.

**주의**: Render 무료 플랜은 15분 미접속 시 서버가 잠듭니다. cron-job.org가 호출하면 깨어나는 데 최대 1분 정도 걸릴 수 있어요 (에러 아님, 정상).

**중요 — DB 관련 주의**: Render 무료 웹서비스는 **재배포될 때마다 파일시스템이 초기화**됩니다. 즉 코드를 다시 push하면 SQLite에 쌓인 이력(`app.db`)이 날아갑니다. 지금은 "테스트 용도"라 하셨으니 큰 문제는 아니지만, 나중에 이력을 계속 유지하고 싶어지면:
- Render의 "Persistent Disk" 기능(유료, 월 $1부터)을 붙이거나
- Render의 관리형 Postgres(무료 30일 후 만료)로 옮기거나
- 아예 외부 무료 DB(Supabase, Neon 같은 곳의 무료 Postgres)를 쓰는 방법이 있습니다

지금 구조(`db.py`)는 SQLite 함수 호출부만 감싸놓은 거라, 나중에 Postgres로 바꿔도 `db.py` 파일 하나만 고치면 됩니다.

---

## 6. 접속

`https://본인앱주소.onrender.com` 접속 → 로그인(APP_USERNAME/APP_PASSWORD) → 대시보드.
광고 리포트는 `/ads` 경로.

---

## 문제 해결

| 증상 | 원인 |
|---|---|
| "아직 데이터가 없습니다" | 아직 `/refresh`를 한 번도 호출 안 함 — 직접 브라우저로 `/refresh?token=...` 방문 |
| 첫 접속이 느림(1분) | 무료 플랜 슬립 상태에서 깨어나는 중, 정상 |
| GSC/GA4가 계속 데모로 뜸 | `_MOCK` 값이 `false`인지, JSON을 통째로 넣었는지 확인 |
| 403 on /refresh | REFRESH_TOKEN이 cron-job.org URL의 token과 다름 |
