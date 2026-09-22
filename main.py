"""
URL 즉석분석 웹앱.
로그인한 사용자가 아무 URL이나 입력하면 그 자리에서 크롤링해서
기술 SEO 점수 · 웹 성능(PageSpeed) · AI 노출(Gemini) · GEO 산출물(robots.txt/llms.txt/JSON-LD)을
전부 실데이터로 보여준다. 계정 인증이 필요한 서비스(GSC/GA4/네이버 등)는 애초에
"임의의 URL"에 적용할 수 없는 구조라 이 앱에는 없다 — 소유권 인증 없이는 그 데이터를
아무도 내줄 수 없기 때문.

라우트:
  GET  /login          로그인 폼
  POST /login          로그인 처리
  GET  /logout
  GET  /                URL 즉석분석 셸 (로그인 필요)
  GET  /_content/analyze  실제 분석 처리 (iframe 안에서 로드됨)
  GET  /health
"""

import base64
import html
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import requests
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse, StreamingResponse
from starlette.middleware.sessions import SessionMiddleware

import config
from collectors.tech_audit import audit_technical
from collectors.prescribe import prescribe
from collectors.pagespeed import collect_pagespeed
from collectors.geo_gemini import generate_prompts, run_geo_visibility, guess_brand_name
from collectors.geo_status import check_current_geo_status
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


# ---------------- URL 즉석분석 ----------------

@app.get("/", response_class=HTMLResponse)
def analyze_shell(request: Request):
    if not _require_login(request):
        return RedirectResponse("/login", status_code=303)
    return HTMLResponse(sidebar_shell("analyze", "/_content/analyze", title="URL 분석"))


@app.get("/analyze")
def analyze_legacy_redirect():
    return RedirectResponse("/", status_code=301)


@app.get("/monitor")
@app.get("/ads")
@app.get("/artifacts")
def removed_feature_redirect():
    return RedirectResponse("/", status_code=301)


@app.get("/_content/analyze", response_class=HTMLResponse)
def analyze_content(request: Request, url: str = "", competitors: str = ""):
    if not _require_login(request):
        return RedirectResponse("/login", status_code=303)

    # url이 비어있으면 입력 폼만 보여준다
    if not url.strip():
        return HTMLResponse(ANALYZE_FORM_PAGE)

    from collectors.onpage import USER_AGENT, TIMEOUT

    target = url.strip()
    if not target.startswith("http"):
        target = "https://" + target

    try:
        r = requests.get(target, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        tech = audit_technical(r.url, r.text)
    except Exception as e:
        return HTMLResponse(ANALYZE_FORM_TEMPLATE.replace(
            "{error}", f"<div class='err'>크롤 실패: {type(e).__name__} — URL을 확인해주세요.</div>"
        ).replace("{prev_url}", target).replace("{prev_competitors}", html.escape(competitors)))

    # 여기까지는 빠르고(크롤링 1번) 로컬 계산이라 즉시 끝난다. 느린 건 전부
    # 스트리밍 응답 안에서 병렬로 처리하면서 단계마다 화면을 채워나간다.
    scores = score_categories(tech)
    rx = prescribe(tech=tech)
    brand_name = guess_brand_name(tech) or target
    artifacts = generate_all(tech, brand_name=brand_name or None, social_urls=None)
    competitor_urls = [c.strip() for c in competitors.split(",") if c.strip()][:2]

    return StreamingResponse(
        _stream_analyze(target, r.text, tech, scores, rx, brand_name, artifacts,
                         competitor_urls, USER_AGENT, TIMEOUT),
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


def _crawl_competitor(curl, user_agent, timeout):
    cnorm = curl if curl.startswith("http") else "https://" + curl
    try:
        cr = requests.get(cnorm, headers={"User-Agent": user_agent}, timeout=timeout)
        ctech = audit_technical(cr.url, cr.text)
        return {"url": cnorm, "error": None, "scores": score_categories(ctech),
                "brand": guess_brand_name(ctech) or cnorm}
    except Exception as e:
        return {"url": cnorm, "error": f"{type(e).__name__}: {e}", "scores": None, "brand": cnorm}


def _render_competitor_card(target, scores, competitor_data):
    if not competitor_data:
        return ""
    rows = f"""
    <div class="cmp-row cmp-head"><div>사이트</div><div>검색·AI 접근</div><div>콘텐츠 품질</div><div>브랜드·구조화</div></div>
    <div class="cmp-row"><div>{html.escape(target)} (자사)</div>
      <div>{scores['access']['score']}</div><div>{scores['content']['score']}</div><div>{scores['brand']['score']}</div>
    </div>"""
    for c in competitor_data:
        if c["error"]:
            rows += f"""
            <div class="cmp-row"><div>{html.escape(c['url'])}</div><div class="cmp-err" style="grid-column:span 3">크롤 실패: {html.escape(c['error'])}</div></div>"""
        else:
            s = c["scores"]
            rows += f"""
            <div class="cmp-row"><div>{html.escape(c['url'])}</div>
              <div>{s['access']['score']}</div><div>{s['content']['score']}</div><div>{s['brand']['score']}</div>
            </div>"""
    return f"""
    <div class="card" id="ph-competitors">
      <h2>경쟁사 비교 — SEO/GEO 점수</h2>
      {rows}
    </div>"""


def _cite_domain(u):
    return (urlparse(u).netloc or u).lower().lstrip("www.")


def _render_geo_and_citation(gen_prompts, gen_prompts_error, tech, brand_name, target, competitor_data):
    """AI 노출(Gemini) + 인용 상세 카드를 만든다. 실패하면 가짜 점수 대신 명확한 에러만 표시.
    반환값: (geo_section_html, citation_detail_html)"""
    try:
        if gen_prompts_error:
            raise gen_prompts_error
        competitors_for_gemini = [
            {"name": c["brand"], "domain": c["url"]} for c in competitor_data if not c["error"]
        ]
        geo = run_geo_visibility(
            gen_prompts, config.GEMINI_API_KEY, config.GEMINI_MODEL,
            brand_name=brand_name, brand_domain=target,
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
        quota_banner = ""
        if not live and errored and all("429" in rec["detail"] for rec in errored):
            quota_banner = """
            <div class="issue-empty" style="margin-bottom:14px">
              Gemini 무료 쿼터를 초과했습니다 — 분당 한도면 1분 후, 일일 한도면 하루 지나야 복구됩니다.
              오늘 반복 테스트를 많이 하셨다면 일일 한도일 가능성이 큽니다.
            </div>"""

        # 경쟁사별 노출도/인용 점유율 — 자사와 같은 질문 세트를 같은 응답에서 함께 판별한 것.
        comparison_rows = ""
        if competitors_for_gemini:
            comparison_rows += f"""
            <div class="share-row"><span>{html.escape(brand_name)} (자사)</span>
              <span>노출 {exposure_score if exposure_score is not None else "—"}% · 인용 {citation_share if citation_share is not None else "—"}%</span></div>"""
            for comp in competitors_for_gemini:
                name = comp["name"]
                cm = sum(1 for rec in live if rec["competitor_mentions"].get(name))
                cc = sum(1 for rec in live if rec["competitor_citations"].get(name))
                ce = round(cm / total * 100) if total else None
                cs = round(cc / total * 100) if total else None
                comparison_rows += f"""
                <div class="share-row"><span>{html.escape(name)}</span>
                  <span>노출 {ce if ce is not None else "—"}% · 인용 {cs if cs is not None else "—"}%</span></div>"""

        # 인용 상세 — 실제로 인용된 URL을 도메인 기준 자사/경쟁사/제3자로 분류, 페이지별 순위화.
        target_domain = _cite_domain(target)
        competitor_domains = {_cite_domain(c["domain"]) for c in competitors_for_gemini}
        all_cited = [u for rec in live for u in rec["cited_urls"]]
        page_counts = Counter(all_cited)

        def _classify_source(u):
            d = _cite_domain(u)
            if d == target_domain:
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

        if quota_banner:
            # 전부 429면 "—" 투성이 점수·비교·프롬프트 목록을 늘어놔봐야 정보가 없다.
            # 배너 하나로 끝낸다.
            geo_section = f"""
            <div class="card" id="ph-geo">
              <h2>AI 노출 (Gemini)</h2>
              {quota_banner}
            </div>"""
            return geo_section, citation_detail_section

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
          <div class="sub-inline">자동 생성된 질문 {len(geo['records'])}개 중 {total}개 성공 · Google Search grounding 기반 실데이터</div>
          <div class="scores" style="margin:14px 0 18px;grid-template-columns:repeat(2,1fr)">
            <div class="score-card">
              <div class="score-label">노출도 점수</div>
              <div class="score-num">{exposure_score if exposure_score is not None else "—"}<span>/100</span></div>
            </div>
            <div class="score-card">
              <div class="score-label">인용 점유율</div>
              <div class="score-num">{citation_share if citation_share is not None else "—"}<span>/100</span></div>
            </div>
          </div>
          {comparison_rows}
          {geo_rows}
        </div>"""
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


def _stream_analyze(target, page_html, tech, scores, rx, brand_name, artifacts,
                     competitor_urls, user_agent, timeout):
    """
    독립적인 외부 호출(PSI·현재 GEO 상태·경쟁사 크롤링·Gemini)이 끝나는 대로 해당 카드를
    채워 넣고 진행률을 갱신하는 스트리밍 응답. 브라우저가 청크를 받는 대로 그 안의
    <script>를 실행하기 때문에, 클라이언트 쪽엔 폴링/웹소켓 없이 그냥 평범한 HTML 응답이다.
    (호스팅의 리버스 프록시가 응답을 전부 버퍼링하면 실시간 효과는 없어지지만, 최종
    결과는 동일하게 나온다.)
    """
    gemini_enabled = bool(config.GEMINI_API_KEY)
    steps_total = 2 + len(competitor_urls) + (2 if gemini_enabled else 0)

    seo_cards = _render_seo_score_cards(scores)
    issue_rows = _render_issue_rows(rx)

    if competitor_urls:
        competitor_placeholder = '<div class="card" id="ph-competitors"><h2>경쟁사 비교 — SEO/GEO 점수</h2><div class="issue-empty">크롤링 중…</div></div>'
    else:
        competitor_placeholder = ""

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
    <a class="reanalyze" href="/_content/analyze">다른 URL 분석하기</a>
  </div>
  <div class="progress-wrap">
    <div class="progress-track"><div class="progress-fill" id="pf" style="width:0%"></div></div>
    <div class="progress-label"><span id="pp">0%</span> · 분석 진행 중</div>
  </div>
  <div class="scores">{seo_cards}<div class="score-card" id="ph-psi"><div class="score-label">웹 성능</div><div class="score-num" style="font-size:16px;color:var(--dim2)">측정 중…</div></div></div>
  <div class="card"><h2>발견된 이슈</h2>{issue_rows}</div>
  <div class="card" id="ph-geostatus"><h2>현재 GEO 상태 (실제 확인)</h2><div class="issue-empty">확인 중…</div></div>
  {competitor_placeholder}
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
        futures[ex.submit(check_current_geo_status, target, page_html)] = ("geostatus", None)
        futures[ex.submit(collect_pagespeed, target, api_key=config.PAGESPEED_API_KEY or None)] = ("psi", None)
        for i, curl in enumerate(competitor_urls):
            futures[ex.submit(_crawl_competitor, curl, user_agent, timeout)] = ("competitor", i)
        if gemini_enabled:
            futures[ex.submit(generate_prompts, tech, config.GEMINI_API_KEY, config.GEMINI_MODEL, count=3)] = ("prompts", None)

        competitor_data = [None] * len(competitor_urls)
        gen_state = {"ready": not gemini_enabled, "value": None, "error": None}
        gemini_emitted = False

        def competitors_ready():
            return all(c is not None for c in competitor_data)

        def build_gemini_chunk():
            nonlocal done
            geo_html, cite_html = _render_geo_and_citation(
                gen_state["value"], gen_state["error"], tech, brand_name, target,
                [c for c in competitor_data if c is not None],
            )
            done += 1
            script = f"fillEl('ph-geo','{_b64(geo_html)}');"
            if cite_html:
                script += f"fillEl('ph-citation','{_b64(cite_html)}');"
            return f"<script>{script}</script>\n"

        for fut in as_completed(futures):
            kind, idx = futures[fut]

            if kind == "geostatus":
                geo_status = fut.result()
                frag = _render_geo_status_card(geo_status)
                done += 1
                yield f"<script>fillEl('ph-geostatus','{_b64(frag)}');</script>\n"
                yield progress_script()

            elif kind == "psi":
                psi = fut.result()
                frag = _render_psi_card(psi)
                done += 1
                yield f"<script>fillEl('ph-psi','{_b64(frag)}');</script>\n"
                yield progress_script()

            elif kind == "competitor":
                try:
                    competitor_data[idx] = fut.result()
                except Exception as e:
                    competitor_data[idx] = {"url": competitor_urls[idx], "error": str(e),
                                             "scores": None, "brand": competitor_urls[idx]}
                done += 1
                if competitors_ready():
                    frag = _render_competitor_card(target, scores, competitor_data)
                    if frag:
                        yield f"<script>fillEl('ph-competitors','{_b64(frag)}');</script>\n"
                yield progress_script()
                if gemini_enabled and gen_state["ready"] and competitors_ready() and not gemini_emitted:
                    gemini_emitted = True
                    yield build_gemini_chunk()
                    yield progress_script()

            elif kind == "prompts":
                try:
                    gen_state["value"] = fut.result()
                except Exception as e:
                    gen_state["error"] = e
                gen_state["ready"] = True
                done += 1
                yield progress_script()
                if competitors_ready() and not gemini_emitted:
                    gemini_emitted = True
                    yield build_gemini_chunk()
                    yield progress_script()

        # 안전장치: 어떤 이유로든 위 루프에서 못 내보냈으면 마지막에 강제로 내보낸다.
        if gemini_enabled and not gemini_emitted:
            yield build_gemini_chunk()
            yield progress_script()

    yield "</body></html>"


def _esc_html(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


ANALYZE_FORM_TEMPLATE = """
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
button:disabled{opacity:.6;cursor:default}
.err{color:#c5221f;font-size:12.5px;margin-bottom:10px}
.wait-note{display:none;margin-top:14px;font-size:11.5px;color:#9A9DA3;text-align:center;line-height:1.5}
label.sub-label{display:block;font-size:11.5px;color:#9A9DA3;margin:2px 0 6px}
</style></head><body>
<form class="box" method="get" action="/_content/analyze" onsubmit="
  var b=this.querySelector('button');
  b.disabled=true; b.innerText='분석 중입니다...';
  this.querySelector('.wait-note').style.display='block';
  try{localStorage.setItem('geo_competitors', this.competitors.value);}catch(e){}
">
  <h1>URL 분석</h1>
  <p>분석할 사이트 주소를 입력하면 기술 진단과 GEO 산출물을 바로 생성합니다.</p>
  {error}
  <input name="url" placeholder="https://example.com" value="{prev_url}" autofocus>
  <label class="sub-label">경쟁사 URL (선택, 쉼표로 구분, 최대 2개) — 한 번 넣으면 다음에도 기억합니다</label>
  <input name="competitors" placeholder="https://competitor1.com, https://competitor2.com" value="{prev_competitors}">
  <button type="submit">분석하기</button>
  <div class="wait-note">결과 화면으로 이동한 뒤 실시간 진행률과 함께 단계별로 채워집니다.</div>
</form>
<script>
(function(){
  var el = document.querySelector('input[name="competitors"]');
  if (el && !el.value) {
    try {
      var saved = localStorage.getItem('geo_competitors');
      if (saved) el.value = saved;
    } catch (e) {}
  }
})();
</script>
</body></html>
"""
ANALYZE_FORM_PAGE = ANALYZE_FORM_TEMPLATE.replace("{error}", "").replace("{prev_url}", "").replace("{prev_competitors}", "")


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
.cmp-row{display:grid;grid-template-columns:2fr 1fr 1fr 1fr;gap:8px;padding:10px 0;
  border-top:1px solid #ECECE9;font-size:13px;align-items:center}
.cmp-row:first-child{border-top:none}
.cmp-head{font-weight:500;color:var(--dim);font-size:11.5px}
.cmp-err{color:#c5221f;font-size:12px}
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


@app.get("/health", response_class=PlainTextResponse)
def health():
    return "ok"
