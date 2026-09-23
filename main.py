"""
분석 실행 웹앱.
로그인한 사용자가 "내 사이트" 설정에 등록해둔 사이트를 기준으로 그 자리에서 크롤링해서
기술 SEO 점수 · 웹 성능(PageSpeed) · AI 노출(Gemini) · GEO 산출물(robots.txt/llms.txt/JSON-LD)을
전부 실데이터로 보여준다. 계정 인증이 필요한 서비스(GSC/GA4/네이버 등)는 애초에
"임의의 URL"에 적용할 수 없는 구조라 이 앱에는 없다 — 소유권 인증 없이는 그 데이터를
아무도 내줄 수 없기 때문.

라우트:
  GET  /login          로그인 폼
  POST /login          로그인 처리
  GET  /logout
  GET  /                분석 실행 셸 (로그인 필요)
  GET  /_content/analyze          등록된 사이트 요약 + 실행 버튼 (iframe 안에서 로드됨)
  GET  /_content/analyze?run=1    실제 분석 처리(스트리밍)
  GET  /settings/site, /settings/competitors, /settings/prompts  저장 설정 (로그인 필요)
  GET  /health
"""

import base64
import html
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import requests
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse, StreamingResponse
from starlette.middleware.sessions import SessionMiddleware

import config
from collectors import settings_store
from collectors.tech_audit import audit_technical
from collectors.onpage import crawl_site, USER_AGENT, TIMEOUT
from collectors.prescribe import prescribe
from collectors.pagespeed import collect_pagespeed
from collectors.geo_gemini import generate_prompts, run_geo_visibility, guess_brand_name
from collectors.geo_status import check_current_geo_status
from collectors.history_store import save_snapshot, save_prompt_runs, get_history
from generators.artifacts import generate_all
from generators.scoring import score_categories, score_tier
from layout import sidebar_shell

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=config.SESSION_SECRET)


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
  <h1>로그인</h1>
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


# ---------------- 분석 실행 ----------------

@app.get("/", response_class=HTMLResponse)
def analyze_shell(request: Request):
    if not _require_login(request):
        return RedirectResponse("/login", status_code=303)
    return HTMLResponse(sidebar_shell("analyze", "/_content/analyze", title="분석 실행"))


@app.get("/analyze")
def analyze_legacy_redirect():
    return RedirectResponse("/", status_code=301)


@app.get("/monitor")
@app.get("/ads")
@app.get("/artifacts")
def removed_feature_redirect():
    return RedirectResponse("/", status_code=301)


@app.get("/_content/analyze", response_class=HTMLResponse)
def analyze_content(request: Request, run: str = ""):
    if not _require_login(request):
        return RedirectResponse("/login", status_code=303)

    if not settings_store.configured(config.SUPABASE_URL, config.SUPABASE_KEY):
        return HTMLResponse(_settings_unconfigured_page("분석 실행"))

    site_cfg = settings_store.get_site_config(config.SUPABASE_URL, config.SUPABASE_KEY)
    if not site_cfg["site_urls"]:
        return HTMLResponse(_render_no_site_page())

    target = site_cfg["site_urls"][0]
    saved_competitors = settings_store.list_competitors(config.SUPABASE_URL, config.SUPABASE_KEY)
    saved_prompts = settings_store.list_prompts(config.SUPABASE_URL, config.SUPABASE_KEY, include_archived=False)

    # run=1이 없으면 아직 실행 전 — 뭘 분석하게 되는지 요약만 보여주고 실행 버튼을 누르게 한다.
    if not run:
        return HTMLResponse(_render_analyze_ready_page(target, site_cfg, saved_competitors, saved_prompts))

    try:
        r = requests.get(target, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        tech = audit_technical(r.url, r.text)
        tech["_security"] = _check_security_headers(r)
    except Exception as e:
        return HTMLResponse(_render_analyze_error_page(target, f"{type(e).__name__}: {e}"))

    # 여기까지는 빠르고(크롤링 1번) 로컬 계산이라 즉시 끝난다. 느린 건 전부
    # 스트리밍 응답 안에서 병렬로 처리하면서 단계마다 화면을 채워나간다.
    scores = score_categories(tech)
    rx = prescribe(tech=tech)
    brand_name = guess_brand_name(tech) or target
    artifacts = generate_all(tech, brand_name=brand_name or None, social_urls=None)

    # 설정에 저장된 브랜드 별칭·사이트 URL을 항상 분석에 반영한다
    # (이 배포는 조직 1개 전용이라 워크스페이스 구분 없이 전부 적용).
    brand_names = [brand_name] + [a for a in site_cfg["brand_aliases"] if a != brand_name]
    brand_domains = [target] + [u for u in site_cfg["site_urls"] if u != target]

    return StreamingResponse(
        _stream_analyze(target, r.text, tech, scores, rx, brand_names, brand_domains, artifacts,
                         saved_competitors, saved_prompts),
        media_type="text/html",
    )


# ---------------- 분석 결과 조각 렌더링 헬퍼 (스트리밍에서 단계별로 호출됨) ----------------

def _render_seo_score_cards(scores):
    out = ""
    for key, s in scores.items():
        out += f"""
        <div class="score-card">
          <div class="score-label">{s['label']}</div>
          <div class="score-num">{s['score']}<span>/100</span></div>
          <div class="score-tier">{score_tier(s['score'])}</div>
        </div>"""
    return out


def _render_psi_card(psi):
    if psi["source"].startswith("ERROR"):
        return f"""
        <div class="score-card" id="ph-psi">
          <div class="score-label">웹 성능</div>
          <div class="score-num">측정 실패</div>
          <div class="score-detail">{html.escape(psi.get('detail') or '알 수 없는 오류')}</div>
        </div>"""
    if psi["source"] == "LIVE" and psi["performance"] is None:
        return """
        <div class="score-card" id="ph-psi">
          <div class="score-label">웹 성능</div>
          <div class="score-num">측정 불가</div>
          <div class="score-detail">응답은 왔지만 성능 점수가 비어 있습니다.</div>
        </div>"""
    lcp = psi["lcp"] if psi["lcp"] is not None else "—"
    cls = psi["cls"] if psi["cls"] is not None else "—"
    tbt = psi["tbt"] if psi["tbt"] is not None else "—"
    return f"""
    <div class="score-card" id="ph-psi">
      <div class="score-label">웹 성능</div>
      <div class="score-num">{psi['performance']}<span>/100</span></div>
      <div class="score-tier">{score_tier(psi['performance'])}</div>
      <div class="score-detail">LCP {lcp} · CLS {cls} · TBT {tbt}</div>
    </div>"""


_CRUX_TIER_KO = {"FAST": "좋음", "AVERAGE": "보통", "SLOW": "나쁨"}


def _render_psi_detail_card(psi):
    """Lighthouse의 접근성/권장사항/SEO 점수 + 실제 크롬 사용자 체감 속도(CrUX) +
    개선 여지가 큰 항목(opportunities) — 같은 PSI 호출에 이미 들어있는데 안 쓰던 것들."""
    if psi["source"].startswith("ERROR") or psi.get("performance") is None:
        return '<div id="ph-psi-detail"></div>'

    other_scores = ""
    for key, label in (("accessibility", "접근성"), ("best_practices", "권장사항"), ("seo", "SEO")):
        v = psi.get(key)
        if v is not None:
            other_scores += (f'<div class="share-row"><span>{label}</span>'
                              f'<span>{v}/100 · {score_tier(v)}</span></div>')

    field_html = ""
    fd = psi.get("field_data")
    if fd:
        scope = "이 페이지" if fd["level"] == "page" else "도메인 전체 집계"
        rows = ""
        if fd.get("lcp_ms") is not None:
            tier = _CRUX_TIER_KO.get(fd.get("lcp_category"), fd.get("lcp_category") or "—")
            rows += f'<div class="share-row"><span>LCP (최대 콘텐츠풀 페인트)</span><span>{fd["lcp_ms"]}ms · {tier}</span></div>'
        if fd.get("cls") is not None:
            tier = _CRUX_TIER_KO.get(fd.get("cls_category"), fd.get("cls_category") or "—")
            rows += f'<div class="share-row"><span>CLS (레이아웃 밀림)</span><span>{fd["cls"]} · {tier}</span></div>'
        if fd.get("inp_ms") is not None:
            tier = _CRUX_TIER_KO.get(fd.get("inp_category"), fd.get("inp_category") or "—")
            rows += f'<div class="share-row"><span>INP (상호작용 응답성)</span><span>{fd["inp_ms"]}ms · {tier}</span></div>'
        if rows:
            field_html = f"""
            <div class="cite-list-title">실제 방문자 체감 속도 ({scope} · Chrome 사용자 데이터)</div>
            {rows}"""

    opp_html = ""
    opps = psi.get("opportunities") or []
    if opps:
        opp_rows = "".join(
            f'<div class="issue-row"><span class="issue-num">{i}</span>'
            f'<div><div class="issue-title">{html.escape(o["title"] or "")}</div>'
            f'<div class="issue-why">예상 절감: {html.escape(o["display_value"] or "—")}</div></div></div>'
            for i, o in enumerate(opps, 1)
        )
        opp_html = f'<div class="cite-list-title">개선 여지가 큰 항목</div>{opp_rows}'

    if not (other_scores or field_html or opp_html):
        return '<div id="ph-psi-detail"></div>'

    return f"""
    <div class="card" id="ph-psi-detail">
      <h2>웹 성능 상세 (Lighthouse)</h2>
      {other_scores}
      {field_html}
      {opp_html}
    </div>"""


SECURITY_HEADERS = [
    ("Strict-Transport-Security", "HSTS (HTTPS 강제)"),
    ("X-Content-Type-Options", "MIME 스니핑 방지"),
    ("X-Frame-Options", "클릭재킹 방지"),
    ("Content-Security-Policy", "콘텐츠 보안 정책(CSP)"),
]


def _check_security_headers(resp):
    """이미 받아온 응답의 헤더만 읽는다 — 추가 요청·외부 API 없이 즉시 계산되는 위생 체크."""
    present, missing = [], []
    for key, label in SECURITY_HEADERS:
        (present if key in resp.headers else missing).append(label)
    return {"is_https": resp.url.startswith("https://"), "present": present, "missing": missing}


def _render_tech_detail_card(tech):
    """audit_technical() 원본 수치를 그대로 노출한다. 크롤링 시점에 이미 다 계산해두고
    3개 점수·이슈목록으로만 요약해버리던 것들 — Gemini/PSI 쿼터와 무관하게 항상 나온다."""
    def _tag_row(ok, label):
        cls = "tag-yes" if ok else "tag-no"
        val = "있음" if ok else "없음"
        return f'<div class="share-row"><span>{html.escape(label)}</span><span class="tag {cls}">{val}</span></div>'

    semantic_used = {k: v for k, v in tech["semantic_counts"].items() if v > 0}
    semantic_str = ", ".join(f"{k} {v}개" for k, v in semantic_used.items()) or "없음"
    heading_str = ", ".join(f"h{i} {tech['headings'][f'h{i}']}개" for i in range(1, 7) if tech["headings"][f"h{i}"] > 0) or "없음"
    img_str = (f"총 {tech['img_total']}개 · alt 누락 {tech['img_no_alt_pct']}% · "
               f"lazy-load {tech['img_lazy']}개 · 최신 포맷/최적화 {tech['img_modern_pct']}%"
               if tech["img_total"] else "페이지에 이미지 없음")

    sec = tech.get("_security")
    sec_html = ""
    if sec:
        sec_html = _tag_row(sec["is_https"], "HTTPS 사용")
        for key, label in SECURITY_HEADERS:
            sec_html += _tag_row(label in sec["present"], label)

    return f"""
    <div class="card">
      <h2>기술 SEO 상세</h2>
      <div class="sub-inline">지금 크롤링한 HTML을 직접 파싱한 결과 — 외부 API 없이 항상 확인 가능합니다.</div>
      <div class="share-row"><span>감지된 프레임워크</span><span>{html.escape(tech['framework'])}</span></div>
      <div class="share-row"><span>시맨틱 태그</span><span>{html.escape(semantic_str)}</span></div>
      <div class="share-row"><span>div 태그 수</span><span>{tech['div_count']}개 (시맨틱 대비 {tech['div_ratio']}배)</span></div>
      <div class="share-row"><span>제목 태그 구조</span><span>{html.escape(heading_str)}</span></div>
      <div class="share-row"><span>제목 위계 건너뜀</span><span>{tech['hierarchy_skips']}곳</span></div>
      <div class="share-row"><span>이미지</span><span>{html.escape(img_str)}</span></div>
      {_tag_row(tech['has_canonical'], 'canonical 태그')}
      {_tag_row(tech['has_lang'], 'html lang 속성')}
      {_tag_row(tech['has_viewport'], 'viewport 메타')}
      {_tag_row(tech['og_count'] > 0, f"Open Graph 태그 ({tech['og_count']}개)")}
      {_tag_row(tech['twitter_count'] > 0, f"Twitter 카드 태그 ({tech['twitter_count']}개)")}
      <div class="cite-list-title">보안·위생</div>
      {sec_html}
    </div>"""


SITECRAWL_MAX_PAGES = 15


def _render_sitecrawl_card(crawl):
    """crawl_site() 결과 — 한 페이지가 아니라 사이트 전체(최대 SITECRAWL_MAX_PAGES개)를
    돌아본 결과라, 단일 페이지 감사에서는 안 보이던 깨진 링크·중복 제목 같은 게 나온다.
    Gemini/PSI 쿼터와 무관하게 우리 서버가 직접 크롤링해서 얻는 실데이터다."""
    pages = crawl["pages"]
    total = len(pages)
    if total == 0:
        return """
        <div class="card" id="ph-sitecrawl">
          <h2>사이트 전체 진단</h2>
          <div class="issue-empty">크롤링된 페이지가 없습니다.</div>
        </div>"""

    broken = [p for p in pages if p["status_code"] == 0 or p["status_code"] >= 400]
    avg_load = round(sum(p["load_ms"] for p in pages) / total)
    thin = [p for p in pages if p["status_code"] < 400 and p["status_code"] != 0 and p["word_count"] < 300]
    alt_missing_total = sum(p.get("img_missing_alt", 0) for p in pages)
    title_counts = Counter(p["title"] for p in pages if p.get("title"))
    dup_titles = [t for t, c in title_counts.items() if c > 1]

    rows = f"""
    <div class="share-row"><span>크롤된 페이지</span><span>{total}개 (최대 {SITECRAWL_MAX_PAGES}개까지 확인)</span></div>
    <div class="share-row"><span>평균 로드 시간</span><span>{avg_load}ms</span></div>
    <div class="share-row"><span>오류/응답 실패 페이지</span><span>{len(broken)}개</span></div>
    <div class="share-row"><span>콘텐츠 빈약 페이지 (300단어 미만)</span><span>{len(thin)}개</span></div>
    <div class="share-row"><span>alt 없는 이미지 (전 페이지 합계)</span><span>{alt_missing_total}개</span></div>
    <div class="share-row"><span>제목(title) 중복</span><span>{len(dup_titles)}건</span></div>"""

    detail_rows = ""
    if broken:
        detail_rows += '<div class="cite-list-title">오류 페이지</div>'
        for p in broken[:5]:
            code = p["status_code"] if p["status_code"] else "요청 실패"
            detail_rows += (f'<div class="cite-row"><span class="cite-url">{html.escape(p["url"])}</span>'
                             f'<span class="cite-count">{code}</span></div>')
    if dup_titles:
        detail_rows += '<div class="cite-list-title">중복된 제목</div>'
        for t in dup_titles[:5]:
            urls = [p["url"] for p in pages if p.get("title") == t]
            detail_rows += (f'<div class="cite-row"><span class="cite-url">{html.escape(t)}</span>'
                             f'<span class="cite-count">{len(urls)}개 페이지</span></div>')

    return f"""
    <div class="card" id="ph-sitecrawl">
      <h2>사이트 전체 진단</h2>
      <div class="sub-inline">단일 페이지가 아니라 사이트 내부 링크를 따라가며 여러 페이지를 직접 크롤링한 결과입니다.</div>
      {rows}
      {detail_rows}
    </div>"""


def _run_competitor_tech_audit(competitors):
    """등록된 경쟁사 도메인을 직접 크롤링해 우리 사이트와 같은 채점 기준(score_categories)을
    적용한다. Gemini를 전혀 쓰지 않아 쿼터와 무관하게 항상 동작하는 비교 데이터다."""
    results = []
    for comp in competitors:
        domain = (comp.get("domain") or "").strip()
        name = comp.get("name") or domain or "경쟁사"
        if not domain:
            continue
        url = domain if domain.startswith("http") else f"https://{domain}"
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
            comp_tech = audit_technical(r.url, r.text)
            results.append({"name": name, "scores": score_categories(comp_tech), "error": None})
        except Exception as e:
            results.append({"name": name, "scores": None, "error": f"{type(e).__name__}: {e}"})
    return results


def _render_techcompare_card(scores, brand_label, comp_results):
    if not comp_results:
        return '<div id="ph-techcompare"></div>'

    ok_comps = [c for c in comp_results if c["scores"] is not None]
    err_comps = [c for c in comp_results if c["scores"] is None]

    groups = ""
    for key, s in scores.items():
        rows = f"""
        <div class="bar-row">
          <div class="bar-label">{html.escape(brand_label)} (자사)</div>
          <div class="bar-track"><div class="bar-fill" style="width:{s['score']}%;background:#2a78d6"></div></div>
          <div class="bar-value">{s['score']}</div>
        </div>"""
        for c in ok_comps:
            v = c["scores"][key]["score"]
            rows += f"""
            <div class="bar-row">
              <div class="bar-label">{html.escape(c['name'])}</div>
              <div class="bar-track"><div class="bar-fill" style="width:{v}%;background:#C3C2B7"></div></div>
              <div class="bar-value">{v}</div>
            </div>"""
        groups += f'<div class="cite-list-title">{html.escape(s["label"])}</div>{rows}'

    err_html = "".join(
        f'<div class="issue-row"><span class="tag tag-err">확인 실패</span>'
        f'<div class="issue-title">{html.escape(c["name"])}</div></div>'
        for c in err_comps
    )

    return f"""
    <div class="card" id="ph-techcompare">
      <h2>기술 SEO 비교</h2>
      <div class="sub-inline">등록된 경쟁사 사이트를 직접 크롤링해 같은 기준으로 채점한 결과 — Gemini와 무관합니다.</div>
      {groups}
      {err_html}
    </div>"""


def _render_issue_rows(rx):
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
    return issue_rows


def _render_geo_status_card(geo_status):
    good_points, improve_points = [], []

    rb = geo_status["robots"]
    if rb["exists"] is None:
        improve_points.append(f"robots.txt 확인 실패: {html.escape(rb.get('error', ''))}")
    elif not rb["exists"]:
        improve_points.append("robots.txt가 없습니다 — AI 크롤러 접근 정책을 명시할 수 없는 상태입니다.")
    else:
        blocked = [b for b in rb["bots"] if not b["allowed"]]
        if not blocked:
            good_points.append("robots.txt가 있고 주요 AI 크롤러(GPTBot·ClaudeBot·PerplexityBot 등)를 모두 허용하고 있습니다.")
        else:
            names = ", ".join(b["bot"] for b in blocked[:4]) + ("…" if len(blocked) > 4 else "")
            improve_points.append(f"robots.txt에서 AI 크롤러 {len(blocked)}개가 차단되어 있습니다 ({names}).")

    lm = geo_status["llms"]
    if lm["exists"] is None:
        improve_points.append(f"llms.txt 확인 실패: {html.escape(lm.get('error', ''))}")
    elif lm["exists"]:
        good_points.append("llms.txt가 이미 있습니다.")
    else:
        improve_points.append("llms.txt가 없습니다 — AI가 사이트를 빠르게 이해하도록 돕는 파일입니다.")

    sm = geo_status["sitemap"]
    if sm["exists"] is None:
        improve_points.append(f"sitemap.xml 확인 실패: {html.escape(sm.get('error', ''))}")
    elif sm["exists"]:
        cnt = sm["url_count"]
        if sm["is_index"]:
            good_points.append(f"sitemap.xml이 있습니다 (하위 사이트맵 {cnt}개를 포함한 인덱스 파일).")
        else:
            good_points.append(f"sitemap.xml이 있고 {cnt if cnt is not None else '여러'}개의 URL을 포함합니다.")
    else:
        improve_points.append("sitemap.xml이 없습니다.")

    jl = geo_status["jsonld"]
    valid_jl = [b for b in jl if b["valid"]]
    if valid_jl:
        all_types = sorted({t for b in valid_jl for t in b["types"]})
        good_points.append(f"페이지에 JSON-LD 구조화 데이터가 이미 있습니다 (타입: {', '.join(all_types) or '미상'}).")
    else:
        improve_points.append("페이지에 JSON-LD 구조화 데이터가 없습니다.")
    if any(not b["valid"] for b in jl):
        improve_points.append("JSON-LD 블록 중 문법 오류로 파싱되지 않는 것이 있습니다.")

    good_rows = "".join(f'<div class="issue-row"><span class="tag tag-yes">양호</span><div class="issue-title">{html.escape(p)}</div></div>' for p in good_points)
    improve_rows = "".join(f'<div class="issue-row"><span class="tag tag-no">보완</span><div class="issue-title">{html.escape(p)}</div></div>' for p in improve_points)
    return f"""
    <div class="card" id="ph-geostatus">
      <h2>현재 GEO 상태 (실제 확인)</h2>
      <div class="sub-inline">robots.txt·llms.txt·sitemap.xml·JSON-LD를 지금 이 사이트에서 직접 가져와 확인한 결과입니다.</div>
      {good_rows}
      {improve_rows}
    </div>"""


def _cite_domain(u):
    return (urlparse(u).netloc or u).lower().lstrip("www.")


def _render_trend_svg(points, color="#2a78d6"):
    """points: [(date_str, value|None), ...] 오름차순. 값이 2개 미만이면 그릴 게 없어 빈 문자열.
    단일 시계열이라 범례 없음 — 끝점에 값만 직접 라벨링한다 (dataviz 마크 규칙)."""
    vals = [v for _, v in points if v is not None]
    if len(vals) < 2:
        return ""
    w, h, pad = 280, 72, 8
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    n = len(points)
    step = (w - 2 * pad) / (n - 1) if n > 1 else 0
    coords = []
    for i, (_, v) in enumerate(points):
        if v is None:
            continue
        x = pad + i * step
        y = h - pad - (v - lo) / rng * (h - 2 * pad)
        coords.append((x, y))
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    area = f"{coords[0][0]:.1f},{h - pad:.1f} " + poly + f" {coords[-1][0]:.1f},{h - pad:.1f}"
    last_x, last_y = coords[-1]
    last_val = vals[-1]
    return f"""
    <svg viewBox="0 0 {w} {h}" width="100%" height="{h}" preserveAspectRatio="none" style="display:block">
      <polygon points="{area}" fill="{color}" opacity="0.1"></polygon>
      <polyline points="{poly}" fill="none" stroke="{color}" stroke-width="2"
        stroke-linejoin="round" stroke-linecap="round"></polyline>
      <circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="4" fill="{color}"></circle>
    </svg>
    <div class="trend-val">최근값 {last_val}%</div>"""


def _render_trend_section(history):
    """history: get_history() 결과. 값이 2개 미만이면 그릴 게 없어 빈 문자열."""
    if not history:
        return ""
    exp_pts = [(h["date"], h["exposure_score"]) for h in history]
    cit_pts = [(h["date"], h["citation_share"]) for h in history]
    men_pts = [(h["date"], h["mention_share"]) for h in history]
    exp_svg = _render_trend_svg(exp_pts, "#2a78d6")
    cit_svg = _render_trend_svg(cit_pts, "#1baf7a")
    men_svg = _render_trend_svg(men_pts, "#eb6834")
    if not (exp_svg or cit_svg or men_svg):
        return ""
    return f"""
    <div class="card">
      <h2>추이 (최근 {len(history)}일)</h2>
      <div class="trend-grid">
        <div class="trend-item"><h3>노출도 점수</h3>{exp_svg or '<div class="issue-empty">데이터 부족</div>'}</div>
        <div class="trend-item"><h3>인용 점유율</h3>{cit_svg or '<div class="issue-empty">데이터 부족</div>'}</div>
        <div class="trend-item"><h3>언급 점유율</h3>{men_svg or '<div class="issue-empty">데이터 부족</div>'}</div>
      </div>
    </div>"""


def _render_geo_and_citation(gen_prompts, gen_prompts_error, tech, brand_names, brand_domains, target,
                              extra_competitors, prompts_source="generated", prompt_topics=None):
    """AI 노출(Gemini) + 인용 상세 카드를 만든다. 실패하면 가짜 점수 대신 명확한 에러만 표시.
    brand_names/brand_domains: 등록된 브랜드 별칭·사이트 URL을 전부 포함한 리스트 (guess한 이름/분석 대상
    URL이 항상 0번째). extra_competitors: 설정에 저장된 경쟁사 목록 — 직접 크롤링하지 않고
    Gemini 노출·인용 판별에만 쓴다. prompt_topics: {프롬프트 텍스트: 주제} — 저장된 프롬프트를
    쓴 경우에만 채워지며, 프롬프트별 원본 이력 적재 시 주제를 같이 남기는 데 쓴다.
    반환값: (geo_section_html, citation_detail_html)"""
    brand_label = brand_names[0]
    try:
        if gen_prompts_error:
            raise gen_prompts_error
        competitors_for_gemini = [
            {"name": sc.get("name") or sc.get("domain") or "", "domain": sc.get("domain") or "",
             "aliases": sc.get("aliases") or []}
            for sc in extra_competitors
        ]
        geo = run_geo_visibility(
            gen_prompts, config.GEMINI_API_KEY, config.GEMINI_MODEL,
            brand_name=brand_names, brand_domain=brand_domains,
            competitors=competitors_for_gemini,
        )
        live = [rec for rec in geo["records"] if rec["status"] == "LIVE"]
        errored = [rec for rec in geo["records"] if rec["status"] != "LIVE"]
        total = len(live)
        mentioned_count = sum(1 for rec in live if rec["mentioned"])
        cited_count = sum(1 for rec in live if rec["cited"])
        exposure_score = round(mentioned_count / total * 100) if total else None
        citation_share = round(cited_count / total * 100) if total else None

        # 전부 429(쿼터 초과)로 실패한 경우, 개별 에러 대신 원인을 명확히 알려준다.
        # 응답 본문에 quotaId가 찍혀있으면 분당/일일 한도 중 뭔지 실제로 구분할 수 있다.
        quota_banner = ""
        if not live and errored and all("429" in rec["detail"] for rec in errored):
            combined_detail = " ".join(rec["detail"] for rec in errored)
            if "PerDay" in combined_detail:
                quota_msg = "Gemini 무료 쿼터의 <b>일일 한도</b>를 초과했습니다 — 내일(태평양시간 자정 기준) 복구됩니다."
            elif "PerMinute" in combined_detail:
                quota_msg = "Gemini 무료 쿼터의 <b>분당 한도</b>를 초과했습니다 — 1분 정도 후 다시 시도하면 됩니다."
            else:
                quota_msg = "Gemini 무료 쿼터를 초과했습니다 — 분당 한도면 1분 후, 일일 한도면 하루 지나야 복구됩니다."
            quota_banner = f"""
            <div class="issue-empty" style="margin-bottom:14px">
              {quota_msg}
            </div>"""

        # 경쟁사별 노출도/인용 — 자사와 같은 질문 세트를 같은 응답에서 함께 판별한 것.
        tracked = [(brand_label, mentioned_count, cited_count)]
        for comp in competitors_for_gemini:
            name = comp["name"]
            cm = sum(1 for rec in live if rec["competitor_mentions"].get(name))
            cc = sum(1 for rec in live if rec["competitor_citations"].get(name))
            tracked.append((name, cm, cc))

        # 노출도 순위 — 등록한 사이트들(자사+경쟁사) 안에서의 순위. "시장 전체 1위"가 아니라
        # "내가 등록한 비교 대상 중 순위"라는 걸 라벨에서 분명히 한다.
        ranked = sorted(tracked, key=lambda t: -t[1])
        self_rank = next((i for i, t in enumerate(ranked, 1) if t[0] == brand_label), None) if total else None

        # 언급 점유율 — 개별 노출도(%)가 아니라 "전체 언급 중 내 비중" (share of voice).
        total_mentions_all = sum(t[1] for t in tracked)
        mention_share = round(mentioned_count / total_mentions_all * 100) if total_mentions_all else None

        # 인용→브랜드 귀속률 — 우리 도메인이 인용된 답변 중, 브랜드명도 같이 언급된 비율.
        cited_records = [rec for rec in live if rec["cited"]]
        attributed_count = sum(1 for rec in cited_records if rec["mentioned"])
        attribution_rate = (
            round(attributed_count / len(cited_records) * 100) if cited_records else None
        )

        def _fmt_pct(v):
            return f"{v}<span>/100</span>" if v is not None else "—"

        overview_cards = f"""
        <div class="score-card">
          <div class="score-label">노출도 점수</div>
          <div class="score-num">{_fmt_pct(exposure_score)}</div>
          <div class="score-detail">완료된 응답 중 브랜드 언급 비율</div>
        </div>
        <div class="score-card">
          <div class="score-label">노출도 순위</div>
          <div class="score-num">{f"#{self_rank}" if self_rank else "—"}</div>
          <div class="score-detail">등록한 {len(tracked)}개 사이트 중 순위</div>
        </div>
        <div class="score-card">
          <div class="score-label">인용 점유율</div>
          <div class="score-num">{_fmt_pct(citation_share)}</div>
          <div class="score-detail">우리 도메인이 인용된 답변 비율</div>
        </div>
        <div class="score-card">
          <div class="score-label">언급 점유율</div>
          <div class="score-num">{_fmt_pct(mention_share)}</div>
          <div class="score-detail">전체 언급 중 자사 비중 (경쟁사 대비)</div>
        </div>
        <div class="score-card">
          <div class="score-label">인용→브랜드 귀속률</div>
          <div class="score-num">{_fmt_pct(attribution_rate)}</div>
          <div class="score-detail">인용 답변 {attributed_count}/{len(cited_records)}개에서 브랜드 언급</div>
        </div>
        <div class="score-card">
          <div class="score-label">브랜드 언급 프롬프트</div>
          <div class="score-num">{mentioned_count}<span>/{total if total else "—"}</span></div>
          <div class="score-detail">답변에서 브랜드가 1회 이상 언급된 질문 수</div>
        </div>"""

        # 노출도 비교 막대그래프 — 자사는 강조색, 경쟁사는 회색(emphasis 형태: 비교 대상을
        # 각각 구분하는 게 목적이 아니라 "자사 vs 나머지"가 이야기의 핵심이라서).
        exposure_bar_html = ""
        if competitors_for_gemini and total:
            max_cnt = max(t[1] for t in tracked) or 1
            for name, cnt, _ in ranked:
                pct_of_max = round(cnt / max_cnt * 100)
                is_self = name == brand_label
                bar_color = "#2a78d6" if is_self else "#C3C2B7"
                exposure_bar_html += f"""
                <div class="bar-row">
                  <div class="bar-label">{html.escape(name)}{' (자사)' if is_self else ''}</div>
                  <div class="bar-track"><div class="bar-fill" style="width:{pct_of_max}%;background:{bar_color}"></div></div>
                  <div class="bar-value">{round(cnt / total * 100)}%</div>
                </div>"""
            exposure_bar_html = f"""
            <div class="card">
              <h2>노출도 비교</h2>
              <div class="sub-inline">같은 질문 세트 기준, 프롬프트 중 언급된 비율</div>
              {exposure_bar_html}
            </div>"""

        # 언급 점유율 스택 바 — 전체 언급 중 각 사이트가 차지하는 비중 (part-to-whole).
        mention_share_html = ""
        if competitors_for_gemini and total_mentions_all:
            palette = ["#2a78d6", "#eb6834", "#1baf7a"]
            segs, legend = "", ""
            for i, (name, cnt, _) in enumerate(tracked):
                if not cnt:
                    continue
                color = palette[i % len(palette)]
                pct = round(cnt / total_mentions_all * 100)
                is_self = name == brand_label
                segs += f'<div style="flex:{cnt} 0 0;background:{color}"></div>'
                legend += f'<div class="legend-item"><span class="legend-swatch" style="background:{color}"></span>{html.escape(name)}{" (자사)" if is_self else ""} {pct}%</div>'
            mention_share_html = f"""
            <div class="card">
              <h2>언급 점유율</h2>
              <div class="sub-inline">경쟁사 대비 전체 언급에서 차지하는 비중</div>
              <div class="stack-bar">{segs}</div>
              <div class="legend-row">{legend}</div>
            </div>"""

        # 인용 상세 — 실제로 인용된 URL을 도메인 기준 자사/경쟁사/제3자로 분류, 페이지별 순위화.
        # 자사 판정은 지금 분석 중인 URL 하나가 아니라 등록된 모든 사이트 URL 기준.
        own_domains = {_cite_domain(d) for d in brand_domains}
        competitor_domains = {_cite_domain(c["domain"]) for c in competitors_for_gemini}
        all_cited = [u for rec in live for u in rec["cited_urls"]]
        page_counts = Counter(all_cited)

        def _classify_source(u):
            d = _cite_domain(u)
            if d in own_domains:
                return "자사"
            if d in competitor_domains:
                return "경쟁사"
            return "제3자"

        source_counts = Counter(_classify_source(u) for u in all_cited)
        total_cites = sum(source_counts.values())

        citation_detail_section = ""
        if all_cited:
            cats = [("자사", "#2a78d6"), ("경쟁사", "#eb6834"), ("제3자", "#1baf7a")]
            segs, legend = "", ""
            for label, color in cats:
                cnt = source_counts.get(label, 0)
                if not cnt:
                    continue
                pct = round(cnt / total_cites * 100)
                segs += f'<div style="flex:{cnt} 0 0;background:{color}"></div>'
                legend += f'<div class="legend-item"><span class="legend-swatch" style="background:{color}"></span>{label} {cnt}건 ({pct}%)</div>'

            page_rows = ""
            for u, cnt in page_counts.most_common(5):
                src = _classify_source(u)
                badge_cls = "tag-yes" if src == "자사" else "tag-no"
                page_rows += f"""
                <div class="cite-row">
                  <a href="{html.escape(u)}" target="_blank" rel="noopener" class="cite-url">{html.escape(u)}</a>
                  <span class="tag {badge_cls}">{src}</span>
                  <span class="cite-count">{cnt}회</span>
                </div>"""

            citation_detail_section = f"""
            <div class="card" id="ph-citation">
              <h2>인용 상세</h2>
              <div class="sub-inline">Gemini 응답에서 실제로 인용된 출처 {total_cites}건 기준</div>
              <div class="stack-bar">{segs}</div>
              <div class="legend-row">{legend}</div>
              <div class="cite-list-title">가장 많이 인용된 페이지</div>
              {page_rows}
            </div>"""

        # 추이 — Supabase에 오늘 값을 저장하고(설정 안 했으면 조용히 무시), 과거 이력을
        # 가져와 라인 차트로. 이력이 2개 미만이면 그릴 게 없어 카드 자체가 안 뜬다.
        # 오늘 쿼터 초과로 저장할 실측치가 없어도, 과거에 쌓인 이력은 그대로 보여준다.
        target_domain_key = _cite_domain(target)
        if total:
            save_snapshot(config.SUPABASE_URL, config.SUPABASE_KEY, target_domain_key,
                          exposure_score, citation_share, mention_share)
        history = get_history(config.SUPABASE_URL, config.SUPABASE_KEY, target_domain_key)
        # 요약 숫자 3개와 별개로, 이번 실행의 프롬프트별 원본 결과를 전부 적재한다 —
        # 나중에 프롬프트별 이력·"경쟁사는 인용됐는데 우리는 안 된 페이지" 같은 걸 만들려면
        # 이 원본이 필요하다. 성공/실패 여부와 무관하게 시도한 프롬프트 전부 기록한다.
        save_prompt_runs(config.SUPABASE_URL, config.SUPABASE_KEY, target_domain_key,
                          geo["records"], prompt_topics)

        if quota_banner:
            # 전부 429면 "—" 투성이 점수·비교·프롬프트 목록을 늘어놔봐야 정보가 없다.
            # 배너 하나로 끝내되, 과거 추이 데이터는 있으면 이어서 보여준다.
            geo_section = f"""
            <div class="card" id="ph-geo">
              <h2>AI 노출 (Gemini)</h2>
              {quota_banner}
            </div>""" + _render_trend_section(history)
            return geo_section, citation_detail_section
        trend_section = _render_trend_section(history)

        prompts_desc = "저장된 프롬프트" if prompts_source == "saved" else "자동 생성된 질문"
        geo_rows = ""
        for rec in geo["records"]:
            if rec["status"] != "LIVE":
                status_html = f'<span class="tag tag-err">실패: {html.escape(rec["detail"])}</span>'
                geo_rows += f"""
                <div class="prompt-row">
                  <div class="prompt-text">{html.escape(rec['prompt'])}</div>
                  <div class="prompt-status">{status_html}</div>
                </div>"""
                continue
            m = '<span class="tag tag-yes">언급됨</span>' if rec["mentioned"] else '<span class="tag tag-no">언급 없음</span>'
            c = '<span class="tag tag-yes">인용됨</span>' if rec["cited"] else '<span class="tag tag-no">인용 없음</span>'
            sites_html = f'<div class="prompt-site"><span class="site-label">자사</span>{m}{c}</div>'
            for comp in competitors_for_gemini:
                name = comp["name"]
                cm = '<span class="tag tag-yes">언급됨</span>' if rec["competitor_mentions"].get(name) else '<span class="tag tag-no">언급 없음</span>'
                cc2 = '<span class="tag tag-yes">인용됨</span>' if rec["competitor_citations"].get(name) else '<span class="tag tag-no">인용 없음</span>'
                sites_html += f'<div class="prompt-site"><span class="site-label">{html.escape(name)}</span>{cm}{cc2}</div>'
            geo_rows += f"""
            <div class="prompt-row">
              <div class="prompt-text">{html.escape(rec['prompt'])}</div>
              <div class="prompt-sites">{sites_html}</div>
            </div>"""

        geo_section = f"""
        <div class="card" id="ph-geo">
          <h2>AI 노출 (Gemini)</h2>
          <div class="sub-inline">{prompts_desc} {len(geo['records'])}개 중 {total}개 성공 · Google Search grounding 기반 실데이터</div>
          <div class="scores" style="margin:14px 0 18px">
            {overview_cards}
          </div>
          {geo_rows}
        </div>
        {trend_section}
        {exposure_bar_html}
        {mention_share_html}"""
        return geo_section, citation_detail_section
    except Exception as e:
        if "429" in str(e):
            msg = "Gemini 무료 쿼터를 초과했습니다 — 분당 한도면 1분 후, 일일 한도면 하루 지나야 복구됩니다."
        else:
            msg = f"확인 실패: {html.escape(str(e))}"
        geo_section = f"""
        <div class="card" id="ph-geo">
          <h2>AI 노출 (Gemini)</h2>
          <div class="issue-empty">{msg}</div>
        </div>"""
        return geo_section, ""


def _b64(s):
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


def _stream_analyze(target, page_html, tech, scores, rx, brand_names, brand_domains, artifacts,
                     saved_competitors, saved_prompts):
    """
    독립적인 외부 호출(PSI·현재 GEO 상태·Gemini)이 끝나는 대로 해당 카드를 채워 넣고
    진행률을 갱신하는 스트리밍 응답. 브라우저가 청크를 받는 대로 그 안의 <script>를
    실행하기 때문에, 클라이언트 쪽엔 폴링/웹소켓 없이 그냥 평범한 HTML 응답이다.
    (호스팅의 리버스 프록시가 응답을 전부 버퍼링하면 실시간 효과는 없어지지만, 최종
    결과는 동일하게 나온다.)

    saved_prompts가 있으면 Gemini에 새로 질문을 생성시키지 않고 그대로 쓴다 — 매번 다른
    질문이면 추이 비교가 의미 없어지기 때문. saved_competitors는 크롤링 없이 Gemini
    노출·인용 판별 비교 대상에만 포함시킨다.
    """
    gemini_enabled = bool(config.GEMINI_API_KEY)
    using_saved_prompts = gemini_enabled and bool(saved_prompts)
    has_competitors = bool(saved_competitors)
    steps_total = 3  # geostatus, psi, sitecrawl
    if has_competitors:
        steps_total += 1  # techcompare — Gemini와 무관하게 항상 돌기 때문에 gemini_enabled와 별개
    if gemini_enabled:
        steps_total += 1 if using_saved_prompts else 2

    seo_cards = _render_seo_score_cards(scores)
    issue_rows = _render_issue_rows(rx)
    tech_detail_card = _render_tech_detail_card(tech)
    techcompare_placeholder = ("""
        <div class="card" id="ph-techcompare"><h2>기술 SEO 비교</h2><div class="issue-empty">확인 중…</div></div>"""
        if has_competitors else "")

    if gemini_enabled:
        gemini_placeholder = '<div class="card" id="ph-geo"><h2>AI 노출 (Gemini)</h2><div class="issue-empty">확인 중…</div></div>'
    else:
        gemini_placeholder = """
        <div class="card" id="ph-geo">
          <h2>AI 노출 (Gemini)</h2>
          <div class="issue-empty">GEMINI_API_KEY가 설정되지 않아 확인하지 못했습니다. aistudio.google.com/apikey 에서 무료로 발급할 수 있습니다.</div>
        </div>"""

    shell = f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{ANALYZE_CSS}</style></head><body>
<div class="app">
  <div class="topbar">
    <div class="url-label">분석 대상: {html.escape(target)}</div>
    <a class="reanalyze" href="/_content/analyze">다시 분석하기</a>
  </div>
  <div class="progress-wrap">
    <div class="progress-track"><div class="progress-fill" id="pf" style="width:0%"></div></div>
    <div class="progress-label"><span id="pp">0%</span> · 분석 진행 중</div>
  </div>
  <div class="scores">{seo_cards}<div class="score-card" id="ph-psi"><div class="score-label">웹 성능</div><div class="score-num" style="font-size:16px;color:var(--dim2)">측정 중…</div></div></div>
  <div id="ph-psi-detail"></div>
  <div class="card"><h2>발견된 이슈</h2>{issue_rows}</div>
  {tech_detail_card}
  <div class="card" id="ph-sitecrawl"><h2>사이트 전체 진단</h2><div class="issue-empty">크롤링 중…</div></div>
  {techcompare_placeholder}
  <div class="card" id="ph-geostatus"><h2>현재 GEO 상태 (실제 확인)</h2><div class="issue-empty">확인 중…</div></div>
  {gemini_placeholder}
  <div id="ph-citation"></div>
  <div class="card">
    <div class="card-h"><h2>권장 robots.txt</h2><button class="copy" onclick="cp('r')">복사</button></div>
    <pre id="r">{_esc_html(artifacts['robots_txt'])}</pre>
  </div>
  <div class="card">
    <div class="card-h"><h2>권장 llms.txt</h2><button class="copy" onclick="cp('l')">복사</button></div>
    <pre id="l">{_esc_html(artifacts['llms_txt'])}</pre>
  </div>
  <div class="card">
    <div class="card-h"><h2>권장 JSON-LD</h2><button class="copy" onclick="cp('j')">복사</button></div>
    <pre id="j">{_esc_html(artifacts['json_ld'])}</pre>
  </div>
</div>
{ANALYZE_HELPER_JS}
"""
    yield shell

    if steps_total == 0:
        yield "</body></html>"
        return

    done = 0

    def progress_script():
        return f"<script>setProgress({done},{steps_total});</script>\n"

    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {}
        futures[ex.submit(check_current_geo_status, target, page_html)] = "geostatus"
        futures[ex.submit(collect_pagespeed, target, api_key=config.PAGESPEED_API_KEY or None)] = "psi"
        futures[ex.submit(crawl_site, target, max_pages=SITECRAWL_MAX_PAGES, delay=0.2)] = "sitecrawl"
        if has_competitors:
            futures[ex.submit(_run_competitor_tech_audit, saved_competitors)] = "techcompare"
        if gemini_enabled and not using_saved_prompts:
            futures[ex.submit(generate_prompts, tech, config.GEMINI_API_KEY, config.GEMINI_MODEL, count=3)] = "prompts"

        if using_saved_prompts:
            gen_state = {"value": [p["prompt"] for p in saved_prompts], "error": None}
        else:
            gen_state = {"value": None, "error": None}
        gemini_emitted = False

        def build_gemini_chunk():
            nonlocal done
            prompt_topics = ({p["prompt"]: p.get("topic") for p in saved_prompts}
                              if using_saved_prompts else {})
            geo_html, cite_html = _render_geo_and_citation(
                gen_state["value"], gen_state["error"], tech, brand_names, brand_domains, target,
                saved_competitors, prompts_source="saved" if using_saved_prompts else "generated",
                prompt_topics=prompt_topics,
            )
            done += 1
            script = f"fillEl('ph-geo','{_b64(geo_html)}');"
            if cite_html:
                script += f"fillEl('ph-citation','{_b64(cite_html)}');"
            return f"<script>{script}</script>\n"

        for fut in as_completed(futures):
            kind = futures[fut]

            if kind == "geostatus":
                geo_status = fut.result()
                frag = _render_geo_status_card(geo_status)
                done += 1
                yield f"<script>fillEl('ph-geostatus','{_b64(frag)}');</script>\n"
                yield progress_script()

            elif kind == "psi":
                psi = fut.result()
                frag = _render_psi_card(psi)
                detail_frag = _render_psi_detail_card(psi)
                done += 1
                yield f"<script>fillEl('ph-psi','{_b64(frag)}');fillEl('ph-psi-detail','{_b64(detail_frag)}');</script>\n"
                yield progress_script()

            elif kind == "sitecrawl":
                crawl = fut.result()
                frag = _render_sitecrawl_card(crawl)
                done += 1
                yield f"<script>fillEl('ph-sitecrawl','{_b64(frag)}');</script>\n"
                yield progress_script()

            elif kind == "techcompare":
                comp_results = fut.result()
                frag = _render_techcompare_card(scores, brand_names[0], comp_results)
                done += 1
                yield f"<script>fillEl('ph-techcompare','{_b64(frag)}');</script>\n"
                yield progress_script()

            elif kind == "prompts":
                try:
                    gen_state["value"] = fut.result()
                except Exception as e:
                    gen_state["error"] = e
                done += 1
                gemini_emitted = True
                yield build_gemini_chunk()
                yield progress_script()

        # using_saved_prompts일 땐 Gemini를 기다리게 할 future가 따로 없으므로,
        # geostatus/psi(와 필요시 prompts)가 다 끝난 뒤 여기서 발행한다.
        if gemini_enabled and not gemini_emitted:
            yield build_gemini_chunk()
            yield progress_script()

    yield "</body></html>"


def _esc_html(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _render_no_site_page():
    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{SETTINGS_CSS}</style></head><body>
<div class="app">
  <div class="card">
    <h2>등록된 사이트가 없습니다</h2>
    <div class="desc">분석을 실행하려면 먼저 내 사이트를 등록해야 합니다.</div>
    <a href="/settings/site" class="primary">내 사이트 설정으로 이동</a>
  </div>
</div>
</body></html>"""


def _render_analyze_ready_page(target, site_cfg, saved_competitors, saved_prompts):
    extra_sites = len(site_cfg["site_urls"]) - 1
    site_note = f" 외 {extra_sites}개 등록됨(자사 인용 판별에는 전부 반영)" if extra_sites > 0 else ""
    prompts_note = (f"저장된 프롬프트 {len(saved_prompts)}개 사용"
                     if saved_prompts else "저장된 프롬프트 없음 — 실행할 때마다 자동 생성됩니다")
    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{SETTINGS_CSS}</style></head><body>
<div class="app">
  <div class="card">
    <h2>분석 실행</h2>
    <div class="desc">등록된 사이트·경쟁사·프롬프트 설정을 기준으로 기술 진단과 AI 노출을 확인합니다.</div>
    <div class="list-row" style="border-top:none">
      <div class="list-main">
        <div class="list-title">대상 사이트: {html.escape(target)}{site_note}</div>
        <div class="list-sub">등록된 경쟁사 {len(saved_competitors)}개 · {prompts_note}</div>
      </div>
    </div>
    <a href="/_content/analyze?run=1" class="primary">지금 분석 실행</a>
  </div>
</div>
</body></html>"""


def _render_analyze_error_page(target, detail):
    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{SETTINGS_CSS}</style></head><body>
<div class="app">
  <div class="card">
    <h2>크롤 실패</h2>
    <div class="desc">{html.escape(target)}에서 응답을 받지 못했습니다: {html.escape(detail)}</div>
    <a href="/_content/analyze?run=1" class="primary">다시 시도</a>
    <a href="/settings/site" class="ghost" style="margin-left:8px">내 사이트 설정 확인</a>
  </div>
</div>
</body></html>"""


ANALYZE_CSS = """
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css');
:root{--bg:#FAFAF9;--line:#E4E4E1;--ink:#14161A;--dim:#5B5F66;--dim2:#9A9DA3;--accent:#1E5E46}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:'Pretendard',sans-serif;
  font-size:14px;line-height:1.6}
.app{max-width:900px;margin:0 auto;padding:32px 24px 64px}
.topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}
.url-label{font-size:13px;color:var(--dim)}
a.reanalyze{font-size:12.5px;color:var(--ink);border-bottom:1px solid var(--line)}
.progress-wrap{margin-bottom:24px}
.progress-track{width:100%;height:6px;background:#E4E4E1;border-radius:3px;overflow:hidden}
.progress-fill{width:0%;height:100%;background:#14161A;border-radius:3px;transition:width .3s ease}
.progress-label{font-size:11.5px;color:#9A9DA3;text-align:center;margin-top:8px}
.progress-label span{color:var(--ink);font-weight:500}
.scores{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:24px}
.score-card{border:1px solid var(--line);border-radius:2px;padding:18px}
.score-label{font-size:12.5px;color:var(--dim)}
.score-num{font-size:28px;font-weight:500;margin:8px 0 4px}
.score-num span{font-size:14px;color:var(--dim2);font-weight:400}
.score-tier{font-size:12px;color:var(--dim2)}
.score-detail{font-size:11px;color:var(--dim2);margin-top:6px}
.card{border:1px solid var(--line);border-radius:2px;padding:24px;margin-top:16px}
.card h2{font-size:15px;font-weight:500;margin:0 0 14px}
.issue-row{display:flex;gap:12px;padding:12px 0;border-top:1px solid #ECECE9}
.issue-row:first-child{border-top:none}
.issue-num{font-size:12px;color:var(--dim2);flex:0 0 auto}
.issue-title{font-size:13.5px;font-weight:500}
.issue-why{font-size:12.5px;color:var(--dim);margin-top:3px}
.issue-empty{color:var(--dim2);font-size:13px}
.sub-inline{font-size:12px;color:var(--dim2);margin-bottom:4px}
.prompt-row{display:flex;justify-content:space-between;align-items:center;gap:12px;
  padding:12px 0;border-top:1px solid #ECECE9}
.prompt-row:first-child{border-top:none}
.prompt-text{font-size:13.5px;flex:1}
.prompt-status{display:flex;gap:6px;flex:0 0 auto}
.tag{font-size:11px;padding:3px 8px;border-radius:10px;white-space:nowrap}
.tag-yes{background:#E6F4EC;color:#1E5E46}
.tag-no{background:#F0F0EE;color:var(--dim)}
.tag-err{background:#FBE9E7;color:#c5221f}
.share-row{display:flex;justify-content:space-between;padding:8px 0;border-top:1px solid #ECECE9;font-size:13px}
.share-row:first-child{border-top:none}
.prompt-sites{display:flex;flex-direction:column;gap:5px;align-items:flex-end;flex:0 0 auto}
.prompt-site{display:flex;align-items:center;gap:6px;font-size:11px}
.site-label{color:var(--dim2);min-width:60px;text-align:right}
.stack-bar{display:flex;height:26px;border-radius:4px;overflow:hidden;gap:2px;background:#ECECE9;margin-top:6px}
.legend-row{display:flex;flex-wrap:wrap;gap:14px;margin-top:10px}
.legend-item{display:flex;align-items:center;gap:6px;font-size:12.5px;color:var(--dim)}
.legend-swatch{width:10px;height:10px;border-radius:2px;flex:0 0 auto}
.cite-list-title{font-size:12.5px;font-weight:500;color:var(--dim);margin:18px 0 6px}
.cite-row{display:flex;align-items:center;gap:10px;padding:9px 0;border-top:1px solid #ECECE9}
.cite-row:first-child{border-top:none}
.cite-url{flex:1;font-size:12.5px;color:var(--ink);text-decoration:none;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.cite-url:hover{text-decoration:underline}
.cite-count{font-size:12px;color:var(--dim2);flex:0 0 auto}
.bar-row{display:flex;align-items:center;gap:10px;padding:8px 0}
.bar-label{width:120px;flex:0 0 auto;font-size:12.5px;color:var(--dim);overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.bar-track{flex:1;height:16px;background:#ECECE9;border-radius:3px;overflow:hidden}
.bar-fill{height:100%;border-radius:3px;min-width:2px}
.bar-value{width:40px;flex:0 0 auto;font-size:12px;color:var(--dim2);text-align:right}
.trend-val{text-align:right;font-size:11px;color:var(--dim2);margin-top:2px}
.trend-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:8px}
.trend-item h3{font-size:12.5px;font-weight:500;color:var(--dim);margin:0 0 8px}
.card-h{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
button.copy{border:1px solid var(--ink);background:transparent;color:var(--ink);
  padding:5px 12px;font-size:12px;border-radius:2px;cursor:pointer}
pre{background:#F3F3F1;border:1px solid var(--line);border-radius:2px;padding:14px;
  overflow:auto;font-size:12px;max-height:280px;font-family:ui-monospace,monospace;
  white-space:pre-wrap}
"""

ANALYZE_HELPER_JS = """
<script>
function b64ToStr(b64){
  var bin = atob(b64);
  var bytes = new Uint8Array(bin.length);
  for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new TextDecoder('utf-8').decode(bytes);
}
function fillEl(id, b64){
  var el = document.getElementById(id);
  if (el) el.outerHTML = b64ToStr(b64);
}
function setProgress(done, total){
  var pct = Math.round(done / total * 100);
  var pf = document.getElementById('pf'), pp = document.getElementById('pp');
  if (pf) pf.style.width = pct + '%';
  if (pp) pp.textContent = pct + '%';
}
function cp(id){
  navigator.clipboard.writeText(document.getElementById(id).innerText);
  event.target.innerText = "복사됨";
  setTimeout(function(){ event.target.innerText = "복사"; }, 1200);
}
</script>
"""


# ---------------- 설정: 내 사이트 / 경쟁사 / 프롬프트 목록 ----------------
# 매번 URL 분석할 때마다 경쟁사를 타이핑하고 Gemini 질문을 새로 생성하면, 오늘과
# 내일의 측정 기준이 달라져 추이 비교가 무의미해진다. 여기서 저장해두면
# analyze_content()가 항상 이 값을 가져다 쓴다.

def _split_lines(s):
    """줄바꿈·쉼표 어느 쪽으로 구분해도 되는 textarea/input 값을 리스트로. 중복 제거."""
    parts = re.split(r"[\n,]+", s or "")
    seen = []
    for p in parts:
        p = p.strip()
        if p and p not in seen:
            seen.append(p)
    return seen


def _settings_unconfigured_page(feature_name):
    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{SETTINGS_CSS}</style></head><body>
<div class="app">
  <div class="card">
    <h2>{html.escape(feature_name)}</h2>
    <div class="issue-empty">SUPABASE_URL/SUPABASE_KEY가 설정되지 않아 이 기능을 쓸 수 없습니다. Render 환경변수에 추가해주세요.</div>
  </div>
</div>
</body></html>"""


def _render_site_settings_page(cfg, saved=False, error=False):
    if error:
        banner = '<div class="banner-err">저장하지 못했습니다 — Supabase 테이블(geo_site_config)이 만들어졌는지, 환경변수가 올바른지 확인해주세요.</div>'
    elif saved:
        banner = '<div class="banner-ok">저장했습니다.</div>'
    else:
        banner = ""
    site_urls_val = "\n".join(cfg["site_urls"])
    brand_aliases_val = "\n".join(cfg["brand_aliases"])
    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{SETTINGS_CSS}</style></head><body>
<div class="app">
  {banner}
  <div class="card">
    <h2>내 사이트</h2>
    <div class="desc">등록한 URL은 자사 인용으로, 등록한 이름은 자사 노출로 판별합니다. URL 분석 시 항상 반영됩니다.</div>
    <form method="post" action="/_content/settings/site">
      <div class="field">
        <label>내 사이트 URL (줄바꿈 또는 쉼표로 구분)</label>
        <textarea name="site_urls" placeholder="https://example.com">{html.escape(site_urls_val)}</textarea>
      </div>
      <div class="field">
        <label>자사 노출 인식 이름 (줄바꿈 또는 쉼표로 구분)</label>
        <textarea name="brand_aliases" placeholder="브랜드명, 회사명, 영문 표기 등">{html.escape(brand_aliases_val)}</textarea>
        <div class="hint">AI 답변 본문에 위 이름 중 하나가 표시되면 자사 노출로 집계합니다.</div>
      </div>
      <button type="submit" class="primary">저장</button>
    </form>
  </div>
</div>
</body></html>"""


def _render_competitors_settings_page(competitors, added=False, deleted=False, error=False):
    banner = ""
    if error:
        banner = '<div class="banner-err">처리하지 못했습니다 — Supabase 테이블(geo_competitors)이 만들어졌는지 확인해주세요.</div>'
    elif added:
        banner = '<div class="banner-ok">경쟁사를 추가했습니다.</div>'
    elif deleted:
        banner = '<div class="banner-ok">경쟁사를 삭제했습니다.</div>'
    rows = ""
    for c in competitors:
        aliases = ", ".join(c.get("aliases") or [])
        sub = html.escape(c.get("domain") or "")
        if aliases:
            sub += " · " + html.escape(aliases)
        rows += f"""
        <div class="list-row">
          <div class="list-main">
            <div class="list-title">{html.escape(c.get('name') or c.get('domain') or '')}</div>
            <div class="list-sub">{sub}</div>
          </div>
          <div class="list-actions">
            <form method="post" action="/_content/settings/competitors/{c['id']}/delete" onsubmit="return confirm('삭제하시겠습니까?')">
              <button type="submit" class="danger">삭제</button>
            </form>
          </div>
        </div>"""
    if not rows:
        rows = '<div class="issue-empty">등록된 경쟁사가 없습니다.</div>'
    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{SETTINGS_CSS}</style></head><body>
<div class="app">
  {banner}
  <div class="card">
    <h2>경쟁사 관리</h2>
    <div class="desc">추적할 경쟁사를 관리합니다. URL 분석 시 AI 노출·인용 비교에 자동으로 포함됩니다.</div>
    {rows}
  </div>
  <div class="card">
    <h2>경쟁사 추가</h2>
    <form method="post" action="/_content/settings/competitors/add" class="add-row">
      <div class="field"><label>이름</label><input name="name" placeholder="경쟁사명" required></div>
      <div class="field"><label>도메인</label><input name="domain" placeholder="competitor.com" required></div>
      <div class="field"><label>별칭 (쉼표로 구분, 선택)</label><input name="aliases" placeholder="약칭, 영문명"></div>
      <button type="submit" class="primary">추가</button>
    </form>
  </div>
</div>
</body></html>"""


def _render_prompts_settings_page(prompts, added=False, deleted=False, error=False):
    banner = ""
    if error:
        banner = '<div class="banner-err">처리하지 못했습니다 — Supabase 테이블(geo_prompts)이 만들어졌는지 확인해주세요.</div>'
    elif added:
        banner = '<div class="banner-ok">프롬프트를 추가했습니다.</div>'
    elif deleted:
        banner = '<div class="banner-ok">프롬프트를 삭제했습니다.</div>'
    rows = ""
    for p in prompts:
        status = '<span class="tag tag-no">보관됨</span>' if p.get("archived") else '<span class="tag tag-yes">모니터링 중</span>'
        toggle_label = "복원" if p.get("archived") else "보관"
        topic = p.get("topic") or ""
        sub = (html.escape(topic) + " · " if topic else "") + status
        rows += f"""
        <div class="list-row">
          <div class="list-main">
            <div class="list-title">{html.escape(p.get('prompt') or '')}</div>
            <div class="list-sub">{sub}</div>
          </div>
          <div class="list-actions">
            <form method="post" action="/_content/settings/prompts/{p['id']}/toggle">
              <button type="submit" class="ghost">{toggle_label}</button>
            </form>
            <form method="post" action="/_content/settings/prompts/{p['id']}/delete" onsubmit="return confirm('삭제하시겠습니까?')">
              <button type="submit" class="danger">삭제</button>
            </form>
          </div>
        </div>"""
    if not rows:
        rows = '<div class="issue-empty">등록된 프롬프트가 없습니다. 없으면 분석할 때마다 자동 생성됩니다.</div>'
    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{SETTINGS_CSS}</style></head><body>
<div class="app">
  {banner}
  <div class="card">
    <h2>프롬프트 목록</h2>
    <div class="desc">저장해두면 분석할 때마다 새로 생성하지 않고 이 질문들로 AI 노출을 추적해서 날짜별 비교가 가능해집니다. 비워두면 지금처럼 자동 생성됩니다.</div>
    {rows}
  </div>
  <div class="card">
    <h2>프롬프트 추가</h2>
    <form method="post" action="/_content/settings/prompts/add" class="add-row">
      <div class="field"><label>주제 (선택)</label><input name="topic" placeholder="예: 가격·도입 조건"></div>
      <div class="field" style="flex:2"><label>프롬프트</label><input name="prompt" placeholder="AI에게 던질 질문" required></div>
      <button type="submit" class="primary">추가</button>
    </form>
  </div>
</div>
</body></html>"""


SETTINGS_CSS = """
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css');
:root{--bg:#FAFAF9;--line:#E4E4E1;--ink:#14161A;--dim:#5B5F66;--dim2:#9A9DA3;--accent:#1E5E46}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:'Pretendard',sans-serif;
  font-size:14px;line-height:1.6}
.app{max-width:760px;margin:0 auto;padding:32px 24px 64px}
.card{border:1px solid var(--line);border-radius:2px;padding:24px;margin-bottom:16px}
.card h2{font-size:15px;font-weight:500;margin:0 0 6px}
.card .desc{font-size:12.5px;color:var(--dim2);margin-bottom:16px}
.field{margin-bottom:14px}
.field label{display:block;font-size:12.5px;color:var(--dim);margin-bottom:6px}
.field textarea,.field input{width:100%;padding:9px 10px;border:1px solid var(--line);
  border-radius:2px;font-size:13.5px;font-family:inherit;box-sizing:border-box;resize:vertical}
.field textarea{min-height:76px}
.field .hint{font-size:11.5px;color:var(--dim2);margin-top:4px}
.primary{display:inline-block;padding:9px 16px;background:var(--ink);color:#fff;border:none;
  border-radius:2px;font-size:13px;cursor:pointer;text-decoration:none;box-sizing:border-box}
.danger{display:inline-block;padding:5px 10px;background:transparent;color:#c5221f;border:1px solid #f0c9c7;
  border-radius:2px;font-size:12px;cursor:pointer;text-decoration:none;box-sizing:border-box}
.ghost{display:inline-block;padding:5px 10px;background:transparent;color:var(--dim);border:1px solid var(--line);
  border-radius:2px;font-size:12px;cursor:pointer;text-decoration:none;box-sizing:border-box}
.banner-ok{background:#E6F4EC;color:#1E5E46;font-size:12.5px;padding:9px 12px;
  border-radius:2px;margin-bottom:16px}
.banner-err{background:#FBE9E7;color:#c5221f;font-size:12.5px;padding:9px 12px;
  border-radius:2px;margin-bottom:16px}
.list-row{display:flex;align-items:center;gap:12px;padding:12px 0;border-top:1px solid #ECECE9}
.list-row:first-child{border-top:none}
.list-main{flex:1;min-width:0}
.list-title{font-size:13.5px;font-weight:500}
.list-sub{font-size:12px;color:var(--dim2);margin-top:2px}
.list-actions{display:flex;gap:6px;flex:0 0 auto}
.tag{font-size:11px;padding:3px 8px;border-radius:10px;white-space:nowrap}
.tag-yes{background:#E6F4EC;color:#1E5E46}
.tag-no{background:#F0F0EE;color:var(--dim)}
.issue-empty{color:var(--dim2);font-size:13px}
.add-row{display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap}
.add-row .field{flex:1;min-width:140px;margin-bottom:0}
"""


@app.get("/settings/site", response_class=HTMLResponse)
def settings_site_shell(request: Request):
    if not _require_login(request):
        return RedirectResponse("/login", status_code=303)
    return HTMLResponse(sidebar_shell("settings-site", "/_content/settings/site", title="내 사이트"))


@app.get("/_content/settings/site", response_class=HTMLResponse)
def settings_site_content(request: Request, saved: str = "", error: str = ""):
    if not _require_login(request):
        return RedirectResponse("/login", status_code=303)
    if not settings_store.configured(config.SUPABASE_URL, config.SUPABASE_KEY):
        return HTMLResponse(_settings_unconfigured_page("내 사이트"))
    cfg = settings_store.get_site_config(config.SUPABASE_URL, config.SUPABASE_KEY)
    return HTMLResponse(_render_site_settings_page(cfg, saved=bool(saved), error=bool(error)))


@app.post("/_content/settings/site")
def settings_site_save(request: Request, site_urls: str = Form(""), brand_aliases: str = Form("")):
    if not _require_login(request):
        return RedirectResponse("/login", status_code=303)
    ok = settings_store.save_site_config(
        config.SUPABASE_URL, config.SUPABASE_KEY,
        _split_lines(site_urls), _split_lines(brand_aliases),
    )
    qs = "saved=1" if ok else "error=1"
    return RedirectResponse(f"/_content/settings/site?{qs}", status_code=303)


@app.get("/settings/competitors", response_class=HTMLResponse)
def settings_competitors_shell(request: Request):
    if not _require_login(request):
        return RedirectResponse("/login", status_code=303)
    return HTMLResponse(sidebar_shell("settings-competitors", "/_content/settings/competitors", title="경쟁사"))


@app.get("/_content/settings/competitors", response_class=HTMLResponse)
def settings_competitors_content(request: Request, added: str = "", deleted: str = "", error: str = ""):
    if not _require_login(request):
        return RedirectResponse("/login", status_code=303)
    if not settings_store.configured(config.SUPABASE_URL, config.SUPABASE_KEY):
        return HTMLResponse(_settings_unconfigured_page("경쟁사 관리"))
    competitors = settings_store.list_competitors(config.SUPABASE_URL, config.SUPABASE_KEY)
    return HTMLResponse(_render_competitors_settings_page(
        competitors, added=bool(added), deleted=bool(deleted), error=bool(error)))


@app.post("/_content/settings/competitors/add")
def settings_competitors_add(request: Request, name: str = Form(...), domain: str = Form(...), aliases: str = Form("")):
    if not _require_login(request):
        return RedirectResponse("/login", status_code=303)
    ok = settings_store.add_competitor(
        config.SUPABASE_URL, config.SUPABASE_KEY,
        name.strip(), domain.strip(), _split_lines(aliases),
    )
    qs = "added=1" if ok else "error=1"
    return RedirectResponse(f"/_content/settings/competitors?{qs}", status_code=303)


@app.post("/_content/settings/competitors/{competitor_id}/delete")
def settings_competitors_delete(request: Request, competitor_id: str):
    if not _require_login(request):
        return RedirectResponse("/login", status_code=303)
    ok = settings_store.delete_competitor(config.SUPABASE_URL, config.SUPABASE_KEY, competitor_id)
    qs = "deleted=1" if ok else "error=1"
    return RedirectResponse(f"/_content/settings/competitors?{qs}", status_code=303)


@app.get("/settings/prompts", response_class=HTMLResponse)
def settings_prompts_shell(request: Request):
    if not _require_login(request):
        return RedirectResponse("/login", status_code=303)
    return HTMLResponse(sidebar_shell("settings-prompts", "/_content/settings/prompts", title="프롬프트 목록"))


@app.get("/_content/settings/prompts", response_class=HTMLResponse)
def settings_prompts_content(request: Request, added: str = "", deleted: str = "", error: str = ""):
    if not _require_login(request):
        return RedirectResponse("/login", status_code=303)
    if not settings_store.configured(config.SUPABASE_URL, config.SUPABASE_KEY):
        return HTMLResponse(_settings_unconfigured_page("프롬프트 목록"))
    prompts = settings_store.list_prompts(config.SUPABASE_URL, config.SUPABASE_KEY)
    return HTMLResponse(_render_prompts_settings_page(
        prompts, added=bool(added), deleted=bool(deleted), error=bool(error)))


@app.post("/_content/settings/prompts/add")
def settings_prompts_add(request: Request, topic: str = Form(""), prompt: str = Form(...)):
    if not _require_login(request):
        return RedirectResponse("/login", status_code=303)
    ok = settings_store.add_prompt(config.SUPABASE_URL, config.SUPABASE_KEY, topic.strip(), prompt.strip())
    qs = "added=1" if ok else "error=1"
    return RedirectResponse(f"/_content/settings/prompts?{qs}", status_code=303)


@app.post("/_content/settings/prompts/{prompt_id}/toggle")
def settings_prompts_toggle(request: Request, prompt_id: str):
    if not _require_login(request):
        return RedirectResponse("/login", status_code=303)
    prompts = settings_store.list_prompts(config.SUPABASE_URL, config.SUPABASE_KEY)
    current = next((p for p in prompts if str(p["id"]) == prompt_id), None)
    ok = True
    if current is not None:
        ok = settings_store.set_prompt_archived(
            config.SUPABASE_URL, config.SUPABASE_KEY, prompt_id, not current.get("archived")
        )
    qs = "" if ok else "?error=1"
    return RedirectResponse(f"/_content/settings/prompts{qs}", status_code=303)


@app.post("/_content/settings/prompts/{prompt_id}/delete")
def settings_prompts_delete(request: Request, prompt_id: str):
    if not _require_login(request):
        return RedirectResponse("/login", status_code=303)
    ok = settings_store.delete_prompt(config.SUPABASE_URL, config.SUPABASE_KEY, prompt_id)
    qs = "deleted=1" if ok else "error=1"
    return RedirectResponse(f"/_content/settings/prompts?{qs}", status_code=303)


@app.get("/health", response_class=PlainTextResponse)
def health():
    return "ok"
