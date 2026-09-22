"""
GA4 + 광고 플랫폼 트래픽 리포트 렌더러.
기존 PDF 수기 리포트와 동일한 구성(소스/매체 표, 오전/오후 추이 차트, 광고 플랫폼 표)을
자동 생성. 디자인은 render_gsc.py와 같은 절제된 타이포그래피 체계를 공유.
"""

import html
from datetime import datetime


def _esc(s):
    return html.escape(str(s if s is not None else ""))


def _fmt(n):
    try:
        return f"{int(round(n)):,}"
    except Exception:
        return str(n)


def _won(n):
    return f"₩{_fmt(n)}"


def _source_table(rows):
    body = ""
    for r in rows:
        body += f"""
        <tr>
          <td><span class="k-query">{_esc(r['source_medium'])}</span></td>
          <td class="num">{_fmt(r['views'])}</td>
          <td class="num">{_fmt(r['sessions'])}</td>
          <td class="num">{_fmt(r['active_users'])}</td>
          <td class="num">{r['avg_engagement_sec']}초</td>
          <td class="num">{_fmt(r['events'])}</td>
        </tr>"""
    return f"""
    <table class="ptbl">
      <thead><tr>
        <th>소스/매체</th><th class="num">조회수</th><th class="num">세션수</th>
        <th class="num">활성사용자</th><th class="num">평균 참여시간</th><th class="num">이벤트 수</th>
      </tr></thead>
      <tbody>{body}</tbody>
    </table>"""


def _traffic_chart(timeline, width=980, height=260):
    """오전/오후 구간별 조회수(막대) — PDF의 트래픽 차트와 동일한 구조."""
    if not timeline:
        return "<div class='chart-empty'>데이터 없음</div>"

    n = len(timeline)
    vmax = max((t["views"] for t in timeline), default=1) or 1
    pad_l, pad_r, pad_t, pad_b = 10, 10, 16, 40
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    bw = plot_w / n * 0.55
    gap = plot_w / n

    bars = ""
    labels = ""
    for i, t in enumerate(timeline):
        x = pad_l + i * gap + (gap - bw) / 2
        h = (t["views"] / vmax) * plot_h
        y = pad_t + (plot_h - h)
        bars += f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{h:.1f}" fill="var(--ink)" opacity="{"1" if i == n-1 else "0.55"}"/>'
        lx = pad_l + i * gap + gap / 2
        labels += f'<text x="{lx:.1f}" y="{height-24}" class="ax">{_esc(t["date"])}</text>'
        labels += f'<text x="{lx:.1f}" y="{height-10}" class="ax2">{_esc(t["period"])}</text>'
        vtxt = _fmt(t["views"])
        labels += f'<text x="{lx:.1f}" y="{y-6:.1f}" class="axv">{vtxt}</text>'

    return f"""
    <svg viewBox="0 0 {width} {height}" class="barchart" preserveAspectRatio="xMidYMid meet">
      {bars}{labels}
      <line x1="{pad_l}" y1="{pad_t+plot_h}" x2="{width-pad_r}" y2="{pad_t+plot_h}" stroke="var(--line)" stroke-width="1"/>
    </svg>"""


def _ad_row(label, d, pending=False):
    if pending:
        return f"""
        <tr class="row-pending">
          <td><span class="k-query">{_esc(label)}</span></td>
          <td class="num">—</td><td class="num">—</td><td class="num">—</td>
          <td class="num">—</td><td class="num">—</td><td class="num">—</td>
          <td class="pending-note">연동 대기 (API 승인 필요)</td>
        </tr>"""
    src = (d or {}).get("source", "")
    if src.startswith("ERROR"):
        detail = d.get("detail", src)
        return f"""
        <tr class="row-error">
          <td><span class="k-query">{_esc(label)}</span></td>
          <td class="num">—</td><td class="num">—</td><td class="num">—</td>
          <td class="num">—</td><td class="num">—</td><td class="num">—</td>
          <td class="error-note" title="{_esc(detail)}">연동 실패 — {_esc(src.replace('ERROR:',''))}</td>
        </tr>"""
    return f"""
    <tr>
      <td><span class="k-query">{_esc(label)}</span></td>
      <td class="num">{_fmt(d['impressions'])}</td>
      <td class="num">{_fmt(d['clicks'])}</td>
      <td class="num">{_won(d['cost'])}</td>
      <td class="num">{_won(d['cpc'])}</td>
      <td class="num">{_won(d['cpm'])}</td>
      <td class="num">{d['ctr']}%</td>
      <td></td>
    </tr>"""


def render_ads_report(ga4, naver, out_path, google_ads=None, meta_ads=None,
                       google_enabled=False, meta_enabled=False, notes=None):
    now = datetime.now()
    date_label = f"{now.month}월 {now.day}일"
    time_label = now.strftime("%H:%M")

    is_ga4_mock = ga4.get("source") == "MOCK"
    is_naver_mock = naver.get("source") == "MOCK"
    any_mock = is_ga4_mock or is_naver_mock

    mock_banner = ""
    if any_mock:
        parts = []
        if is_ga4_mock:
            parts.append("GA4")
        if is_naver_mock:
            parts.append("네이버 검색광고")
        mock_banner = f"""
        <div class="banner"><strong>데모 데이터</strong> — {', '.join(parts)}가 아직 실데이터에
        연결되지 않았습니다. Render 환경변수에 인증 정보를 채우면 실데이터로 바뀝니다
        (자세한 방법은 DEPLOY.md 참고).</div>"""

    ad_rows = (
        _ad_row("google / searchad", google_ads, pending=not google_enabled) +
        _ad_row("Naver / searchad", naver) +
        _ad_row("google / displayad", google_ads, pending=not google_enabled) +
        _ad_row("Meta / ad", meta_ads, pending=not meta_enabled)
    )

    notes_html = ""
    if notes:
        items = "".join(f"<li>{_esc(n)}</li>" for n in notes)
        notes_html = f"""
        <section class="card">
          <div class="card-h"><h2>메모</h2></div>
          <ul class="notes-list">{items}</ul>
        </section>"""

    doc = f"""<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>트래픽 리포트 · {_esc(date_label)} {_esc(time_label)}</title>
<style>{CSS}</style>
</head><body>
<div class="app" id="report-root">

  <header class="topbar">
    <div class="brand">
      <div class="logo"></div>
      <div>
        <div class="brand-title">트래픽 리포트</div>
        <div class="brand-sub">{_esc(date_label)} 오후 {_esc(time_label)}</div>
      </div>
    </div>
    <button class="print-btn no-print" onclick="window.print()">PDF로 저장</button>
  </header>

  {mock_banner}

  <section class="card">
    <div class="card-h"><h2>소스/매체별 트래픽</h2>
      <div class="card-meta">GA4 · 속성 {ga4.get('property_id','')}</div></div>
    {_source_table(ga4.get('source_rows', []))}
  </section>

  <section class="card">
    <div class="card-h"><h2>트래픽 추이</h2>
      <div class="card-meta">오전/오후 구간별 조회수</div></div>
    {_traffic_chart(ga4.get('timeline', []))}
  </section>

  <section class="card">
    <div class="card-h"><h2>광고 플랫폼 실적</h2>
      <div class="card-meta">노출 · 클릭 · 비용 · CPC · CPM · CTR</div></div>
    <table class="ptbl adtbl">
      <thead><tr>
        <th>구분</th><th class="num">노출</th><th class="num">클릭</th>
        <th class="num">비용</th><th class="num">CPC</th><th class="num">CPM</th>
        <th class="num">CTR</th><th></th>
      </tr></thead>
      <tbody>{ad_rows}</tbody>
    </table>
    <div class="card-note">Google Ads·Meta는 API 승인 전까지 자동 집계에서 제외됩니다.
    승인 완료 시 Render 환경변수에 인증 정보를 넣으면 자동으로 채워집니다.</div>
  </section>

  {notes_html}

  <footer class="foot">
    GA4 데이터는 Google Analytics 공식 API · 광고 데이터는 각 플랫폼 공식 API 기준 ·
    매일 09:00 / 16:00 자동 생성
  </footer>
</div>

</body></html>"""

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    return out_path


CSS = """
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css');

:root{
  --bg:#FAFAF9; --line:#E4E4E1; --line2:#ECECE9;
  --ink:#14161A; --dim:#5B5F66; --dim2:#9A9DA3;
  --accent:#1E5E46;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:'Pretendard','Geist',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  -webkit-font-smoothing:antialiased;font-size:14px;line-height:1.6;
  word-break:keep-all;overflow-wrap:break-word}
.app{max-width:1080px;margin:0 auto;padding:40px 24px 64px}

.topbar{display:flex;align-items:center;justify-content:space-between;
  padding:0 0 24px;border-bottom:1px solid var(--line);margin-bottom:32px}
.brand{display:flex;align-items:center;gap:12px}
.logo{width:8px;height:8px;border-radius:50%;background:var(--accent);flex:0 0 auto}
.brand-title{font-size:18px;font-weight:500;letter-spacing:-.015em}
.brand-sub{font-size:12.5px;color:var(--dim);margin-top:2px}
.print-btn{border:1px solid var(--ink);background:transparent;color:var(--ink);
  padding:8px 16px;font-size:13px;border-radius:2px;cursor:pointer;
  font-family:inherit}
.print-btn:hover{background:var(--ink);color:var(--bg)}

.banner{border-left:2px solid var(--ink);padding:4px 0 4px 16px;
  font-size:13px;color:var(--dim);margin-bottom:28px;line-height:1.6}
.banner strong{color:var(--ink);font-weight:500}

.card{background:transparent;border:1px solid var(--line);border-radius:2px;
  padding:28px 28px;margin-top:24px}
.card-h{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:20px;
  flex-wrap:wrap;gap:6px}
.card-h h2{font-size:16px;font-weight:500;margin:0;letter-spacing:-.01em}
.card-meta{font-size:12px;color:var(--dim2)}
.card-note{margin-top:20px;padding-top:16px;border-top:1px solid var(--line2);
  font-size:12px;color:var(--dim2);line-height:1.6}

.ptbl{width:100%;border-collapse:collapse;font-size:13px}
.ptbl th{text-align:left;color:var(--dim2);font-weight:500;font-size:11px;
  padding:0 10px 10px;border-bottom:1px solid var(--line);letter-spacing:.03em}
.ptbl th.num,.ptbl td.num{text-align:right;font-variant-numeric:tabular-nums}
.ptbl td{padding:11px 10px;border-bottom:1px solid var(--line2)}
.ptbl tbody tr:last-child td{border-bottom:none}
.k-query{font-weight:400}
.row-pending td{color:var(--dim2)}
.row-error td{color:var(--dim2)}
.pending-note{font-size:11px;color:var(--dim2);text-align:right;white-space:nowrap}
.error-note{font-size:11px;color:var(--ink);text-align:right;white-space:nowrap;
  border-bottom:1px dotted var(--ink)}

.barchart{width:100%;height:260px;display:block}
.barchart .ax{fill:var(--dim);font-size:11px;text-anchor:middle;font-weight:500}
.barchart .ax2{fill:var(--dim2);font-size:10px;text-anchor:middle}
.barchart .axv{fill:var(--dim);font-size:10px;text-anchor:middle;font-variant-numeric:tabular-nums}
.chart-empty{color:var(--dim2);padding:40px;text-align:center}

.notes-list{margin:0;padding-left:18px;font-size:13.5px;color:var(--dim);line-height:1.8}

.foot{margin-top:40px;padding-top:20px;border-top:1px solid var(--line);
  font-size:11.5px;color:var(--dim2);line-height:1.7}

@media print{
  .no-print{display:none}
  body{background:#fff}
  .app{padding:0;max-width:100%}
  .card{page-break-inside:avoid}
}

@media(max-width:820px){
  .app{padding:24px 16px 48px}
  .adtbl{font-size:11.5px}
}
"""
