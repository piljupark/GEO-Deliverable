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

import html
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
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

    import requests as _rq
    from collectors.onpage import USER_AGENT, TIMEOUT

    target = url.strip()
    if not target.startswith("http"):
        target = "https://" + target

    try:
        r = _rq.get(target, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        tech = audit_technical(r.url, r.text)
    except Exception as e:
        return HTMLResponse(ANALYZE_FORM_TEMPLATE.replace(
            "{error}", f"<div class='err'>크롤 실패: {type(e).__name__} — URL을 확인해주세요.</div>"
        ).replace("{prev_url}", target).replace("{prev_competitors}", html.escape(competitors)))

    scores = score_categories(tech)
    rx = prescribe(tech=tech)

    # 브랜드명은 매번 크롤링한 이 URL의 실제 정보에서만 뽑는다 — 관리자가 설정한
    # 고정 브랜드명을 쓰면 다른 사이트를 분석할 때 엉뚱한 이름이 섞여 들어간다.
    brand_name = guess_brand_name(tech) or target
    artifacts = generate_all(tech, brand_name=brand_name or None, social_urls=None)

    competitor_urls = [c.strip() for c in competitors.split(",") if c.strip()][:2]

    def _crawl_competitor(curl):
        cnorm = curl if curl.startswith("http") else "https://" + curl
        try:
            cr = _rq.get(cnorm, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
            ctech = audit_technical(cr.url, cr.text)
            return {"url": cnorm, "error": None, "scores": score_categories(ctech),
                    "brand": guess_brand_name(ctech) or cnorm}
        except Exception as e:
            return {"url": cnorm, "error": f"{type(e).__name__}: {e}", "scores": None, "brand": cnorm}

    # PageSpeed·현재 GEO 상태 확인·경쟁사 크롤링·Gemini 질문 생성은 서로 의존관계가
    # 없는 독립적인 외부 호출이라 순서대로 기다릴 필요가 없다 — 동시에 실행해서
    # 전체 대기시간을 "합계"가 아니라 "가장 느린 것 하나" 수준으로 줄인다.
    with ThreadPoolExecutor(max_workers=5) as ex:
        fut_psi = ex.submit(collect_pagespeed, target, api_key=config.PAGESPEED_API_KEY or None)
        fut_geo_status = ex.submit(check_current_geo_status, target, r.text)
        fut_competitors = [ex.submit(_crawl_competitor, c) for c in competitor_urls]
        fut_prompts = (
            ex.submit(generate_prompts, tech, config.GEMINI_API_KEY, config.GEMINI_MODEL, count=3)
            if config.GEMINI_API_KEY else None
        )

        psi = fut_psi.result()
        geo_status = fut_geo_status.result()
        competitor_data = [f.result() for f in fut_competitors]
        gen_prompts, gen_prompts_error = None, None
        if fut_prompts is not None:
            try:
                gen_prompts = fut_prompts.result()
            except Exception as e:
                gen_prompts_error = e

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
    elif psi["source"] == "LIVE" and psi["performance"] is None:
        score_cards += """
        <div class="score-card">
          <div class="score-label">웹 성능</div>
          <div class="score-num">측정 불가</div>
          <div class="score-detail">응답은 왔지만 성능 점수가 비어 있습니다.</div>
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

    # 현재 GEO 상태 — robots.txt/llms.txt/sitemap.xml/JSON-LD가 실제로 있는지, AI 크롤러를
    # 막고 있진 않은지 있는 그대로 확인해서 "잘된 점"/"개선점"으로 정리한다.
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
    geo_status_section = f"""
    <div class="card">
      <h2>현재 GEO 상태 (실제 확인)</h2>
      <div class="sub-inline">robots.txt·llms.txt·sitemap.xml·JSON-LD를 지금 이 사이트에서 직접 가져와 확인한 결과입니다.</div>
      {good_rows}
      {improve_rows}
    </div>"""

    # 경쟁사 SEO/GEO 점수 비교 카드 (경쟁사를 입력했을 때만)
    competitor_score_section = ""
    if competitor_data:
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
        competitor_score_section = f"""
        <div class="card">
          <h2>경쟁사 비교 — SEO/GEO 점수</h2>
          {rows}
        </div>"""

    # AI 노출(Gemini) — 크롤링한 사이트 정보로 질문을 자동 생성해서 실제로 Gemini에 물어봄.
    # mock 없음: 키가 없거나 실패하면 명확한 안내만 표시하고 가짜 점수는 절대 채우지 않는다.
    # 경쟁사도 고정 목록을 쓰지 않는다 — 분석 대상이 매번 바뀌는데 고정 경쟁사를 대입하면 무의미하다.
    citation_detail_section = ""
    if not config.GEMINI_API_KEY:
        geo_section = """
        <div class="card">
          <h2>AI 노출 (Gemini)</h2>
          <div class="issue-empty">GEMINI_API_KEY가 설정되지 않아 확인하지 못했습니다. aistudio.google.com/apikey 에서 무료로 발급할 수 있습니다.</div>
        </div>"""
    else:
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
            live = [r for r in geo["records"] if r["status"] == "LIVE"]
            errored = [r for r in geo["records"] if r["status"] != "LIVE"]
            total = len(live)
            mentioned_count = sum(1 for r in live if r["mentioned"])
            cited_count = sum(1 for r in live if r["cited"])
            exposure_score = round(mentioned_count / total * 100) if total else None
            citation_share = round(cited_count / total * 100) if total else None

            # 전부 429(쿼터 초과)로 실패한 경우, 개별 에러 대신 원인을 명확히 알려준다.
            quota_banner = ""
            if not live and errored and all("429" in r["detail"] for r in errored):
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
                    cm = sum(1 for r in live if r["competitor_mentions"].get(name))
                    cc = sum(1 for r in live if r["competitor_citations"].get(name))
                    ce = round(cm / total * 100) if total else None
                    cs = round(cc / total * 100) if total else None
                    comparison_rows += f"""
                    <div class="share-row"><span>{html.escape(name)}</span>
                      <span>노출 {ce if ce is not None else "—"}% · 인용 {cs if cs is not None else "—"}%</span></div>"""

            # 인용 상세 — 실제로 인용된 URL을 도메인 기준 자사/경쟁사/제3자로 분류, 페이지별 순위화.
            from collections import Counter
            from urllib.parse import urlparse as _urlparse

            def _cite_domain(u):
                return (_urlparse(u).netloc or u).lower().lstrip("www.")

            target_domain = _cite_domain(target)
            competitor_domains = {_cite_domain(c["domain"]) for c in competitors_for_gemini}
            all_cited = [u for r in live for u in r["cited_urls"]]
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
                <div class="card">
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
                <div class="card">
                  <h2>AI 노출 (Gemini)</h2>
                  {quota_banner}
                </div>"""
            else:
                geo_rows = ""
                for r in geo["records"]:
                    if r["status"] != "LIVE":
                        status_html = f'<span class="tag tag-err">실패: {html.escape(r["detail"])}</span>'
                        geo_rows += f"""
                        <div class="prompt-row">
                          <div class="prompt-text">{html.escape(r['prompt'])}</div>
                          <div class="prompt-status">{status_html}</div>
                        </div>"""
                        continue
                    m = '<span class="tag tag-yes">언급됨</span>' if r["mentioned"] else '<span class="tag tag-no">언급 없음</span>'
                    c = '<span class="tag tag-yes">인용됨</span>' if r["cited"] else '<span class="tag tag-no">인용 없음</span>'
                    sites_html = f'<div class="prompt-site"><span class="site-label">자사</span>{m}{c}</div>'
                    for comp in competitors_for_gemini:
                        name = comp["name"]
                        cm = '<span class="tag tag-yes">언급됨</span>' if r["competitor_mentions"].get(name) else '<span class="tag tag-no">언급 없음</span>'
                        cc2 = '<span class="tag tag-yes">인용됨</span>' if r["competitor_citations"].get(name) else '<span class="tag tag-no">인용 없음</span>'
                        sites_html += f'<div class="prompt-site"><span class="site-label">{html.escape(name)}</span>{cm}{cc2}</div>'
                    geo_rows += f"""
                    <div class="prompt-row">
                      <div class="prompt-text">{html.escape(r['prompt'])}</div>
                      <div class="prompt-sites">{sites_html}</div>
                    </div>"""

                geo_section = f"""
                <div class="card">
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
        except Exception as e:
            if "429" in str(e):
                msg = "Gemini 무료 쿼터를 초과했습니다 — 분당 한도면 1분 후, 일일 한도면 하루 지나야 복구됩니다."
            else:
                msg = f"확인 실패: {html.escape(str(e))}"
            geo_section = f"""
            <div class="card">
              <h2>AI 노출 (Gemini)</h2>
              <div class="issue-empty">{msg}</div>
            </div>"""

    page = ANALYZE_RESULT_PAGE.format(
        url=html.escape(target),
        score_cards=score_cards,
        issue_rows=issue_rows,
        geo_status_section=geo_status_section,
        competitor_score_section=competitor_score_section,
        geo_section=geo_section,
        citation_detail_section=citation_detail_section,
        robots=_esc_html(artifacts["robots_txt"]),
        llms=_esc_html(artifacts["llms_txt"]),
        jsonld=_esc_html(artifacts["json_ld"]),
    )
    return HTMLResponse(page)


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
.wait-note{display:none;margin-top:10px;font-size:12px;color:#5B5F66;text-align:center}
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
  <div class="wait-note">사이트 크롤링·현재 GEO 상태·웹 성능·AI 노출 확인을 순서대로 진행합니다. 경쟁사를 넣으면 더 걸릴 수 있어요.</div>
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
.sub-inline{{font-size:12px;color:var(--dim2);margin-bottom:4px}}
.prompt-row{{display:flex;justify-content:space-between;align-items:center;gap:12px;
  padding:12px 0;border-top:1px solid #ECECE9}}
.prompt-row:first-child{{border-top:none}}
.prompt-text{{font-size:13.5px;flex:1}}
.prompt-status{{display:flex;gap:6px;flex:0 0 auto}}
.tag{{font-size:11px;padding:3px 8px;border-radius:10px;white-space:nowrap}}
.tag-yes{{background:#E6F4EC;color:#1E5E46}}
.tag-no{{background:#F0F0EE;color:var(--dim)}}
.tag-err{{background:#FBE9E7;color:#c5221f}}
.share-row{{display:flex;justify-content:space-between;padding:8px 0;border-top:1px solid #ECECE9;font-size:13px}}
.share-row:first-child{{border-top:none}}
.cmp-row{{display:grid;grid-template-columns:2fr 1fr 1fr 1fr;gap:8px;padding:10px 0;
  border-top:1px solid #ECECE9;font-size:13px;align-items:center}}
.cmp-row:first-child{{border-top:none}}
.cmp-head{{font-weight:500;color:var(--dim);font-size:11.5px}}
.cmp-err{{color:#c5221f;font-size:12px}}
.prompt-sites{{display:flex;flex-direction:column;gap:5px;align-items:flex-end;flex:0 0 auto}}
.prompt-site{{display:flex;align-items:center;gap:6px;font-size:11px}}
.site-label{{color:var(--dim2);min-width:60px;text-align:right}}
.stack-bar{{display:flex;height:26px;border-radius:4px;overflow:hidden;gap:2px;background:#ECECE9;margin-top:6px}}
.legend-row{{display:flex;flex-wrap:wrap;gap:14px;margin-top:10px}}
.legend-item{{display:flex;align-items:center;gap:6px;font-size:12.5px;color:var(--dim)}}
.legend-swatch{{width:10px;height:10px;border-radius:2px;flex:0 0 auto}}
.cite-list-title{{font-size:12.5px;font-weight:500;color:var(--dim);margin:18px 0 6px}}
.cite-row{{display:flex;align-items:center;gap:10px;padding:9px 0;border-top:1px solid #ECECE9}}
.cite-row:first-child{{border-top:none}}
.cite-url{{flex:1;font-size:12.5px;color:var(--ink);text-decoration:none;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}}
.cite-url:hover{{text-decoration:underline}}
.cite-count{{font-size:12px;color:var(--dim2);flex:0 0 auto}}
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
  {geo_status_section}
  {competitor_score_section}
  {geo_section}
  {citation_detail_section}
  <div class="card">
    <div class="card-h"><h2>권장 robots.txt</h2><button class="copy" onclick="cp('r')">복사</button></div>
    <pre id="r">{robots}</pre>
  </div>
  <div class="card">
    <div class="card-h"><h2>권장 llms.txt</h2><button class="copy" onclick="cp('l')">복사</button></div>
    <pre id="l">{llms}</pre>
  </div>
  <div class="card">
    <div class="card-h"><h2>권장 JSON-LD</h2><button class="copy" onclick="cp('j')">복사</button></div>
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
