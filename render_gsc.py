"""
Google Search Console 스타일 대시보드 렌더러.
밝은 배경, 정보 밀집, 시계열 라인차트(순수 SVG), 지표 타일, 정렬 테이블.
외부 의존 없음 — HTML 파일 하나로 완결.
"""

import html
from datetime import datetime


def _esc(s):
    return html.escape(str(s if s is not None else ""))


def _fmt(n):
    """1234567 -> 1,234,567"""
    try:
        return f"{int(round(n)):,}"
    except Exception:
        return str(n)


def _line_chart(series, key, color, height=180, label=""):
    """단일 지표 시계열 SVG 라인차트."""
    if not series:
        return "<div class='chart-empty'>데이터 없음</div>"
    vals = [p[key] for p in series]
    n = len(vals)
    vmax = max(vals) or 1
    vmin = min(vals)
    pad = 8
    w = 760
    h = height
    plot_h = h - 30
    dx = (w - pad * 2) / max(n - 1, 1)

    def x(i): return pad + i * dx
    def y(v):
        if vmax == vmin:
            return plot_h / 2 + 10
        return 10 + (plot_h - 10) * (1 - (v - vmin) / (vmax - vmin))

    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))
    # area
    area = f"{x(0):.1f},{plot_h+10:.1f} " + pts + f" {x(n-1):.1f},{plot_h+10:.1f}"

    # x축 라벨 (처음/중간/끝)
    idxs = [0, n // 2, n - 1]
    xlabels = ""
    for i in idxs:
        d = series[i]["date"][5:]  # MM-DD
        xlabels += f'<text x="{x(i):.1f}" y="{h-4}" class="ax">{d}</text>'

    return f"""
    <svg viewBox="0 0 {w} {h}" class="linechart" preserveAspectRatio="none">
      <defs>
        <linearGradient id="grad-{key}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="{color}" stop-opacity="0.22"/>
          <stop offset="100%" stop-color="{color}" stop-opacity="0"/>
        </linearGradient>
      </defs>
      <polygon points="{area}" fill="url(#grad-{key})"/>
      <polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2"
                stroke-linejoin="round" stroke-linecap="round"/>
      {xlabels}
    </svg>"""


def _metric_tile(label, value, sub, color, active=False):
    cls = "tile active" if active else "tile"
    return f"""
    <div class="{cls}" style="--accent:{color}">
      <div class="tile-label">{_esc(label)}</div>
      <div class="tile-value">{_esc(value)}</div>
      <div class="tile-sub">{_esc(sub)}</div>
    </div>"""


def _perf_table(rows, first_col, is_url=False):
    body = ""
    for r in rows:
        key = r["key"]
        if is_url:
            disp = key.replace("https://", "").replace("http://", "")
            key_html = f'<span class="k-url" title="{_esc(key)}">{_esc(disp[:60])}</span>'
        else:
            key_html = f'<span class="k-query">{_esc(key)}</span>'
        body += f"""
        <tr>
          <td>{key_html}</td>
          <td class="num">{_fmt(r['clicks'])}</td>
          <td class="num">{_fmt(r['impressions'])}</td>
          <td class="num">{r['ctr']}<span class="pct">%</span></td>
          <td class="num pos">{r['position']}</td>
        </tr>"""
    return f"""
    <table class="ptbl">
      <thead><tr>
        <th>{_esc(first_col)}</th><th class="num">클릭</th>
        <th class="num">노출</th><th class="num">CTR</th><th class="num">평균순위</th>
      </tr></thead>
      <tbody>{body}</tbody>
    </table>"""


def _index_table(rows):
    body = ""
    vmap = {
        "PASS": ("색인됨", "v-pass"),
        "NEUTRAL": ("보류", "v-neutral"),
        "FAIL": ("제외됨", "v-fail"),
        "ERROR": ("오류", "v-fail"),
        "UNKNOWN": ("확인불가", "v-neutral"),
    }
    for r in rows:
        label, cls = vmap.get(r["verdict"], ("?", "v-neutral"))
        crawl = r["last_crawl"][:10] if r.get("last_crawl") else "—"
        disp = r["url"].replace("https://", "").replace("http://", "")
        body += f"""
        <tr>
          <td><span class="k-url" title="{_esc(r['url'])}">{_esc(disp[:52])}</span></td>
          <td><span class="verdict {cls}">{label}</span></td>
          <td class="cov">{_esc(r.get('coverage',''))}</td>
          <td class="crawl">{crawl}</td>
        </tr>"""
    return f"""
    <table class="ptbl">
      <thead><tr><th>URL</th><th>상태</th><th>커버리지</th><th>최근 크롤</th></tr></thead>
      <tbody>{body}</tbody>
    </table>"""


def _compare_tile(label, block, invert=False, unit=""):
    """기간 비교 타일. invert=True면 값이 내려가는 게 좋은 것(순위)."""
    cur = block["current"]
    chg = block["change_pct"]
    if chg is None:
        arrow, cls, chg_txt = "", "flat", "—"
    else:
        up = chg > 0
        good = (not up) if invert else up
        arrow = "▲" if up else ("▼" if chg < 0 else "—")
        cls = "up" if good else ("down" if chg != 0 else "flat")
        chg_txt = f"{arrow} {abs(chg)}%"
    return f"""
    <div class="cmp-tile">
      <div class="cmp-label">{_esc(label)}</div>
      <div class="cmp-cur">{_fmt(cur) if not unit else str(cur)+unit}</div>
      <div class="cmp-chg {cls}">{chg_txt}<span class="cmp-prev">직전 {_fmt(block['previous']) if not unit else str(block['previous'])+unit}</span></div>
    </div>"""


def _compare_panel(compare):
    if not compare:
        return ""
    tiles = (
        _compare_tile("클릭수", compare["clicks"]) +
        _compare_tile("노출수", compare["impressions"]) +
        _compare_tile("CTR", compare["ctr"], unit="%") +
        _compare_tile("평균순위", compare["position"], invert=True)
    )
    return f"""
    <section class="card">
      <div class="card-h"><h2>기간 비교</h2><div class="card-meta">현재 기간 vs 직전 동일 기간</div></div>
      <div class="cmp-grid">{tiles}</div>
      <div class="card-note">평균순위는 숫자가 <b>내려갈수록</b> 개선(초록)입니다. 나머지는 올라갈수록 개선.</div>
    </section>"""


def _opportunity_panel(rows):
    if not rows:
        return ""
    body = ""
    for r in rows[:12]:
        body += f"""
        <tr>
          <td><span class="k-query">{_esc(r['key'])}</span></td>
          <td class="num">{r['position']}</td>
          <td class="num">{_fmt(r['impressions'])}</td>
          <td class="num">{r['ctr']}<span class="pct">%</span></td>
          <td class="num gain">+{_fmt(r['potential_gain'])}</td>
        </tr>"""
    return f"""
    <section class="card">
      <div class="card-h"><h2>기회 키워드 <span class="tag-hot">11–20위</span></h2>
        <div class="card-meta">1페이지 진입 임박 · 우선 공략</div></div>
      <table class="ptbl"><thead><tr>
        <th>검색어</th><th class="num">현재순위</th><th class="num">노출</th>
        <th class="num">CTR</th><th class="num">예상 클릭↑</th>
      </tr></thead><tbody>{body}</tbody></table>
      <div class="card-note">현재 2페이지(11–20위)에 있어 조금만 순위를 올리면 클릭이 크게 늘 수 있는 검색어. '예상 클릭↑'은 5위권 진입 시 추가 클릭 추정치입니다.</div>
    </section>"""


def _ctr_panel(rows):
    if not rows:
        return ""
    body = ""
    for r in rows[:12]:
        body += f"""
        <tr>
          <td><span class="k-query">{_esc(r['key'])}</span></td>
          <td class="num">{r['position']}</td>
          <td class="num">{_fmt(r['impressions'])}</td>
          <td class="num low">{r['ctr']}%</td>
          <td class="num">{r['expected_ctr']}%</td>
          <td class="num gain">+{_fmt(r['missed_clicks'])}</td>
        </tr>"""
    return f"""
    <section class="card">
      <div class="card-h"><h2>CTR 개선 후보 <span class="tag-warn">제목·설명</span></h2>
        <div class="card-meta">노출은 많은데 클릭률이 낮음</div></div>
      <table class="ptbl"><thead><tr>
        <th>검색어</th><th class="num">순위</th><th class="num">노출</th>
        <th class="num">현재CTR</th><th class="num">기대CTR</th><th class="num">놓친 클릭</th>
      </tr></thead><tbody>{body}</tbody></table>
      <div class="card-note">순위 대비 클릭률이 기대치보다 낮은 검색어. 해당 페이지의 &lt;title&gt;·meta description을 매력적으로 고치면 순위 변화 없이도 클릭을 회복할 수 있습니다.</div>
    </section>"""


def _buckets_panel(buckets):
    if not buckets:
        return ""
    total = sum(b["count"] for b in buckets.values()) or 1
    colors = {"1-3위": "var(--accent)", "4-10위": "var(--ink)",
              "11-20위": "var(--ink-soft)", "21위+": "var(--ink-faint)"}
    bars = ""
    for name, b in buckets.items():
        w = round(b["count"] / total * 100)
        bars += f"""
        <div class="bkt-row">
          <div class="bkt-name">{_esc(name)}</div>
          <div class="bkt-track"><div class="bkt-fill" style="width:{w}%;background:{colors[name]}"></div></div>
          <div class="bkt-val">{b['count']}개 <span class="bkt-imp">· 노출 {_fmt(b['impressions'])}</span></div>
        </div>"""
    return f"""
    <section class="card">
      <div class="card-h"><h2>순위 구간 분포</h2><div class="card-meta">검색어 {total}개 기준</div></div>
      <div class="bkts">{bars}</div>
      <div class="card-note">11–20위 구간이 두꺼우면 '기회 키워드'가 많다는 뜻 — 여기를 밀어올리는 게 가장 효율적입니다.</div>
    </section>"""


def _competitor_panel(sites):
    """내 사이트 vs 경쟁사 기술 지표 매트릭스. 각 지표에서 최고값 강조."""
    if not sites or len(sites) < 2:
        return ""

    reach = [s for s in sites if s.get("reachable")]
    if len(reach) < 2:
        return ""

    # 비교 지표 정의: (라벨, 키, 높을수록 좋음?, 포맷)
    metrics = [
        ("기술 점수", "score", True, lambda v: str(v)),
        ("로드(ms)", "load_ms", False, lambda v: _fmt(v)),
        ("title 길이", "title_len", None, lambda v: str(v)),   # 적정범위라 승패 판정 안함
        ("meta 길이", "meta_desc_len", None, lambda v: str(v)),
        ("H1 개수", "h1_count", None, lambda v: str(v)),
        ("본문 단어", "word_count", True, lambda v: _fmt(v)),
        ("스키마 종류", "schema_count", True, lambda v: str(v)),
        ("내부링크", "internal_links", True, lambda v: str(v)),
        ("alt 누락", "img_missing_alt", False, lambda v: str(v)),
        ("이슈 수", "issue_count", False, lambda v: str(v)),
    ]

    # 헤더 (사이트명)
    headers = ""
    for s in sites:
        cls = "col-me" if s.get("is_me") else ""
        badge = '<span class="me-badge">나</span>' if s.get("is_me") else ""
        dom = s.get("domain", "")
        headers += f'<th class="{cls}">{badge}<div class="site-dom">{_esc(dom)}</div></th>'

    # 각 지표 행
    rows = ""
    for label, key, higher_better, fmt in metrics:
        # 최고값 계산 (reachable 사이트 중)
        best_idx = None
        if higher_better is not None:
            vals = [(i, s.get(key, 0)) for i, s in enumerate(sites) if s.get("reachable")]
            if vals:
                best = max(vals, key=lambda x: x[1]) if higher_better else min(vals, key=lambda x: x[1])
                best_idx = best[0]
        cells = ""
        for i, s in enumerate(sites):
            if not s.get("reachable"):
                cells += '<td class="cell-na">—</td>'
                continue
            v = s.get(key, 0)
            win = "cell-win" if i == best_idx else ""
            me = "col-me" if s.get("is_me") else ""
            cells += f'<td class="{win} {me}">{fmt(v)}</td>'
        rows += f'<tr><td class="metric-name">{_esc(label)}</td>{cells}</tr>'

    # 도달 실패 사이트 안내
    unreachable = [s for s in sites if not s.get("reachable")]
    warn = ""
    if unreachable:
        names = ", ".join(_esc(s.get("domain", "?")) for s in unreachable)
        warn = f'<div class="card-note">크롤 실패(자바스크립트 렌더링/차단 가능): {names}. 이 사이트는 비교에서 제외됐습니다.</div>'

    return f"""
    <section class="card">
      <div class="card-h"><h2>경쟁사 온페이지 비교</h2>
        <div class="card-meta">공개 HTML 기준 기술 지표 · 초록=해당 지표 우위</div></div>
      <div class="cmp-scroll">
        <table class="ctbl">
          <thead><tr><th class="metric-name"></th>{headers}</tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
      <div class="card-note"><b>비교 범위 주의</b> — 여기 지표는 각 사이트의 <b>공개 HTML로 확인 가능한 기술 요소</b>뿐입니다.
      경쟁사의 실제 검색 트래픽·키워드 순위·백링크는 상대방 데이터라 알 수 없으며, 그건 유료 서드파티(Ahrefs/DataForSEO 등) 영역입니다.
      title·meta·H1 길이는 우열이 아니라 적정 범위 문제라 승패 표시를 하지 않았습니다.{warn}</div>
    </section>"""


def _prescription_panel(rx):
    """진단 신호등 + 우선순위 To-Do 리스트."""
    if not rx or not rx.get("todos"):
        return ""
    s = rx["summary"]
    # 단일 강조색 원칙: good만 accent(채움), 나머지는 ink(테두리만) — 색상이 아니라 채움 여부로 구분
    hc = s["health_class"]

    # 신호등 헤더
    header = f"""
    <div class="rx-health">
      <div class="rx-dot {hc}"></div>
      <div class="rx-health-body">
        <div class="rx-health-label">{_esc(s['health_label'])}</div>
        <div class="rx-health-msg">{_esc(s['health_msg'])}</div>
      </div>
      <div class="rx-counts">
        <div class="rx-count"><b>{s['total']}</b><span>할 일</span></div>
        <div class="rx-count"><b>기술 {s['tech']}</b><span>프론트</span></div>
        <div class="rx-count"><b>콘텐츠 {s['content']}</b><span>데이터</span></div>
      </div>
    </div>"""

    # To-Do 카드들
    impact_label = {3: "효과 큼", 2: "효과 중간", 1: "효과 작음"}
    effort_label = {1: "쉬움", 2: "보통", 3: "어려움"}
    cards = ""
    for i, t in enumerate(rx["todos"], 1):
        is_tech = t["category"] == "tech"
        cat_name = "기술 · 프론트" if is_tech else "콘텐츠 · 데이터"
        cat_cls = "cat-tech" if is_tech else "cat-content"
        imp_cls = "imp3" if t["impact"] == 3 else ("imp2" if t["impact"] == 2 else "imp1")
        ev = f'<span class="rx-ev">{_esc(t["evidence"])}</span>' if t.get("evidence") else ""
        cards += f"""
        <div class="rx-card">
          <div class="rx-card-top">
            <span class="rx-num">{i}</span>
            <span class="rx-cat {cat_cls}">{cat_name}</span>
            <span class="rx-tag {imp_cls}">{impact_label[t['impact']]}</span>
            <span class="rx-tag eff">{effort_label[t['effort']]}</span>
            {ev}
          </div>
          <div class="rx-title">{_esc(t['title'])}</div>
          <div class="rx-why"><b>왜</b> {_esc(t['why'])}</div>
          <div class="rx-how"><b>어떻게</b> {_esc(t['how'])}</div>
        </div>"""

    return f"""
    <section class="card rx-panel">
      <div class="card-h"><h2>진단 &amp; 처방</h2>
        <div class="card-meta">효과 크고 쉬운 순서 · 기술은 프론트 작업 / 콘텐츠는 데이터 작업</div></div>
      {header}
      <div class="rx-list">{cards}</div>
      <div class="card-note">우선순위는 <b>효과 ÷ 난이도</b>로 자동 계산했습니다. 프론트 개발자라면 기술 항목을 작업 목록으로, 콘텐츠 담당은 콘텐츠 항목을 참고하세요. 각 항목의 근거 데이터는 아래 패널에서 확인할 수 있습니다.</div>
    </section>"""


def _related_panel(related, terms):
    """GSC 실데이터에서 지정 단어가 포함된 실제 검색어 전부."""
    terms_str = ", ".join(f"'{t}'" for t in terms) if terms else ""
    if related is None:
        return ""
    if not related:
        return f"""
        <section class="card">
          <div class="card-h"><h2>연관 검색어 탐색</h2>
            <div class="card-meta">GSC 실데이터 기준 · {_esc(terms_str)} 포함</div></div>
          <div class="card-note">{_esc(terms_str)}가 포함된 검색어로 노출된 기록이 아직 없습니다.
          아직 그 키워드로 검색결과에 뜬 적이 없다는 뜻 — 관련 콘텐츠를 만들면 새로 노출을 만들 수 있습니다.</div>
        </section>"""

    rows = ""
    for q in related:
        rows += f"""
        <tr>
          <td><span class="k-query">{_esc(q['key'])}</span></td>
          <td class="num">{_fmt(q['clicks'])}</td>
          <td class="num">{_fmt(q['impressions'])}</td>
          <td class="num">{q['ctr']}<span class="pct">%</span></td>
          <td class="num pos">{q['position']}</td>
        </tr>"""

    return f"""
    <section class="card">
      <div class="card-h"><h2>연관 검색어 탐색 <span class="tag-hot">실데이터</span></h2>
        <div class="card-meta">{_esc(terms_str)} 포함 · {len(related)}건 · 이미 노출된 적 있는 진짜 검색어</div></div>
      <table class="ptbl"><thead><tr>
        <th>검색어</th><th class="num">클릭</th><th class="num">노출</th>
        <th class="num">CTR</th><th class="num">평균순위</th>
      </tr></thead><tbody>{rows}</tbody></table>
      <div class="card-note">추측이 아니라 실제로 사람들이 검색해서 이 사이트가 노출됐던 검색어입니다.
      순위가 낮은 것부터 콘텐츠를 보강하면 효과적입니다.</div>
    </section>"""


def _sparkline(points, key, color, width=140, height=40):
    """작은 추세선. points: [{date, impressions,...}], key: 'impressions' 또는 'gsc_position'."""
    vals = [p[key] for p in points if p.get(key) is not None]
    if len(vals) < 2:
        return f'<svg width="{width}" height="{height}"></svg>'
    vmax, vmin = max(vals), min(vals)
    pad = 4
    n = len(vals)
    dx = (width - pad * 2) / max(n - 1, 1)

    def x(i): return pad + i * dx
    def y(v):
        if vmax == vmin:
            return height / 2
        return pad + (height - pad * 2) * (1 - (v - vmin) / (vmax - vmin))

    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))
    return f"""<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">
      <polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2"
                stroke-linejoin="round" stroke-linecap="round"/>
    </svg>"""


def _growth_panel(growth):
    """타겟 키워드 성장 추적 — 실행마다 쌓인 기록의 추세."""
    if not growth:
        return ""
    rows = ""
    for g in growth:
        kw = g["keyword"]
        if g["status"] == "no_data":
            rows += f"""
            <div class="grow-card">
              <div class="grow-kw">{_esc(kw)}</div>
              <div class="grow-empty">아직 기록 없음</div>
            </div>"""
            continue

        n = g["n_records"]
        latest = g["latest"]
        imp_spark = _sparkline(g["points"], "impressions", "#4285f4")
        pos_spark = _sparkline(g["points"], "gsc_position", "#5e35b1")

        if g["status"] == "started":
            trend_note = '<span class="grow-note">첫 기록 — 다음 실행부터 변화 추적됩니다</span>'
        else:
            imp_arrow = "▲" if g["imp_delta"] > 0 else ("▼" if g["imp_delta"] < 0 else "—")
            imp_cls = "up" if g["imp_delta"] > 0 else ("down" if g["imp_delta"] < 0 else "flat")
            pos_txt = ""
            if g["pos_delta"] is not None:
                p_arrow = "▲" if g["pos_delta"] > 0 else ("▼" if g["pos_delta"] < 0 else "—")
                p_cls = "up" if g["pos_delta"] > 0 else ("down" if g["pos_delta"] < 0 else "flat")
                pos_txt = f'<span class="grow-chg {p_cls}">순위 {p_arrow} {abs(g["pos_delta"])}</span>'
            trend_note = (f'<span class="grow-chg {imp_cls}">노출 {imp_arrow} {abs(g["imp_delta"])}</span>'
                          + pos_txt)

        serp_txt = f'{latest["serp_rank"]}위' if latest.get("serp_rank") else "10위 밖/미확인"

        rows += f"""
        <div class="grow-card">
          <div class="grow-top">
            <div class="grow-kw">{_esc(kw)}</div>
            <div class="grow-n">기록 {n}회</div>
          </div>
          <div class="grow-stats">
            <div class="grow-stat"><b>{_fmt(latest['impressions'])}</b><span>노출</span></div>
            <div class="grow-stat"><b>{latest['gsc_position'] or '—'}</b><span>GSC순위</span></div>
            <div class="grow-stat"><b>{serp_txt}</b><span>SERP순위</span></div>
          </div>
          <div class="grow-sparks">
            <div class="grow-spark-block">{imp_spark}<span>노출 추이</span></div>
            <div class="grow-spark-block">{pos_spark}<span>순위 추이</span></div>
          </div>
          <div class="grow-trend">{trend_note}</div>
        </div>"""

    return f"""
    <section class="card">
      <div class="card-h"><h2>타겟 키워드 성장 추적</h2>
        <div class="card-meta">실행할 때마다 자동 기록 · 시간에 따른 변화</div></div>
      <div class="grow-grid">{rows}</div>
      <div class="card-note">python run_gsc.py를 실행할 때마다 그날 기록이 쌓입니다.
      최소 2번 이상 실행(며칠 간격 권장)해야 추세가 보입니다. 노출은 늘고 순위는 낮아지는(1위에 가까워지는) 게 좋은 신호입니다.</div>
    </section>"""


def _serp_panel(serp, my_domain):
    """키워드별 실제 SERP 순위 — 내 위치 vs 경쟁사."""
    if not serp or not serp.get("items"):
        return ""
    items = serp["items"]
    basis_label = serp.get("basis_label", "기회 키워드 기준")

    no_key = all(it["source"] == "NO_KEY" for it in items)
    if no_key:
        return f"""
        <section class="card">
          <div class="card-h"><h2>키워드별 실제 순위 (SERP)</h2></div>
          <div class="banner" style="margin:0">
            <strong>SerpApi 키가 설정되지 않았습니다.</strong>
            Render 환경변수의 SERPAPI_KEY를 설정하면 이 키워드들의 실제 구글 검색결과 순위와
            경쟁사 도메인이 표시됩니다. (월 250회 무료)
          </div>
        </section>"""

    rows = ""
    for it in items:
        kw = it["keyword"]
        src = it["source"]
        src_badge = {
            "LIVE": '<span class="src-b src-live">실시간</span>',
            "CACHE": '<span class="src-b src-cache">캐시(7일내)</span>',
        }.get(src, f'<span class="src-b src-warn">{_esc(src)}</span>')

        if it["my_rank"]:
            my_badge = f'<span class="myrank-yes">{it["my_rank"]}위</span>'
        elif it["results"]:
            my_badge = '<span class="myrank-no">10위 밖</span>'
        else:
            my_badge = '<span class="myrank-na">—</span>'

        comp_list = ""
        for c in it["competitors"][:5]:
            mine = " comp-me" if my_domain in c["domain"] else ""
            comp_list += f'<span class="comp-chip{mine}">{c["rank"]}. {_esc(c["domain"])}</span>'
        if not comp_list:
            comp_list = '<span class="comp-empty">데이터 없음</span>'

        rows += f"""
        <tr>
          <td><span class="k-query">{_esc(kw)}</span> {src_badge}</td>
          <td class="num">{my_badge}</td>
          <td class="comp-cell">{comp_list}</td>
        </tr>"""

    quota = serp.get("quota_note", "")
    return f"""
    <section class="card">
      <div class="card-h"><h2>키워드별 실제 순위 (SERP)</h2>
        <div class="card-meta">Google 실검색결과 · {_esc(basis_label)}</div></div>
      <table class="ptbl serp-tbl">
        <thead><tr><th>검색어</th><th class="num">내 순위</th><th>상위 결과 (1~10위)</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
      <div class="card-note">{_esc(quota)} · 같은 키워드는 7일간 캐시되어 쿼터를 아낍니다.
      '내 순위'가 비어있으면 10위 안에 없다는 뜻입니다.</div>
    </section>"""


def render_gsc(gsc, out_path, onpage=None, insights=None, competitors=None, prescription=None, serp=None, related=None, related_terms=None, growth=None):
    ts = gsc["timeseries"]
    totals = gsc["totals"]
    dr = gsc["date_range"]
    is_mock = gsc.get("source") == "MOCK"

    # 색인 요약
    idx = gsc.get("index_status", [])
    n_indexed = sum(1 for r in idx if r["verdict"] == "PASS")
    n_notidx = sum(1 for r in idx if r["verdict"] in ("FAIL", "NEUTRAL"))

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    dom = gsc["site_url"].replace("sc-domain:", "").rstrip("/")

    mock_banner = ""
    if is_mock:
        mock_banner = """
        <div class="banner">
          <strong>데모 데이터</strong> — 아직 Search Console API에 연결되지 않았습니다.
          아래 숫자는 예시이며 실제 검색 데이터가 아닙니다. 인증을 붙이면 실데이터로 바뀝니다.
        </div>"""

    charts = f"""
      <div class="chart-block">
        <div class="chart-tag">클릭수</div>
        {_line_chart(ts, 'clicks', 'var(--accent)')}
      </div>
      <div class="chart-block">
        <div class="chart-tag">노출수</div>
        {_line_chart(ts, 'impressions', 'var(--ink)')}
      </div>"""

    tiles = (
        _metric_tile("총 클릭수", _fmt(totals["clicks"]), f"최근 {len(ts)}일", "var(--accent)", active=True) +
        _metric_tile("총 노출수", _fmt(totals["impressions"]), f"{dr['start']} ~ {dr['end']}", "var(--line)") +
        _metric_tile("평균 CTR", f"{totals['ctr']}%", "클릭÷노출", "var(--line)") +
        _metric_tile("평균 게재순위", totals["position"], "낮을수록 좋음", "var(--line)")
    )

    # 심화 인사이트 패널
    insight_html = ""
    if insights:
        insight_html = (
            _compare_panel(insights.get("compare")) +
            _buckets_panel(insights.get("buckets")) +
            _opportunity_panel(insights.get("opportunity")) +
            _ctr_panel(insights.get("ctr_fix"))
        )

    # SERP 순위 패널
    dom_for_serp = gsc["site_url"].replace("sc-domain:", "").rstrip("/")
    if serp:
        serp_html = _serp_panel(serp, dom_for_serp)
    else:
        serp_html = """
        <section class="card">
          <div class="card-h"><h2>키워드별 실제 순위 (SERP)</h2></div>
          <div class="card-note">지금 기간엔 11~20위 기회 키워드나 CTR개선 후보가 없어 조회할 대상이 없습니다.
          이는 나쁜 신호가 아니라 그 구간에 해당하는 실검색어가 적다는 뜻입니다.</div>
        </section>"""

    # 경쟁사 비교 패널
    competitor_html = _competitor_panel(competitors) if competitors else ""

    # 온페이지 이슈 요약 (선택적으로 함께 표시)
    onpage_panel = ""
    if onpage and onpage.get("pages"):
        pages = onpage["pages"]
        n_err = sum(1 for p in pages for s, _ in p.get("issues", []) if s == "error")
        n_warn = sum(1 for p in pages for s, _ in p.get("issues", []) if s == "warn")
        rows = ""
        for p in sorted(pages, key=lambda x: -len(x.get("issues", [])))[:8]:
            badges = ""
            for sev, msg in p.get("issues", [])[:3]:
                bc = "b-err" if sev == "error" else "b-warn"
                badges += f'<span class="badge {bc}">{_esc(msg)}</span>'
            if not badges:
                badges = '<span class="badge b-ok">양호</span>'
            u = p["url"].replace("https://", "").replace("http://", "")
            rows += f'<tr><td><span class="k-url">{_esc(u[:48])}</span></td><td>{badges}</td></tr>'
        onpage_panel = f"""
        <section class="card">
          <div class="card-h">
            <h2>온페이지 기술 점검</h2>
            <div class="card-meta">에러 {n_err} · 경고 {n_warn} · {len(pages)}p 크롤</div>
          </div>
          <table class="ptbl"><thead><tr><th>URL</th><th>이슈</th></tr></thead>
          <tbody>{rows}</tbody></table>
          <div class="card-note">이 항목은 검색엔진 데이터가 아니라 사이트 HTML을 직접 크롤해 측정한 자체 진단입니다.</div>
        </section>"""

    doc = f"""<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>검색 성과 대시보드 · {_esc(dom)}</title>
<style>{CSS}</style>
</head><body>
<div class="app">

  <header class="topbar">
    <div class="brand">
      <div class="logo"></div>
      <div>
        <div class="brand-title">검색 성과 대시보드</div>
        <div class="brand-sub">{_esc(dom)} · 업데이트 {now}</div>
      </div>
    </div>
    <div class="src-chip {'chip-mock' if is_mock else 'chip-live'}">
      {'DEMO' if is_mock else 'LIVE · GSC API'}
    </div>
  </header>

  {mock_banner}

  <section class="tiles">{tiles}</section>

  {_prescription_panel(prescription) if prescription else ""}

  <section class="card chart-card">
    <div class="card-h"><h2>기간별 추이</h2>
      <div class="card-meta">{dr['start']} ~ {dr['end']} · 검색 유형: 웹</div></div>
    <div class="charts">{charts}</div>
  </section>

  <div class="two-col">
    <section class="card">
      <div class="card-h"><h2>인기 검색어</h2><div class="card-meta">Top {len(gsc['top_queries'])}</div></div>
      {_perf_table(gsc['top_queries'], '검색어')}
    </section>
    <section class="card">
      <div class="card-h"><h2>인기 페이지</h2><div class="card-meta">Top {len(gsc['top_pages'])}</div></div>
      {_perf_table(gsc['top_pages'], '페이지', is_url=True)}
    </section>
  </div>

  {_related_panel(related, related_terms or [])}

  {insight_html}

  <section class="card">
    <div class="card-h"><h2>색인 상태</h2>
      <div class="card-meta">색인됨 {n_indexed} · 미색인 {n_notidx} · 표본 {len(idx)}건</div></div>
    {_index_table(idx)}
    <div class="card-note">URL Inspection API 기준. 전체 사이트가 아니라 지정한 URL 표본에 대한 결과입니다.</div>
  </section>

  {serp_html}

  {_growth_panel(growth) if growth else ""}

  {competitor_html}

  {onpage_panel}

  <footer class="foot">
    검색 성과·색인 데이터는 Google Search Console API에서 가져온 <b>공식 데이터</b>입니다 ·
    데이터는 2~3일 지연되며, 고volume 쿼리는 Google이 샘플링·익명화할 수 있습니다 ·
    온페이지 점검은 자체 크롤 기반 진단입니다
  </footer>
</div>
</body></html>"""

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    return out_path


CSS = """
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css');

:root{
  --bg:#FAFAF9; --card:#FAFAF9; --line:#E4E4E1; --line2:#ECECE9;
  --ink:#14161A; --dim:#5B5F66; --dim2:#9A9DA3;
  --accent:#1E5E46;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:'Pretendard','Geist',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  -webkit-font-smoothing:antialiased;font-size:14px;line-height:1.6;
  word-break:keep-all;overflow-wrap:break-word}
.app{max-width:1080px;margin:0 auto;padding:40px 24px 64px}

/* topbar */
.topbar{display:flex;align-items:center;justify-content:space-between;
  padding:0 0 24px;border-bottom:1px solid var(--line);margin-bottom:32px}
.brand{display:flex;align-items:center;gap:12px}
.logo{width:8px;height:8px;border-radius:50%;background:var(--accent);flex:0 0 auto}
.brand-title{font-size:18px;font-weight:500;letter-spacing:-.015em}
.brand-sub{font-size:12.5px;color:var(--dim);margin-top:2px;font-weight:400}
.src-chip{font-size:11px;font-weight:500;letter-spacing:.06em;padding:5px 12px;
  border:1px solid var(--line);border-radius:3px;color:var(--dim)}
.chip-live{color:var(--accent);border-color:var(--accent)}
.chip-mock{color:var(--dim2)}

/* banner */
.banner{border-left:2px solid var(--ink);padding:4px 0 4px 16px;
  font-size:13px;color:var(--dim);margin-bottom:28px;line-height:1.6}
.banner strong{color:var(--ink);font-weight:500}

/* tiles */
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:0;margin-bottom:0;
  border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.tile{padding:20px 22px;position:relative;border-right:1px solid var(--line)}
.tile:last-child{border-right:none}
.tile.active{box-shadow:inset 0 2px 0 0 var(--accent)}
.tile-label{font-size:12.5px;color:var(--dim);font-weight:400}
.tile-value{font-size:28px;font-weight:500;letter-spacing:-.02em;margin:8px 0 3px;
  font-variant-numeric:tabular-nums;font-family:'Geist','Pretendard',sans-serif}
.tile-sub{font-size:11.5px;color:var(--dim2)}
section.card + section.card, section.card + .rx-panel { margin-top: 0; }

/* cards → framed sections, no shadow, no color fill */
.card{background:transparent;border:1px solid var(--line);border-radius:2px;
  padding:28px 28px;margin-top:24px}
.card-h{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:20px;
  flex-wrap:wrap;gap:6px}
.card-h h2{font-size:16px;font-weight:500;margin:0;letter-spacing:-.01em;
  font-family:'Pretendard',sans-serif}
.card-meta{font-size:12px;color:var(--dim2)}
.card-note{margin-top:20px;padding-top:16px;border-top:1px solid var(--line2);
  font-size:12px;color:var(--dim2);line-height:1.6}
.card-note b{color:var(--dim);font-weight:500}

/* charts */
.charts{display:grid;grid-template-columns:1fr 1fr;gap:32px}
.chart-block{position:relative}
.chart-tag{font-size:11.5px;font-weight:500;color:var(--dim);margin-bottom:8px;
  letter-spacing:.02em}
.linechart{width:100%;height:180px;display:block}
.linechart .ax{fill:var(--dim2);font-size:10px;text-anchor:middle}
.chart-empty{color:var(--dim2);padding:40px;text-align:center}

/* two col */
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:24px}
.two-col .card{margin-top:24px}

/* perf tables */
.ptbl{width:100%;border-collapse:collapse;font-size:13px}
.ptbl th{text-align:left;color:var(--dim2);font-weight:500;font-size:11px;
  padding:0 10px 10px;border-bottom:1px solid var(--line);letter-spacing:.03em}
.ptbl th.num,.ptbl td.num{text-align:right;font-variant-numeric:tabular-nums}
.ptbl td{padding:11px 10px;border-bottom:1px solid var(--line2)}
.ptbl tbody tr:last-child td{border-bottom:none}
.ptbl tbody tr:hover{background:rgba(20,22,26,.02)}
.k-query{font-weight:400}
.k-url{font-family:ui-monospace,'SF Mono',Menlo,monospace;font-size:12px;color:var(--accent)}
.pct{color:var(--dim2);font-size:11px;margin-left:1px}
.pos{color:var(--dim)}

/* verdict — 색 대신 채움/윤곽으로 상태 구분 */
.verdict{font-size:12px;font-weight:400;color:var(--dim);white-space:nowrap;
  display:inline-flex;align-items:center;gap:6px}
.verdict::before{content:"";width:6px;height:6px;border-radius:50%;
  border:1.5px solid var(--dim2);display:inline-block}
.v-pass{color:var(--ink)}
.v-pass::before{background:var(--accent);border-color:var(--accent)}
.v-neutral::before{border-color:var(--dim2)}
.v-fail::before{border-color:var(--ink);border-style:solid}
.cov{color:var(--dim);font-size:12px}
.crawl{color:var(--dim2);font-size:12px;font-variant-numeric:tabular-nums}

/* onpage badges — 색 배경 제거, 텍스트 라벨화 */
.badge{display:inline-block;font-size:11.5px;padding:0;margin:1px 12px 1px 0;
  white-space:nowrap;color:var(--dim);border-bottom:1px dotted var(--line)}
.b-err{color:var(--ink);font-weight:500}
.b-warn{color:var(--dim)}
.b-ok{color:var(--dim2)}

/* 기간 비교 */
.cmp-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:0;
  border-top:1px solid var(--line)}
.cmp-tile{border-right:1px solid var(--line);padding:18px 20px 4px}
.cmp-tile:last-child{border-right:none}
.cmp-label{font-size:12px;color:var(--dim);font-weight:400}
.cmp-cur{font-size:22px;font-weight:500;margin:6px 0 4px;font-variant-numeric:tabular-nums;
  font-family:'Geist','Pretendard',sans-serif}
.cmp-chg{font-size:12.5px;font-weight:500;display:flex;flex-direction:column;gap:2px}
.cmp-chg.up{color:var(--accent)}
.cmp-chg.down{color:var(--dim)}
.cmp-chg.flat{color:var(--dim2)}
.cmp-prev{font-size:11px;color:var(--dim2);font-weight:400}

/* 태그 */
.tag-hot,.tag-warn{font-size:11.5px;font-weight:400;color:var(--dim);
  padding:0;margin-left:8px;border-bottom:1px dotted var(--line)}
.gain{color:var(--accent);font-weight:500}
.low{color:var(--ink);font-weight:500}

/* 순위 구간 */
.bkts{display:flex;flex-direction:column;gap:14px}
.bkt-row{display:grid;grid-template-columns:72px 1fr auto;align-items:center;gap:14px}
.bkt-name{font-size:13px;font-weight:400;color:var(--dim)}
.bkt-track{height:6px;background:var(--line2);overflow:hidden}
.bkt-fill{height:100%;transition:width .7s}
.bkt-val{font-size:12.5px;color:var(--dim);white-space:nowrap;font-variant-numeric:tabular-nums}
.bkt-imp{color:var(--dim2)}

/* 경쟁사 비교 */
.cmp-scroll{overflow-x:auto}
.ctbl{width:100%;border-collapse:collapse;font-size:13px;min-width:480px}
.ctbl th{padding:10px 12px;border-bottom:1px solid var(--line);text-align:center;
  font-size:12px;font-weight:500;vertical-align:bottom;color:var(--dim)}
.ctbl th.metric-name{text-align:left}
.ctbl td{padding:11px 12px;border-bottom:1px solid var(--line2);text-align:center;
  font-variant-numeric:tabular-nums}
.ctbl td.metric-name{text-align:left;color:var(--dim);font-weight:400;font-size:12.5px}
.ctbl tbody tr:last-child td{border-bottom:none}
.site-dom{font-family:ui-monospace,'SF Mono',Menlo,monospace;font-size:11.5px;
  color:var(--ink);margin-top:4px;word-break:break-all;max-width:130px}
.me-badge{font-size:10px;font-weight:500;color:var(--accent);border-bottom:1px solid var(--accent)}
.col-me{}
th.col-me{box-shadow:inset 0 -2px 0 0 var(--accent)}
.cell-win{color:var(--accent);font-weight:500}
.cell-win.col-me{color:var(--accent);font-weight:600}
.cell-na{color:var(--dim2)}

/* 처방 레이어 */
.rx-panel{}
.rx-health{display:flex;align-items:center;gap:20px;padding:0 0 20px;
  border-bottom:1px solid var(--line);margin-bottom:24px}
.rx-dot{width:9px;height:9px;border-radius:50%;border:1.5px solid var(--dim2);flex:0 0 auto}
.rx-dot.good{background:var(--accent);border-color:var(--accent)}
.rx-dot.caution{border-color:var(--ink)}
.rx-dot.warn{background:var(--ink);border-color:var(--ink)}
.rx-health-body{flex:1}
.rx-health-label{font-size:16px;font-weight:500;font-family:'Pretendard',sans-serif}
.rx-health-msg{font-size:13px;color:var(--dim);margin-top:3px}
.rx-counts{display:flex;gap:24px}
.rx-count{text-align:center}
.rx-count b{display:block;font-size:18px;font-weight:500;font-variant-numeric:tabular-nums;
  font-family:'Geist','Pretendard',sans-serif}
.rx-count span{font-size:11px;color:var(--dim2)}

.rx-list{display:flex;flex-direction:column}
.rx-card{padding:22px 0;border-top:1px solid var(--line2)}
.rx-card:first-child{border-top:none;padding-top:0}
.rx-card-top{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:10px}
.rx-num{font-family:'Geist','Pretendard',sans-serif;font-size:13px;font-weight:500;
  color:var(--dim2)}
.rx-cat{font-size:11.5px;font-weight:400;color:var(--dim)}
.cat-tech{color:var(--ink)}
.cat-content{color:var(--dim)}
.rx-tag{font-size:11px;font-weight:400;color:var(--dim2)}
.rx-tag.imp3{color:var(--ink);font-weight:500}
.rx-tag.imp2{color:var(--dim)}
.rx-tag.eff{color:var(--dim2)}
.rx-ev{margin-left:auto;font-size:11.5px;color:var(--dim2);font-variant-numeric:tabular-nums}
.rx-title{font-size:16px;font-weight:500;margin-bottom:9px;letter-spacing:-.01em;
  font-family:'Pretendard',sans-serif}
.rx-why,.rx-how{font-size:13.5px;color:var(--dim);line-height:1.65;margin-top:4px}
.rx-why b,.rx-how b{color:var(--ink);font-weight:500;margin-right:6px;font-size:12px}

/* SERP 순위 */
.serp-tbl td{vertical-align:top}
.src-b{font-size:10px;font-weight:400;padding:0;margin-left:8px;color:var(--dim2)}
.src-live{color:var(--accent)}
.src-cache{color:var(--dim2)}
.src-warn{color:var(--dim)}
.myrank-yes{color:var(--accent);font-weight:500;font-size:12.5px}
.myrank-no{color:var(--dim);font-weight:400;font-size:12px}
.myrank-na{color:var(--dim2)}
.comp-cell{display:flex;flex-wrap:wrap;gap:12px}
.comp-chip{font-size:11.5px;color:var(--dim);white-space:nowrap;
  font-family:ui-monospace,'SF Mono',Menlo,monospace}
.comp-chip.comp-me{color:var(--accent);font-weight:500}
.comp-empty{color:var(--dim2);font-size:12px}

/* 타겟 키워드 성장 추적 */
.grow-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:0;
  border-top:1px solid var(--line);border-left:1px solid var(--line)}
.grow-card{border-right:1px solid var(--line);border-bottom:1px solid var(--line);padding:20px 22px}
.grow-top{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:14px}
.grow-kw{font-weight:500;font-size:14px;letter-spacing:-.005em}
.grow-n{font-size:11px;color:var(--dim2)}
.grow-empty{color:var(--dim2);font-size:12.5px;padding:8px 0}
.grow-stats{display:flex;gap:18px;margin-bottom:14px}
.grow-stat{text-align:left}
.grow-stat b{display:block;font-size:16px;font-weight:500;font-variant-numeric:tabular-nums;
  font-family:'Geist','Pretendard',sans-serif}
.grow-stat span{font-size:10px;color:var(--dim2)}
.grow-sparks{display:flex;gap:14px;margin-bottom:10px}
.grow-spark-block{text-align:left;flex:1}
.grow-spark-block span{display:block;font-size:10px;color:var(--dim2);margin-top:4px}
.grow-trend{font-size:12px;display:flex;gap:12px;flex-wrap:wrap}
.grow-chg{font-weight:500}
.grow-chg.up{color:var(--accent)}
.grow-chg.down{color:var(--dim)}
.grow-chg.flat{color:var(--dim2)}
.grow-note{color:var(--dim2);font-style:normal}

.foot{margin-top:40px;padding-top:20px;border-top:1px solid var(--line);
  font-size:11.5px;color:var(--dim2);line-height:1.7;text-align:left}
.foot b{color:var(--dim)}

@media(max-width:820px){
  .tiles{grid-template-columns:repeat(2,1fr)}
  .tile:nth-child(2){border-right:none}
  .charts,.two-col,.grow-grid{grid-template-columns:1fr}
  .cmp-grid{grid-template-columns:repeat(2,1fr)}
  .cmp-tile:nth-child(2){border-right:none}
}
"""
