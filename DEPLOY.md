# 분석 실행 — 클라우드 배포 가이드

"내 사이트" 설정에 등록해둔 사이트를 기준으로 기술 SEO·웹 성능·AI 노출·GEO
산출물을 실데이터로 보여주는 웹앱. 비용/속도가 서로 다른 수집기(크롤링/PageSpeed/
Gemini)를 목적별 페이지(개요·웹 성능·사이트 진단·AI 노출·경쟁사 비교·추이)로
나누고, 각 결과를 Supabase에 캐시해뒀다가 TTL 이내면 재계산 없이 즉시 보여준다.
계정 인증이 필요한 서비스(GSC/GA4/네이버 등)는 쓰지 않으므로 서비스 계정은
필요 없지만, 설정 저장·캐시·추이 이력 때문에 **Supabase는 사실상 필수**다
(3-1번 참고).

GitHub(코드 저장) → Render(실행/호스팅) → Supabase(설정·이력 저장) 순서.

---

## 0. 로컬에서 먼저 확인 (선택, 추천)

```powershell
python -m pip install -r requirements.txt
$env:APP_USERNAME="admin"
$env:APP_PASSWORD="test1234"
$env:SESSION_SECRET="testsecret"
$env:SUPABASE_URL="..."
$env:SUPABASE_KEY="..."
uvicorn main:app --reload
```
브라우저로 http://127.0.0.1:8000 접속 → 로그인 → **내 사이트** 설정에서 사이트 등록
→ **개요** 페이지가 바로 뜨는지 확인. (SUPABASE_URL/KEY를 아직 안 넣었으면 "설정되지
않음" 안내만 뜬다 — 3-1번부터 먼저 진행)

---

## 1. GitHub에 올리기

```powershell
git init
git add .
git commit -m "초기 커밋"
```

GitHub에서 새 저장소 만들기 (Private 추천 — Public이면 코드가 다 공개되니):
- github.com → New repository → 이름 아무거나 → **Private** 선택 → Create

```powershell
git remote add origin https://github.com/본인아이디/저장소이름.git
git branch -M main
git push -u origin main
```

`.gitignore`가 `.env` 같은 민감파일을 제외하니, **실제 키 값은 여기 안 올라갑니다.**

---

## 2. Render에 배포

1. [render.com](https://render.com) 가입 (GitHub 계정으로 바로 가입 가능, 카드 등록 불필요)
2. **New → Web Service** 클릭
3. 방금 만든 GitHub 저장소 연결(Connect) — Render가 자동으로 `render.yaml`을 읽어서 설정을 채웁니다
4. Region은 Singapore 등 가까운 곳 선택
5. **Create Web Service** 클릭 → 첫 배포 시작 (몇 분 걸림)

배포되면 `https://앱이름-xxxx.onrender.com` 같은 URL이 생깁니다.

---

## 3. 환경변수(비밀키) 채우기

Render 대시보드 → 방금 만든 서비스 → **Environment** 탭에서 값 입력:

**필수**
```
APP_USERNAME = admin
APP_PASSWORD = (본인이 정한 강력한 비밀번호)
```

**선택 (없어도 동작하지만 넣으면 더 안정적)**
```
PAGESPEED_API_KEY = ...   # 웹 성능 측정 쿼터 증가
GEMINI_API_KEY = ...      # AI 노출 체크 (없으면 이 섹션만 비활성화)
```

`GEMINI_API_KEY`는 [aistudio.google.com/apikey](https://aistudio.google.com/apikey)에서
카드 등록 없이 무료로 발급됩니다.

`PAGESPEED_API_KEY`를 발급한 것과 같은 Google Cloud 프로젝트에서 **"Chrome UX Report
API"**도 사용 설정해두면, 풀/경량 라이트하우스 감사가 둘 다 실패했을 때 마지막
안전망으로 실제 방문자 체감 속도(CrUX)만이라도 가져올 수 있습니다 (선택 사항 —
안 해도 나머지는 그대로 동작).

저장하면 자동으로 재배포됩니다.

---

## 3-1. Supabase 테이블 (선택 — 추이 그래프 · 내 사이트/경쟁사/프롬프트 저장)

`SUPABASE_URL`/`SUPABASE_KEY`를 설정했다면, Supabase 대시보드 → SQL Editor에서 아래를
한 번 실행해 테이블을 만들어주세요. 없어도 앱은 동작하지만 추이 그래프와 설정 3개
(내 사이트/경쟁사/프롬프트 목록) 페이지가 "설정되지 않음" 상태로 남습니다.

```sql
create table if not exists geo_history (
  domain text not null,
  date date not null,
  exposure_score int,
  citation_share int,
  mention_share int,
  seo_score int,
  psi_score int,
  primary key (domain, date)
);
-- 이미 geo_history를 만들어뒀다면(이전 버전 사용자) 위 create table은 그냥
-- 무시되니, 새로 추가된 컬럼만 이 두 줄로 채워주면 된다:
alter table geo_history add column if not exists seo_score int;
alter table geo_history add column if not exists psi_score int;

create table if not exists geo_site_config (
  id smallint primary key default 1,
  site_urls jsonb not null default '[]',
  brand_aliases jsonb not null default '[]',
  updated_at timestamptz not null default now()
);

create table if not exists geo_competitors (
  id bigserial primary key,
  name text not null,
  domain text not null,
  aliases jsonb not null default '[]',
  created_at timestamptz not null default now()
);

create table if not exists geo_prompts (
  id bigserial primary key,
  topic text,
  prompt text not null,
  archived boolean not null default false,
  created_at timestamptz not null default now()
);

-- geo_history가 하루치 요약 숫자 3개만 남기는 것과 달리, 이건 실행할 때마다 프롬프트별
-- 원본 결과를 한 행씩 그대로 쌓아둔다. 나중에 프롬프트별 이력이나 "경쟁사는 인용됐는데
-- 우리는 안 된 페이지" 같은 걸 만들려면 이 원본이 있어야 한다.
create table if not exists geo_prompt_runs (
  id bigserial primary key,
  domain text not null,
  date date not null,
  prompt text not null,
  topic text,
  status text not null,
  mentioned boolean,
  cited boolean,
  cited_urls jsonb not null default '[]',
  competitor_mentions jsonb not null default '{}',
  competitor_citations jsonb not null default '{}',
  created_at timestamptz not null default now()
);
create index if not exists geo_prompt_runs_domain_date_idx on geo_prompt_runs (domain, date);

-- 페이지별 분석 결과 캐시. domain+kind 기준으로 하나씩만 있고(최신 값으로 덮어씀),
-- 각 페이지가 TTL 이내면 이 값을 그대로 쓰고 재계산을 건너뛴다.
-- kind: overview / psi / sitecrawl / techcompare / ai_exposure
create table if not exists geo_cache (
  domain text not null,
  kind text not null,
  data jsonb not null,
  fetched_at timestamptz not null default now(),
  primary key (domain, kind)
);
```

---

## 3-2. 예약 갱신 (선택 — 방문 전에 미리 캐시 채워두기)

Render 무료 플랜엔 상시 크론이 없다. 대신 **GitHub Actions의 무료 scheduled workflow**로
하루 1~2번 `/internal/refresh`를 호출하면, 사용자가 접속하기 전에 Supabase 캐시가 이미
최신 상태가 돼서 모든 분석 페이지가 즉시 로딩된다. 추이 그래프가 원하는 "매일 스냅샷"도
이 호출이 자동으로 만들어준다.

1. Render 환경변수에 `REFRESH_TOKEN`을 아무 임의의 긴 문자열로 추가 (예: 32자 랜덤 문자열).
   비워두면 `/internal/refresh`는 항상 403을 반환해 아무도 못 쓴다.
2. `.github/workflows/daily-refresh.yml`이 저장소에 이미 있다 — 매일 UTC 21:00(KST 06:00)에
   자동 실행되고, Actions 탭에서 수동 실행도 가능하다.
3. GitHub 저장소 → Settings → Secrets and variables → Actions → New repository secret로 2개 추가:
   - `REFRESH_URL` — 배포된 앱 주소, 예: `https://앱이름-xxxx.onrender.com` (끝에 슬래시 없이)
   - `REFRESH_TOKEN` — 1번에서 Render에 넣은 것과 동일한 문자열

이 엔드포인트는 Gemini까지 호출하므로(설정돼 있으면) 하루 1~2회 정도로만 예약하는 게
무료 쿼터 관리에 안전하다.

---

## 4. 접속

`https://앱이름-xxxx.onrender.com` 접속 → 로그인(APP_USERNAME/APP_PASSWORD) →
**내 사이트** 설정에서 사이트 주소(+ 필요하면 경쟁사, 프롬프트)를 등록 →
왼쪽 메뉴의 **개요/웹 성능/사이트 진단/AI 노출/경쟁사 비교/추이** 각 페이지에서 확인.
개요는 항상 자동으로 뜨고, 나머지(웹 성능·사이트 진단·AI 노출)는 느리거나 쿼터가
있어서 캐시가 없을 때만 새로 계산한다 — 최신 값이 필요하면 각 페이지의 "새로고침" 클릭.

**주의**: Render 무료 플랜은 15분 미접속 시 서버가 잠듭니다. 처음 접속 시 깨어나는 데
최대 1분 정도 걸릴 수 있어요 (에러 아님, 정상).

---

## 문제 해결

| 증상 | 원인 |
|---|---|
| 웹 성능/사이트 진단/AI 노출 페이지가 오래 걸림 | 캐시가 없거나 만료돼서 실제로 새로 계산 중인 것 — 각각 PageSpeed(최대 2분)/사이트 크롤/Gemini 호출이라 느릴 수 있다. 한 번 계산되면 TTL 동안(6~24시간) 재방문 시 즉시 뜬다 |
| "웹 성능: 측정 실패" | 풀 라이트하우스 감사(최대 90초)가 실패하면 자동으로 더 가벼운 감사(성능만, 최대 70초)를, 그것도 실패하면 CrUX 실사용자 데이터만이라도 시도한다. 그 셋 다 실패했고 예전에 성공한 값도 없을 때만 진짜 "측정 실패"가 뜬다 — 대개 `PAGESPEED_API_KEY`를 넣으면 훨씬 안정적 |
| 웹 성능에 "이전 측정값을 표시합니다" 안내 | 방금 재시도했는데 실패해서, 예전에 성공했던 값을 대신 보여주는 것 — 의도된 동작이다. 잠시 후 새로고침하면 다시 시도된다 |
| "AI 노출: GEMINI_API_KEY가 설정되지 않아..." | 아직 키를 안 넣은 것. 위 3번 참고 |
| "AI 노출: 확인 실패: ..." | Gemini API 무료 쿼터 초과 또는 일시 오류. 잠시 후 새로고침 |
| "내 사이트/경쟁사/프롬프트 목록: SUPABASE_URL/SUPABASE_KEY가 설정되지 않아..." | 3-1번 SQL을 아직 안 돌렸거나 환경변수가 없는 것. 설정해도 테이블을 안 만들었으면 저장이 조용히 실패하니 SQL부터 실행 |
| `/internal/refresh`가 403 | `REFRESH_TOKEN` 환경변수가 없거나 요청한 token 값과 다른 것. 3-2번 참고 |
| 새로고침을 눌러도 값이 안 바뀜 | 캐시 TTL 이내라 의도적으로 재계산을 건너뛴 것. 각 페이지 URL 끝에 `?refresh=1`을 붙이면 강제로 새로 계산한다 |
