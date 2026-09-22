"""
Signal 대시보드 웹앱.
로컬 스크립트(run_gsc.py, run_ads.py)를 웹서비스로 감싼 버전.

라우트:
  GET  /login          로그인 폼
  POST /login          로그인 처리
  GET  /logout
  GET  /                검색성과+처방 대시보드 (로그인 필요)
  GET  /ads              GA4+광고 리포트 (로그인 필요)
  GET  /refresh?token=..  데이터 새로고침 (cron-job.org가 호출, REFRESH_TOKEN으로 보호)
"""

import html
import os
import tempfile
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from starlette.middleware.sessions import SessionMiddleware

import config
import db
from collectors.gsc import collect_gsc
from collectors.ga4 import collect_ga4
from collectors.naver_ads import collect_naver_ads
from collectors.insights import build_insights
from collectors.tech_audit import audit_technical
from collectors.prescribe import prescribe
from collectors.serp import rank_keywords
from collectors.competitor import compare_sites
from collectors.tracker import growth_summary
from generators.artifacts import generate_all
from generators.scoring import score_categories, score_tier
from layout import sidebar_shell
from render_gsc import render_gsc
from render_ads import render_ads_report

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=config.SESSION_SECRET)

db.init_db()


# ---------------- 인증 ----------------

def _require_login(request: Request):
    return request.session.get("authenticated") is True


LOGIN_PAGE = """
<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>로그인</title>
<style>
body{{font-family:-apple-system,'Pretendard',sans-serif;background:#FAFAF9;color:#14161A;
  display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
.box{{width:320px;border:1px solid #E4E4E1;padding:32px;border-radius:2px}}
h1{{font-size:16px;font-weight:500;margin:0 0 20px}}
input{{width:100%;padding:9px 10px;margin-bottom:10px;border:1px solid #E4E4E1;
  border-radius:2px;font-size:14px;box-sizing:border-box}}
button{{width:100%;padding:10px;background:#14161A;color:#fff;border:none;
  border-radius:2px;font-size:14px;cursor:pointer}}
.err{{color:#c5221f;font-size:12.5px;margin-bottom:10px}}
</style></head><body>
<form class="box" method="post" action="/login">
  <h1>Signal 로그인</h1>
  {error}
  <input name="username" placeholder="아이디" autofocus>
  <input name="password" type="password" placeholder="비밀번호">
  <button type="submit">로그인</button>
</form>
</body></html>
"""


@app.get("/login", response_class=HTMLResponse)
def login_form():
    return LOGIN_PAGE.format(error="")


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if username == config.APP_USERNAME and password == config.APP_PASSWORD:
        request.session["authenticated"] = True
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(LOGIN_PAGE.format(
        error='<div class="err">아이디 또는 비밀번호가 틀렸습니다.</div>'
    ))


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---------------- 대시보드 ----------------

@app.get("/", response_class=HTMLResponse)
def dashboard_shell(request: Request):
    if not _require_login(request):
        return RedirectResponse("/login", status_code=303)
    return HTMLResponse(sidebar_shell("dashboard", "/_content/dashboard", title="리포트"))


@app.get("/_content/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    if not _require_login(request):
        return RedirectResponse("/login", status_code=303)

    gsc_snap = db.latest_snapshot("gsc")
    if not gsc_snap:
        return HTMLResponse(
            "<p style='font-family:sans-serif;padding:40px'>아직 데이터가 없습니다. "
            "<a href='/refresh?token=" + config.REFRESH_TOKEN + "'>지금 새로고침</a></p>"
        )
    gsc = gsc_snap["data"]
    insights = build_insights(gsc)

    tech_snap = db.latest_snapshot("tech")
    tech = tech_snap["data"] if tech_snap else None

    comp_snap = db.latest_snapshot("competitors")
    competitors = comp_snap["data"] if comp_snap else None

    serp_snap = db.latest_snapshot("serp")
    serp = serp_snap["data"] if serp_snap else None

    prescription = prescribe(tech=tech, gsc=gsc, insights=insights)

    # 성장 추적: DB의 keyword_history를 growth_summary 형태로 변환
    growth = None
    if config.TARGET_KEYWORDS:
        history_map = {}
        for kw in config.TARGET_KEYWORDS:
            points = db.keyword_history(kw)
            history_map[kw] = [
                {"date": p["date"], "impressions": p["impressions"], "clicks": p["clicks"],
                 "gsc_position": p["gsc_position"], "serp_rank": p["serp_rank"]}
                for p in points
            ]
        growth = growth_summary(history_map, config.TARGET_KEYWORDS)

    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as f:
        out_path = f.name
    render_gsc(gsc, out_path, insights=insights, prescription=prescription,
               competitors=competitors, serp=serp, growth=growth)
    html = open(out_path, encoding="utf-8").read()
    os.unlink(out_path)
    return HTMLResponse(html)


@app.get("/ads", response_class=HTMLResponse)
def ads_shell(request: Request):
    if not _require_login(request):
        return RedirectResponse("/login", status_code=303)
    return HTMLResponse(sidebar_shell("ads", "/_content/ads", title="광고 리포트"))


@app.get("/_content/ads", response_class=HTMLResponse)
def ads_report(request: Request):
    if not _require_login(request):
        return RedirectResponse("/login", status_code=303)

    ga4_snap = db.latest_snapshot("ga4")
    naver_snap = db.latest_snapshot("naver")
    if not ga4_snap or not naver_snap:
        return HTMLResponse(
            "<p style='font-family:sans-serif;padding:40px'>아직 데이터가 없습니다. "
            "<a href='/refresh?token=" + config.REFRESH_TOKEN + "'>지금 새로고침</a></p>"
        )

    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as f:
        out_path = f.name
    render_ads_report(ga4_snap["data"], naver_snap["data"], out_path,
                       google_enabled=False, meta_enabled=False)
    html = open(out_path, encoding="utf-8").read()
    os.unlink(out_path)
    return HTMLResponse(html)


# ---------------- 새로고침 (cron-job.org가 호출) ----------------

@app.get("/refresh", response_class=PlainTextResponse)
def refresh(token: str = ""):
    if not config.REFRESH_TOKEN or token != config.REFRESH_TOKEN:
        return PlainTextResponse("forbidden", status_code=403)

    log = []

    # 1) GSC
    gsc = collect_gsc(config.GSC_SITE_URL, auth=config.gsc_auth(), mock=config.GSC_MOCK)
    db.save_snapshot("gsc", gsc)
    log.append(f"GSC: {gsc['source']} 클릭 {gsc['totals']['clicks']}")

    # 2) 기술 진단
    try:
        import requests as _rq
        from collectors.onpage import USER_AGENT, TIMEOUT
        r = _rq.get(config.MY_URL, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        tech = audit_technical(r.url, r.text)
        db.save_snapshot("tech", tech)
        log.append(f"기술진단: H1 {tech['h1_count']}")

        # 2-1) GEO 산출물(robots.txt/llms.txt/JSON-LD) 자동 생성 — LLM 미사용, 규칙 기반
        artifacts = generate_all(tech, brand_name=config.BRAND_NAME or None,
                                  social_urls=config.SOCIAL_URLS or None)
        db.save_snapshot("artifacts", artifacts)
        log.append("GEO 산출물 생성 완료")
    except Exception as e:
        log.append(f"기술진단 실패: {e}")

    # 3) 경쟁사 비교
    if config.COMPETITOR_URLS:
        try:
            competitors = compare_sites(config.MY_URL, config.COMPETITOR_URLS)
            db.save_snapshot("competitors", competitors)
            log.append(f"경쟁사 비교: {len(competitors)}개")
        except Exception as e:
            log.append(f"경쟁사 비교 실패: {e}")

    # 4) SERP + 타겟 키워드 기록
    if config.TARGET_KEYWORDS:
        try:
            serp = rank_keywords(config.TARGET_KEYWORDS,
                                  config.GSC_SITE_URL.replace("sc-domain:", "").rstrip("/"),
                                  api_key=config.SERPAPI_KEY or None)
            db.save_snapshot("serp", serp)
            log.append(f"SERP: {serp['quota_note']}")

            today = datetime.now(timezone.utc).date().isoformat()
            gsc_queries = {q["key"]: q for q in gsc.get("top_queries", [])}
            serp_items = {it["keyword"]: it for it in serp["items"]}
            for kw in config.TARGET_KEYWORDS:
                gq = gsc_queries.get(kw)
                si = serp_items.get(kw)
                db.save_keyword_point(
                    kw, today,
                    impressions=gq["impressions"] if gq else 0,
                    clicks=gq["clicks"] if gq else 0,
                    gsc_position=gq["position"] if gq else None,
                    serp_rank=si["my_rank"] if si else None,
                )
        except Exception as e:
            log.append(f"SERP 실패: {e}")

    # 5) GA4
    ga4 = collect_ga4(config.GA4_PROPERTY_ID, auth=config.ga4_auth(), mock=config.GA4_MOCK)
    db.save_snapshot("ga4", ga4)
    log.append(f"GA4: {ga4['source']} 조회수 {ga4['totals']['views']}")

    # 6) 네이버
    naver = collect_naver_ads(auth=config.naver_auth(), mock=config.NAVER_MOCK)
    db.save_snapshot("naver", naver)
    log.append(f"네이버: {naver['source']}")

    return PlainTextResponse("\n".join(log))


@app.get("/artifacts", response_class=HTMLResponse)
def artifacts_page(request: Request):
    if not _require_login(request):
        return RedirectResponse("/login", status_code=303)

    snap = db.latest_snapshot("artifacts")
    if not snap:
        return HTMLResponse(
            "<p style='font-family:sans-serif;padding:40px'>아직 생성된 산출물이 없습니다. "
            "<a href='/refresh?token=" + config.REFRESH_TOKEN + "'>지금 새로고침</a></p>"
        )
    a = snap["data"]
    html = ARTIFACTS_PAGE.format(
        generated_at=a["generated_at"][:16].replace("T", " "),
        robots=_esc_html(a["robots_txt"]),
        llms=_esc_html(a["llms_txt"]),
        jsonld=_esc_html(a["json_ld"]),
    )
    return HTMLResponse(html)


def _esc_html(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


ARTIFACTS_PAGE = """
<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GEO 산출물</title>
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css');
:root{{--bg:#FAFAF9;--line:#E4E4E1;--ink:#14161A;--dim:#5B5F66;--dim2:#9A9DA3;--accent:#1E5E46}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font-family:'Pretendard',-apple-system,sans-serif;
  font-size:14px;line-height:1.6}}
.app{{max-width:1080px;margin:0 auto;padding:40px 24px 64px}}
.topbar{{display:flex;align-items:center;justify-content:space-between;padding:0 0 24px;
  border-bottom:1px solid var(--line);margin-bottom:32px}}
h1{{font-size:18px;font-weight:500;margin:0}}
.sub{{font-size:12.5px;color:var(--dim);margin-top:4px}}
nav a{{color:var(--dim);text-decoration:none;font-size:13px;margin-left:20px}}
.card{{border:1px solid var(--line);border-radius:2px;padding:24px;margin-top:20px}}
.card-h{{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}}
.card-h h2{{font-size:15px;font-weight:500;margin:0}}
button.copy{{border:1px solid var(--ink);background:transparent;color:var(--ink);
  padding:6px 14px;font-size:12.5px;border-radius:2px;cursor:pointer;font-family:inherit}}
button.copy:hover{{background:var(--ink);color:var(--bg)}}
pre{{background:#F3F3F1;border:1px solid var(--line);border-radius:2px;padding:16px;
  overflow-x:auto;font-size:12.5px;line-height:1.6;max-height:360px;overflow-y:auto;
  font-family:ui-monospace,'SF Mono',Menlo,monospace;white-space:pre-wrap}}
.note{{font-size:12px;color:var(--dim2);margin-top:12px;line-height:1.6}}
</style></head><body>
<div class="app">
  <header class="topbar">
    <div><h1>GEO 산출물</h1><div class="sub">생성 {generated_at} · robots.txt / llms.txt / JSON-LD</div></div>
    <nav><a href="/">대시보드</a><a href="/ads">광고 리포트</a><a href="/logout">로그아웃</a></nav>
  </header>

  <div class="card">
    <div class="card-h"><h2>권장 robots.txt</h2><button class="copy" onclick="cp('robots')">복사</button></div>
    <pre id="robots">{robots}</pre>
    <div class="note">사이트 루트(/robots.txt)에 배포하면 GPTBot·ClaudeBot·PerplexityBot 등 AI 크롤러의 접근을 명시적으로 허용합니다.</div>
  </div>

  <div class="card">
    <div class="card-h"><h2>생성된 llms.txt</h2><button class="copy" onclick="cp('llms')">복사</button></div>
    <pre id="llms">{llms}</pre>
    <div class="note">사이트 루트(/llms.txt)에 배포하면 LLM이 사이트를 빠르게 요약 이해하는 데 참고합니다.</div>
  </div>

  <div class="card">
    <div class="card-h"><h2>생성된 JSON-LD</h2><button class="copy" onclick="cp('jsonld')">복사</button></div>
    <pre id="jsonld">{jsonld}</pre>
    <div class="note">페이지 &lt;head&gt;에 &lt;script type="application/ld+json"&gt;...&lt;/script&gt;로 감싸 삽입하세요.</div>
  </div>
</div>
<script>
function cp(id) {{
  const text = document.getElementById(id).innerText;
  navigator.clipboard.writeText(text);
  event.target.innerText = "복사됨";
  setTimeout(() => event.target.innerText = "복사", 1200);
}}
</script>
</body></html>
"""


@app.get("/analyze", response_class=HTMLResponse)
def analyze_shell(request: Request):
    if not _require_login(request):
        return RedirectResponse("/login", status_code=303)
    return sidebar_shell("analyze", "/_content/analyze", title="URL 분석")


@app.get("/_content/analyze", response_class=HTMLResponse)
def analyze_content(request: Request, url: str = ""):
    if not _require_login(request):
        return RedirectResponse("/login", status_code=303)

    # url이 비어있으면 입력 폼만 보여준다
    if not url.strip():
        return HTMLResponse(ANALYZE_FORM_PAGE)

    import requests as _rq
    from collectors.onpage import USER_AGENT, TIMEOUT
    from collectors.tech_audit import audit_technical

    target = url.strip()
    if not target.startswith("http"):
        target = "https://" + target

    try:
        r = _rq.get(target, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        tech = audit_technical(r.url, r.text)
    except Exception as e:
        return HTMLResponse(ANALYZE_FORM_PAGE.replace(
            "{error}", f"<div class='err'>크롤 실패: {type(e).__name__} — URL을 확인해주세요.</div>"
        ).replace("{prev_url}", target))

    scores = score_categories(tech)

    from collectors.pagespeed import collect_pagespeed
    psi = collect_pagespeed(target, api_key=config.PAGESPEED_API_KEY or None)

    rx = prescribe(tech=tech)
    artifacts = generate_all(tech, brand_name=config.BRAND_NAME or None,
                              social_urls=config.SOCIAL_URLS or None)

    # 점수 카드 HTML
    score_cards = ""
    for key, s in scores.items():
        score_cards += f"""
        <div class="score-card">
          <div class="score-label">{s['label']}</div>
          <div class="score-num">{s['score']}<span>/100</span></div>
          <div class="score-tier">{score_tier(s['score'])}</div>
        </div>"""

    # 웹 성능 카드 — PageSpeed Insights(Lighthouse) 실측값. LIVE일 때만 점수를 보여준다.
    if psi["source"].startswith("ERROR"):
        score_cards += f"""
        <div class="score-card">
          <div class="score-label">웹 성능</div>
          <div class="score-num">측정 실패</div>
          <div class="score-detail">{html.escape(psi.get('detail') or '알 수 없는 오류')}</div>
        </div>"""
    elif psi["source"] == "LIVE":
        lcp = psi["lcp"] if psi["lcp"] is not None else "—"
        cls = psi["cls"] if psi["cls"] is not None else "—"
        tbt = psi["tbt"] if psi["tbt"] is not None else "—"
        score_cards += f"""
        <div class="score-card">
          <div class="score-label">웹 성능</div>
          <div class="score-num">{psi['performance']}<span>/100</span></div>
          <div class="score-tier">{score_tier(psi['performance'])}</div>
          <div class="score-detail">LCP {lcp} · CLS {cls} · TBT {tbt}</div>
        </div>"""

    # 이슈(처방) 리스트 HTML — prescribe()가 만든 기술 항목만
    issue_rows = ""
    for i, t in enumerate(rx["todos"], 1):
        issue_rows += f"""
        <div class="issue-row">
          <span class="issue-num">{i}</span>
          <div>
            <div class="issue-title">{html.escape(t['title'])}</div>
            <div class="issue-why">{html.escape(t['why'])}</div>
          </div>
        </div>"""
    if not issue_rows:
        issue_rows = '<div class="issue-empty">발견된 이슈가 없습니다.</div>'

    page = ANALYZE_RESULT_PAGE.format(
        url=html.escape(target),
        score_cards=score_cards,
        issue_rows=issue_rows,
        robots=_esc_html(artifacts["robots_txt"]),
        llms=_esc_html(artifacts["llms_txt"]),
        jsonld=_esc_html(artifacts["json_ld"]),
    )
    return HTMLResponse(page)


ANALYZE_FORM_PAGE = """
<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css');
body{font-family:'Pretendard',sans-serif;background:#FAFAF9;color:#14161A;
  display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
.box{width:420px}
h1{font-size:18px;font-weight:500;margin:0 0 6px}
p{font-size:13px;color:#5B5F66;margin:0 0 20px}
input{width:100%;padding:11px 12px;border:1px solid #E4E4E1;border-radius:2px;
  font-size:14px;box-sizing:border-box;margin-bottom:10px}
button{width:100%;padding:11px;background:#14161A;color:#fff;border:none;
  border-radius:2px;font-size:14px;cursor:pointer}
.err{color:#c5221f;font-size:12.5px;margin-bottom:10px}
</style></head><body>
<form class="box" method="get" action="/_content/analyze">
  <h1>URL 분석</h1>
  <p>분석할 사이트 주소를 입력하면 기술 진단과 GEO 산출물을 바로 생성합니다.</p>
  {error}
  <input name="url" placeholder="https://example.com" value="{prev_url}" autofocus>
  <button type="submit">분석하기</button>
</form>
</body></html>
"""
ANALYZE_FORM_PAGE = ANALYZE_FORM_PAGE.replace("{error}", "").replace("{prev_url}", "")


ANALYZE_RESULT_PAGE = """
<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css');
:root{{--bg:#FAFAF9;--line:#E4E4E1;--ink:#14161A;--dim:#5B5F66;--dim2:#9A9DA3;--accent:#1E5E46}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font-family:'Pretendard',sans-serif;
  font-size:14px;line-height:1.6}}
.app{{max-width:900px;margin:0 auto;padding:32px 24px 64px}}
.topbar{{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px}}
.url-label{{font-size:13px;color:var(--dim)}}
a.reanalyze{{font-size:12.5px;color:var(--ink);border-bottom:1px solid var(--line)}}
.scores{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:24px}}
.score-card{{border:1px solid var(--line);border-radius:2px;padding:18px}}
.score-label{{font-size:12.5px;color:var(--dim)}}
.score-num{{font-size:28px;font-weight:500;margin:8px 0 4px}}
.score-num span{{font-size:14px;color:var(--dim2);font-weight:400}}
.score-tier{{font-size:12px;color:var(--dim2)}}
.score-detail{{font-size:11px;color:var(--dim2);margin-top:6px}}
.card{{border:1px solid var(--line);border-radius:2px;padding:24px;margin-top:16px}}
.card h2{{font-size:15px;font-weight:500;margin:0 0 14px}}
.issue-row{{display:flex;gap:12px;padding:12px 0;border-top:1px solid #ECECE9}}
.issue-row:first-child{{border-top:none}}
.issue-num{{font-size:12px;color:var(--dim2);flex:0 0 auto}}
.issue-title{{font-size:13.5px;font-weight:500}}
.issue-why{{font-size:12.5px;color:var(--dim);margin-top:3px}}
.issue-empty{{color:var(--dim2);font-size:13px}}
.card-h{{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}}
button.copy{{border:1px solid var(--ink);background:transparent;color:var(--ink);
  padding:5px 12px;font-size:12px;border-radius:2px;cursor:pointer}}
pre{{background:#F3F3F1;border:1px solid var(--line);border-radius:2px;padding:14px;
  overflow:auto;font-size:12px;max-height:280px;font-family:ui-monospace,monospace;
  white-space:pre-wrap}}
</style></head><body>
<div class="app">
  <div class="topbar">
    <div class="url-label">분석 대상: {url}</div>
    <a class="reanalyze" href="/_content/analyze">다른 URL 분석하기</a>
  </div>
  <div class="scores">{score_cards}</div>
  <div class="card">
    <h2>발견된 이슈</h2>
    {issue_rows}
  </div>
  <div class="card">
    <div class="card-h"><h2>robots.txt</h2><button class="copy" onclick="cp('r')">복사</button></div>
    <pre id="r">{robots}</pre>
  </div>
  <div class="card">
    <div class="card-h"><h2>llms.txt</h2><button class="copy" onclick="cp('l')">복사</button></div>
    <pre id="l">{llms}</pre>
  </div>
  <div class="card">
    <div class="card-h"><h2>JSON-LD</h2><button class="copy" onclick="cp('j')">복사</button></div>
    <pre id="j">{jsonld}</pre>
  </div>
</div>
<script>
function cp(id){{
  navigator.clipboard.writeText(document.getElementById(id).innerText);
  event.target.innerText="복사됨"; setTimeout(()=>event.target.innerText="복사",1200);
}}
</script>
</body></html>
"""


@app.get("/health", response_class=PlainTextResponse)
def health():
    return "ok"
