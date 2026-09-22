# URL 즉석분석 — 클라우드 배포 가이드

아무 URL이나 입력하면 그 자리에서 기술 SEO·웹 성능·AI 노출·GEO 산출물을
실데이터로 보여주는 웹앱. 계정 인증이 필요한 서비스는 쓰지 않으므로
서비스 계정, cron 자동화, DB 영속성 같은 걸 신경 쓸 필요가 없다.

GitHub(코드 저장) → Render(실행/호스팅) 순서.

---

## 0. 로컬에서 먼저 확인 (선택, 추천)

```powershell
python -m pip install -r requirements.txt
$env:APP_USERNAME="admin"
$env:APP_PASSWORD="test1234"
$env:SESSION_SECRET="testsecret"
uvicorn main:app --reload
```
브라우저로 http://127.0.0.1:8000 접속 → 로그인 → URL 하나 넣고 분석해보기.

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
  primary key (domain, date)
);

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
```

---

## 4. 접속

`https://앱이름-xxxx.onrender.com` 접속 → 로그인(APP_USERNAME/APP_PASSWORD) →
URL 입력창에 분석하고 싶은 사이트 주소를 넣고 분석.

**주의**: Render 무료 플랜은 15분 미접속 시 서버가 잠듭니다. 처음 접속 시 깨어나는 데
최대 1분 정도 걸릴 수 있어요 (에러 아님, 정상).

---

## 문제 해결

| 증상 | 원인 |
|---|---|
| 분석하기 눌러도 반응이 늦음 | 크롤링+PageSpeed+Gemini를 순서대로 실제 호출하느라 최대 1분 정도 걸림. 버튼이 "분석 중입니다..."로 바뀌면 정상 진행 중 |
| "웹 성능: 측정 실패" | PageSpeed API 쿼터 초과(429) 가능성 큼 — `PAGESPEED_API_KEY`를 넣으면 대부분 해결 |
| "AI 노출: GEMINI_API_KEY가 설정되지 않아..." | 아직 키를 안 넣은 것. 위 3번 참고 |
| "AI 노출: 확인 실패: ..." | Gemini API 무료 쿼터 초과 또는 일시 오류. 잠시 후 재시도 |
| "내 사이트/경쟁사/프롬프트 목록: SUPABASE_URL/SUPABASE_KEY가 설정되지 않아..." | 3-1번 SQL을 아직 안 돌렸거나 환경변수가 없는 것. 설정해도 테이블을 안 만들었으면 저장이 조용히 실패하니 SQL부터 실행 |
