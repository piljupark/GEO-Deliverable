"""
사이드바 셸.
분석 결과 페이지(ANALYZE_RESULT_PAGE 등)가 이미 완결된 HTML 문서라, 그 CSS를 건드리지 않고
iframe으로 감싸는 방식으로 좌측 네비게이션 구조를 얹는다.
"""

import html


def _esc(s):
    return html.escape(str(s))


NAV_ITEMS = [
    ("진단", [
        ("분석 실행", "/", "analyze"),
    ]),
    ("설정", [
        ("내 사이트", "/settings/site", "settings-site"),
        ("경쟁사", "/settings/competitors", "settings-competitors"),
        ("프롬프트 목록", "/settings/prompts", "settings-prompts"),
        ("로그아웃", "/logout", "logout"),
    ]),
]


def sidebar_shell(active_key, content_url, title=""):
    sections_html = ""
    for section_name, items in NAV_ITEMS:
        items_html = ""
        for label, href, key in items:
            active_cls = " active" if key == active_key else ""
            items_html += f'<a class="nav-item{active_cls}" href="{href}">{_esc(label)}</a>'
        sections_html += f"""
        <div class="nav-section">
          <div class="nav-heading">{_esc(section_name)}</div>
          {items_html}
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<style>{SHELL_CSS}</style>
</head><body>
<div class="shell">
  <aside class="sidebar">
    {sections_html}
  </aside>
  <main class="content">
    <iframe src="{content_url}" title="{_esc(title)}"></iframe>
  </main>
</div>
</body></html>"""


SHELL_CSS = """
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css');
:root{--bg:#FAFAF9;--line:#E4E4E1;--ink:#14161A;--dim:#5B5F66;--dim2:#9A9DA3;--accent:#1E5E46}
*{box-sizing:border-box}
html,body{height:100%;margin:0}
body{font-family:'Pretendard',-apple-system,sans-serif;font-size:14px;color:var(--ink)}
.shell{display:flex;height:100%}

.sidebar{width:220px;flex:0 0 auto;background:var(--bg);border-right:1px solid var(--line);
  padding:24px 16px;box-sizing:border-box;overflow-y:auto}

.nav-section{margin-bottom:22px}
.nav-heading{font-size:11px;font-weight:500;color:var(--dim2);letter-spacing:.06em;
  padding:0 8px;margin-bottom:6px}
.nav-item{display:block;padding:8px 8px;border-radius:3px;color:var(--dim);
  text-decoration:none;font-size:13.5px;margin-bottom:2px}
.nav-item:hover{background:#F0F0EE;color:var(--ink)}
.nav-item.active{background:var(--ink);color:var(--bg)}

.content{flex:1;min-width:0;height:100%}
.content iframe{width:100%;height:100%;border:none;display:block}

@media(max-width:760px){
  .shell{flex-direction:column}
  .sidebar{width:100%;border-right:none;border-bottom:1px solid var(--line);
    display:flex;align-items:center;gap:16px;padding:12px 16px;overflow-x:auto}
  .nav-section{margin-bottom:0;display:flex;align-items:center;gap:4px;white-space:nowrap}
  .nav-heading{display:none}
  .content{height:calc(100vh - 60px)}
}
"""
