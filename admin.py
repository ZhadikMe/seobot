#!/usr/bin/env python3
"""
admin.py — Web admin panel for SEO pipeline.
Run: python admin.py  →  http://localhost:8080
"""
import os, sys, uuid, queue, threading, tempfile, zipfile, io, shutil, logging, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, render_template_string, request, Response, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500 MB

GROQ_KEY     = os.environ.get('GROQ_API_KEY', '')
WOWAI_KEY    = os.environ.get('WOWAI_API_KEY', '')
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')

ALL_LANGS = ['ru','de','fr','es','it','pt','pl','nl','cs','ro','sv','tr','el','uk','ko','zh','ja','sk','fi','ar','hi']

LANG_META = {
    'ru': ('🇷🇺', 'Русский'),   'de': ('🇩🇪', 'Deutsch'),   'fr': ('🇫🇷', 'Français'),
    'es': ('🇪🇸', 'Español'),   'it': ('🇮🇹', 'Italiano'),  'pt': ('🇵🇹', 'Português'),
    'pl': ('🇵🇱', 'Polski'),    'nl': ('🇳🇱', 'Nederlands'), 'cs': ('🇨🇿', 'Čeština'),
    'ro': ('🇷🇴', 'Română'),    'sv': ('🇸🇪', 'Svenska'),   'tr': ('🇹🇷', 'Türkçe'),
    'el': ('🇬🇷', 'Ελληνικά'), 'uk': ('🇺🇦', 'Українська'),'ko': ('🇰🇷', '한국어'),
    'zh': ('🇨🇳', '中文'),      'ja': ('🇯🇵', '日本語'),    'sk': ('🇸🇰', 'Slovenčina'),
    'fi': ('🇫🇮', 'Suomi'),     'ar': ('🇸🇦', 'العربية'),  'hi': ('🇮🇳', 'हिन्दी'),
}

_jobs: dict = {}

# ── HTML ──────────────────────────────────────────────────────────────────────

LANG_PILLS_HTML = '\n'.join(
    f'<div class="lang-pill" data-lang="{code}" onclick="toggleLang(this)">'
    f'<span class="pill-flag">{flag}</span>'
    f'<span class="pill-name">{name}</span>'
    f'</div>'
    for code, (flag, name) in LANG_META.items()
)

HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SEO Pipeline</title>
<style>
:root {
  --bg:      #07090f;
  --s1:      #0e1420;
  --s2:      #131b2e;
  --s3:      #1a2540;
  --border:  #1e2d45;
  --b2:      #253350;
  --accent:  #6366f1;
  --a2:      #818cf8;
  --violet:  #8b5cf6;
  --green:   #22c55e;
  --yellow:  #f59e0b;
  --red:     #ef4444;
  --t1:      #e2e8f0;
  --t2:      #94a3b8;
  --t3:      #475569;
  --r:       12px;
}
*,*::before,*::after { box-sizing:border-box; margin:0; padding:0; }
html { scroll-behavior:smooth; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: var(--bg); color: var(--t1); min-height:100vh;
  background-image:
    radial-gradient(ellipse 80% 50% at 50% -10%, rgba(99,102,241,.13), transparent),
    radial-gradient(ellipse 50% 40% at 85% 85%, rgba(139,92,246,.08), transparent);
}
::-webkit-scrollbar { width:5px; height:5px; }
::-webkit-scrollbar-thumb { background:var(--border); border-radius:99px; }

/* ── Header ── */
header {
  border-bottom:1px solid var(--border); backdrop-filter:blur(16px);
  background:rgba(7,9,15,.85); position:sticky; top:0; z-index:100;
}
.hdr { max-width:980px; margin:0 auto; padding:0 24px; height:58px; display:flex; align-items:center; gap:10px; }
.logo {
  width:34px; height:34px; border-radius:9px; flex-shrink:0; font-size:16px;
  background:linear-gradient(135deg,var(--accent),var(--violet));
  display:flex; align-items:center; justify-content:center;
  box-shadow:0 0 18px rgba(99,102,241,.35);
}
.hdr-title { font-size:15px; font-weight:700; color:var(--t1); }
.hdr-badge {
  padding:2px 9px; border-radius:99px; font-size:11px; font-weight:700; letter-spacing:.05em;
  background:rgba(99,102,241,.14); border:1px solid rgba(99,102,241,.28); color:var(--a2);
}
.hdr-sep { flex:1; }
.hdr-dot { width:7px; height:7px; border-radius:50%; background:var(--green); box-shadow:0 0 7px var(--green); animation:blink 2s infinite; }
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:.35} }
.hdr-online { font-size:12px; color:var(--t3); }

/* ── Page ── */
.page { max-width:980px; margin:0 auto; padding:36px 24px 80px; }

/* ── Section label ── */
.slabel {
  font-size:10.5px; font-weight:800; letter-spacing:.1em; text-transform:uppercase; color:var(--t3);
  margin-bottom:12px; display:flex; align-items:center; gap:10px;
}
.slabel::after { content:''; flex:1; height:1px; background:var(--border); }

/* ── Card ── */
.card {
  background:var(--s1); border:1px solid var(--border); border-radius:var(--r);
  padding:22px 24px; margin-bottom:14px; position:relative;
  transition:border-color .2s;
}
.card::before {
  content:''; position:absolute; inset:0;
  background:linear-gradient(135deg,rgba(99,102,241,.03),transparent 55%); pointer-events:none;
}

/* ── Field ── */
.field { margin-bottom:16px; }
.field:last-child { margin-bottom:0; }
.flabel {
  display:block; font-size:11px; font-weight:700; color:var(--t3);
  margin-bottom:7px; text-transform:uppercase; letter-spacing:.07em;
}
.fhint { font-size:12px; color:var(--t3); margin-top:5px; line-height:1.5; }
.fhint a { color:var(--a2); }

input[type=text], input[type=password] {
  width:100%; padding:10px 14px; background:var(--s2); border:1px solid var(--border);
  border-radius:8px; color:var(--t1); font-size:14px; outline:none;
  transition:border-color .15s, box-shadow .15s;
}
input::placeholder { color:var(--t3); }
input:focus { border-color:var(--accent); box-shadow:0 0 0 3px rgba(99,102,241,.12); }
.row2 { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
@media(max-width:600px) { .row2 { grid-template-columns:1fr; } }

/* ── Source tabs ── */
.src-tabs { display:flex; gap:6px; margin-bottom:18px; }
.src-tab {
  flex:1; padding:10px 8px; border:1px solid var(--border); border-radius:9px;
  background:var(--s2); color:var(--t2); font-size:13px; font-weight:500;
  cursor:pointer; text-align:center; transition:all .15s; user-select:none;
}
.src-tab .t-icon { font-size:18px; display:block; margin-bottom:3px; }
.src-tab .t-name { font-size:12px; display:block; }
.src-tab:hover { border-color:var(--b2); color:var(--t1); }
.src-tab.active { background:rgba(99,102,241,.14); border-color:rgba(99,102,241,.4); color:var(--a2); font-weight:700; }
.src-panel { display:none; }
.src-panel.active { display:block; }

/* ── Upload zone ── */
.upload-zone {
  border:1.5px dashed var(--b2); border-radius:10px; padding:32px 20px;
  text-align:center; cursor:pointer; transition:all .2s; position:relative; background:var(--s2);
}
.upload-zone:hover,.upload-zone.drag { border-color:var(--accent); background:rgba(99,102,241,.05); }
.upload-zone.has-file { border-color:rgba(34,197,94,.45); border-style:solid; background:rgba(34,197,94,.04); }
.upload-zone input[type=file] { position:absolute; inset:0; opacity:0; cursor:pointer; width:100%; height:100%; }
.up-icon { font-size:32px; margin-bottom:10px; transition:transform .2s; }
.upload-zone:hover .up-icon { transform:translateY(-2px); }
.up-title { font-size:14px; font-weight:600; color:var(--t1); margin-bottom:3px; }
.up-sub { font-size:12px; color:var(--t3); }
.up-fname { display:none; margin-top:10px; padding:5px 12px; border-radius:6px; background:rgba(34,197,94,.12); border:1px solid rgba(34,197,94,.22); color:#4ade80; font-size:13px; font-weight:500; }
.upload-zone.has-file .up-fname { display:inline-block; }

/* ── Mode tabs ── */
.mode-tabs { display:flex; gap:6px; }
.mode-tab {
  flex:1; padding:10px 8px; border:1px solid var(--border); border-radius:9px;
  background:var(--s2); color:var(--t2); font-size:13px; font-weight:500;
  cursor:pointer; text-align:center; transition:all .15s; user-select:none;
  position:relative;
}
.mode-tab .m-icon { font-size:18px; display:block; margin-bottom:3px; }
.mode-tab .m-name { font-size:12px; display:block; font-weight:600; }
.mode-tab:hover { border-color:var(--b2); color:var(--t1); }
.mode-tab.active { background:rgba(99,102,241,.14); border-color:rgba(99,102,241,.4); color:var(--a2); }
/* Tooltip */
.mode-tab::after {
  content: attr(data-tip);
  position:absolute; bottom:calc(100% + 8px); left:50%; transform:translateX(-50%);
  background:#0a1628; border:1px solid var(--b2); color:var(--t2);
  font-size:12px; line-height:1.5; padding:8px 12px; border-radius:8px;
  width:200px; white-space:normal; text-align:left;
  opacity:0; pointer-events:none; transition:opacity .15s;
  z-index:50; box-shadow:0 8px 24px rgba(0,0,0,.4);
}
.mode-tab:hover::after { opacity:1; }

/* ── Lang selector ── */
.lang-toggle { display:flex; gap:6px; margin-bottom:12px; }
.ltog-btn {
  padding:7px 16px; border:1px solid var(--border); border-radius:8px;
  background:var(--s2); color:var(--t2); font-size:13px; cursor:pointer;
  transition:all .15s; user-select:none;
}
.ltog-btn.active { background:rgba(99,102,241,.14); border-color:rgba(99,102,241,.4); color:var(--a2); font-weight:600; }
.lang-pills { display:none; flex-wrap:wrap; gap:6px; margin-top:4px; }
.lang-pills.visible { display:flex; }
.lang-pill {
  display:flex; align-items:center; gap:5px; padding:5px 11px;
  border:1px solid var(--border); border-radius:99px; background:var(--s2);
  font-size:12px; color:var(--t2); cursor:pointer; transition:all .12s; user-select:none;
}
.lang-pill:hover { border-color:var(--b2); color:var(--t1); }
.lang-pill.selected { background:rgba(99,102,241,.14); border-color:rgba(99,102,241,.45); color:var(--a2); }
.pill-flag { font-size:14px; line-height:1; }
.lang-pills-actions { display:flex; gap:8px; margin-top:8px; }
.lang-act-btn {
  font-size:12px; color:var(--t3); background:none; border:none;
  cursor:pointer; padding:0; transition:color .15s;
}
.lang-act-btn:hover { color:var(--t2); }

/* ── Info box (pipeline steps) ── */
.info-toggle {
  width:100%; display:flex; align-items:center; justify-content:space-between;
  background:none; border:none; padding:0; cursor:pointer; color:var(--t3); font-size:13px;
}
.info-toggle:hover { color:var(--t2); }
.info-toggle .arrow { transition:transform .2s; display:inline-block; }
.info-toggle.open .arrow { transform:rotate(180deg); }
.info-steps { display:none; margin-top:16px; }
.info-steps.open { display:flex; flex-direction:column; gap:1px; }
.step-row { display:flex; gap:14px; position:relative; padding-bottom:16px; }
.step-row:last-child { padding-bottom:0; }
.step-left { display:flex; flex-direction:column; align-items:center; width:36px; flex-shrink:0; }
.step-circle {
  width:36px; height:36px; border-radius:50%; border:1.5px solid var(--border);
  background:var(--s2); display:flex; align-items:center; justify-content:center; font-size:15px; flex-shrink:0;
}
.step-line { flex:1; width:1.5px; background:var(--border); margin-top:4px; min-height:8px; }
.step-row:last-child .step-line { display:none; }
.step-body { flex:1; padding-top:6px; }
.step-title { font-size:13px; font-weight:600; color:var(--t1); margin-bottom:3px; }
.step-desc { font-size:12px; color:var(--t3); line-height:1.6; }
.step-note { display:inline-block; margin-top:4px; font-size:11px; padding:2px 8px; border-radius:99px; background:rgba(245,158,11,.1); border:1px solid rgba(245,158,11,.2); color:var(--yellow); }

/* ── Run button ── */
.btn-run {
  width:100%; padding:14px; margin-top:4px;
  background:linear-gradient(135deg, var(--accent), var(--violet));
  color:#fff; border:none; border-radius:10px; font-size:15px; font-weight:700;
  cursor:pointer; transition:opacity .15s, transform .1s, box-shadow .2s;
  box-shadow:0 4px 24px rgba(99,102,241,.3); position:relative; overflow:hidden;
}
.btn-run::after { content:''; position:absolute; inset:0; background:linear-gradient(135deg,rgba(255,255,255,.08),transparent); }
.btn-run:hover:not(:disabled) { opacity:.9; box-shadow:0 6px 32px rgba(99,102,241,.4); }
.btn-run:active:not(:disabled) { transform:scale(.99); }
.btn-run:disabled { background:var(--s2); color:var(--t3); cursor:not-allowed; box-shadow:none; border:1px solid var(--border); }
.btn-spinner { display:none; width:15px; height:15px; border:2px solid rgba(255,255,255,.3); border-top-color:#fff; border-radius:50%; animation:spin .65s linear infinite; vertical-align:middle; margin-right:7px; }
.btn-run.loading .btn-spinner { display:inline-block; }
@keyframes spin { to { transform:rotate(360deg); } }

/* ── Progress ── */
.prog-wrap { height:3px; background:var(--border); border-radius:99px; overflow:hidden; display:none; margin-bottom:14px; }
.prog-wrap.on { display:block; }
.prog-bar { height:100%; width:0%; border-radius:99px; background:linear-gradient(90deg,var(--accent),var(--violet)); transition:width .5s ease; }

/* ── Log ── */
.log-hdr { display:flex; align-items:center; gap:10px; margin-bottom:14px; }
.log-dot { width:8px; height:8px; border-radius:50%; background:var(--accent); flex-shrink:0; }
.log-dot.running { animation:blink 1s infinite; }
.log-dot.done { background:var(--green); animation:none; }
.log-dot.err  { background:var(--red);   animation:none; }
.log-titl { font-size:13px; font-weight:600; color:var(--t2); }
.log-dom { font-size:12px; color:var(--t3); margin-left:auto; }
.log-area {
  background:#030712; border:1px solid #0d1827; border-radius:10px;
  padding:14px 16px; height:440px; overflow-y:auto;
  font-family:'JetBrains Mono','Fira Code','Courier New',monospace;
  font-size:12.5px; line-height:1.7; color:var(--t2);
}
.ll { display:flex; gap:10px; }
.ll .ts { color:#1a3347; flex-shrink:0; font-size:10.5px; padding-top:2px; user-select:none; min-width:48px; }
.ll .msg { flex:1; white-space:pre-wrap; word-break:break-all; }
.ll.ok   .msg { color:#4ade80; }
.ll.warn .msg { color:#fbbf24; }
.ll.err  .msg { color:#f87171; }
.ll.dim  .msg { color:var(--t3); }

/* ── Result banner ── */
.res-banner { margin-top:14px; padding:16px 20px; border-radius:10px; display:flex; align-items:center; gap:14px; font-size:14px; }
.res-banner.ok { background:rgba(34,197,94,.08); border:1px solid rgba(34,197,94,.2); }
.res-banner.fail { background:rgba(239,68,68,.08); border:1px solid rgba(239,68,68,.2); }
.res-ico { font-size:22px; flex-shrink:0; }
.res-txt { flex:1; line-height:1.5; }
.res-txt a { color:var(--a2); text-decoration:underline; text-underline-offset:3px; }
.res-btn { padding:8px 16px; border-radius:8px; background:rgba(99,102,241,.15); border:1px solid rgba(99,102,241,.28); color:var(--a2); font-size:13px; font-weight:600; text-decoration:none; white-space:nowrap; transition:background .15s; }
.res-btn:hover { background:rgba(99,102,241,.28); }

.hidden { display:none; }
</style>
</head>
<body>

<header>
  <div class="hdr">
    <div class="logo">⚡</div>
    <span class="hdr-title">SEO Pipeline</span>
    <span class="hdr-badge">Admin</span>
    <div class="hdr-sep"></div>
    <div class="hdr-dot"></div>
    <span class="hdr-online">Online</span>
  </div>
</header>

<div class="page">

<!-- ── Источник ── -->
<div class="slabel">Источник сайта</div>
<div class="card">
  <div class="src-tabs">
    <div class="src-tab active" data-src="zip" onclick="setSrc(this)">
      <span class="t-icon">📦</span><span class="t-name">ZIP-архив</span>
    </div>
    <div class="src-tab" data-src="archive" onclick="setSrc(this)">
      <span class="t-icon">🌐</span><span class="t-name">web.archive.org</span>
    </div>
    <div class="src-tab" data-src="github" onclick="setSrc(this)">
      <span class="t-icon">📂</span><span class="t-name">GitHub репо</span>
    </div>
  </div>

  <!-- ZIP -->
  <div class="src-panel active" id="panel-zip">
    <div class="upload-zone" id="drop-zone">
      <input type="file" id="zip-file" accept=".zip">
      <div class="up-icon" id="up-icon">📦</div>
      <div class="up-title">Перетащи ZIP-архив сюда</div>
      <div class="up-sub">или нажми для выбора · до 500 МБ</div>
      <div class="up-fname" id="file-name"></div>
    </div>
  </div>

  <!-- Archive URL -->
  <div class="src-panel" id="panel-archive">
    <div class="field">
      <label class="flabel">Ссылка на снапшот</label>
      <input type="text" id="archive-url"
        placeholder="https://web.archive.org/web/20230601120000/https://example.com/">
      <div class="fhint">
        Как найти: <a href="https://web.archive.org" target="_blank">web.archive.org</a>
        → введи домен → выбери дату → скопируй ссылку из адресной строки
      </div>
    </div>
  </div>

  <!-- GitHub source -->
  <div class="src-panel" id="panel-github">
    <div class="field">
      <label class="flabel">GitHub репозиторий (источник)</label>
      <input type="text" id="github-src" placeholder="username/repository">
      <div class="fhint">Репозиторий откуда взять сайт. Бот скачает содержимое и запустит pipeline.</div>
    </div>
  </div>
</div>

<!-- ── Параметры ── -->
<div class="slabel">Параметры</div>
<div class="card">
  <div class="row2">
    <div class="field">
      <label class="flabel">GitHub репозиторий <span style="font-weight:400;text-transform:none;letter-spacing:0">(для PR)</span></label>
      <input type="text" id="repo" placeholder="username/repository">
      <div class="fhint">Куда пушить результат. Оставь пустым — pipeline без PR.</div>
    </div>
    <div class="field">
      <label class="flabel">Домен сайта</label>
      <input type="text" id="domain" placeholder="example.com">
      <div class="fhint">Используется для canonical URL, sitemap и hreflang тегов.</div>
    </div>
  </div>
  <div class="field">
    <label class="flabel">GitHub токен</label>
    <input type="password" id="token" placeholder="ghp_xxxxxxxxxxxxxxxx">
    <div class="fhint">
      Нужен чтобы запушить изменения в GitHub-репозиторий и открыть Pull Request.
      Получить: <a href="https://github.com/settings/tokens/new?scopes=repo" target="_blank">github.com → Settings → Tokens → repo scope</a>
    </div>
  </div>
</div>

<!-- ── Настройки ── -->
<div class="slabel">Настройки</div>
<div class="card">
  <div class="row2">
    <div class="field">
      <label class="flabel">Режим</label>
      <div class="mode-tabs">
        <div class="mode-tab active" data-mode="full"
          data-tip="SEO-исправления + перевод на все выбранные языки. Создаёт полноценный многоязычный сайт."
          onclick="setMode(this)">
          <span class="m-icon">🚀</span><span class="m-name">Полный</span>
        </div>
        <div class="mode-tab" data-mode="seo_only"
          data-tip="Только SEO: canonical, title/description, Schema.org, OG-теги, robots.txt. Без перевода."
          onclick="setMode(this)">
          <span class="m-icon">🔧</span><span class="m-name">SEO</span>
        </div>
        <div class="mode-tab" data-mode="translate"
          data-tip="Только перевод страниц. SEO-исправления пропускаются — удобно если SEO уже было сделано раньше."
          onclick="setMode(this)">
          <span class="m-icon">🌍</span><span class="m-name">Перевод</span>
        </div>
      </div>
    </div>

    <div class="field">
      <label class="flabel">Языки перевода</label>
      <div class="lang-toggle">
        <div class="ltog-btn active" id="ltog-all" onclick="setLangMode('all')">Все 21 язык</div>
        <div class="ltog-btn" id="ltog-custom" onclick="setLangMode('custom')">Выбрать вручную</div>
      </div>
      <div class="lang-pills" id="lang-pills">
        """ + LANG_PILLS_HTML + r"""
      </div>
      <div class="lang-pills-actions hidden" id="lang-actions">
        <button class="lang-act-btn" onclick="selectAllLangs()">Выбрать все</button>
        <button class="lang-act-btn" onclick="clearAllLangs()">Сбросить</button>
        <span style="font-size:12px;color:var(--t3);margin-left:4px" id="lang-count"></span>
      </div>
    </div>
  </div>
</div>

<!-- ── Что произойдёт ── -->
<div class="slabel">Что произойдёт</div>
<div class="card">
  <button class="info-toggle" id="info-toggle" onclick="toggleInfo()">
    <span>Подробнее о шагах pipeline</span>
    <span class="arrow">▾</span>
  </button>
  <div class="info-steps" id="info-steps">
    <div class="step-row">
      <div class="step-left">
        <div class="step-circle">📥</div>
        <div class="step-line"></div>
      </div>
      <div class="step-body">
        <div class="step-title">Загрузка сайта</div>
        <div class="step-desc">
          ZIP распакуется мгновенно.<br>
          web.archive.org — бот скачает все страницы, CSS, JS, картинки через wget. Для сайта из ~100 страниц это займёт 5–15 минут.<br>
          GitHub — клонируется репозиторий.
        </div>
      </div>
    </div>
    <div class="step-row">
      <div class="step-left">
        <div class="step-circle">🔍</div>
        <div class="step-line"></div>
      </div>
      <div class="step-body">
        <div class="step-title">SEO аудит (до исправлений)</div>
        <div class="step-desc">Сканируются все HTML-страницы: считается количество проблем с title, description, canonical, OG-тегами, Schema.org. Результат покажется в логе как «X проблем на Y страницах».</div>
      </div>
    </div>
    <div class="step-row">
      <div class="step-left">
        <div class="step-circle">🔧</div>
        <div class="step-line"></div>
      </div>
      <div class="step-body">
        <div class="step-title">SEO-исправления</div>
        <div class="step-desc">Поэтапно: очистка archive.org-скриптов → canonical URL → обновление года в title → уникальные description → Schema.org (BreadcrumbList) → OG-теги → nofollow → robots.txt.</div>
      </div>
    </div>
    <div class="step-row">
      <div class="step-left">
        <div class="step-circle">🌍</div>
        <div class="step-line"></div>
      </div>
      <div class="step-body">
        <div class="step-title">Перевод</div>
        <div class="step-desc">Каждая страница переводится на выбранные языки. В логе будет живой счётчик «Перевод: N/K страниц...». Это самый долгий шаг — ~10 сек на страницу × количество языков.</div>
        <span class="step-note">⏱ При 100 стр. × 6 языков ≈ 1.5–2 часа</span>
      </div>
    </div>
    <div class="step-row">
      <div class="step-left">
        <div class="step-circle">🔀</div>
        <div class="step-line"></div>
      </div>
      <div class="step-body">
        <div class="step-title">Pull Request</div>
        <div class="step-desc">Клонируется целевой GitHub-репозиторий, файлы копируются, создаётся ветка <code style="background:var(--s3);padding:1px 5px;border-radius:4px;font-size:11px">seo-fixes</code>, пушится и открывается PR. В конце лога появится ссылка.</div>
      </div>
    </div>
  </div>
</div>

<!-- ── Кнопка ── -->
<button class="btn-run" id="start-btn" onclick="startJob()">
  <span class="btn-spinner"></span>
  <span id="btn-text">Запустить pipeline</span>
</button>

<!-- ── Лог ── -->
<div class="card hidden" id="log-card" style="margin-top:22px">
  <div class="log-hdr">
    <div class="log-dot running" id="log-dot"></div>
    <span class="log-titl" id="log-titl">Выполняется...</span>
    <span class="log-dom" id="log-dom"></span>
  </div>
  <div class="prog-wrap on" id="prog-wrap"><div class="prog-bar" id="prog-bar"></div></div>
  <div class="log-area" id="log"></div>
  <div class="res-banner hidden" id="result"></div>
</div>

</div><!-- /page -->

<script>
let selMode = 'full';
let selSrc  = 'zip';
let langMode = 'all';

/* ── Source tabs ── */
function setSrc(el) {
  document.querySelectorAll('.src-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.src-panel').forEach(p => p.classList.remove('active'));
  el.classList.add('active');
  selSrc = el.dataset.src;
  document.getElementById('panel-' + selSrc).classList.add('active');
}

/* ── Mode tabs ── */
function setMode(el) {
  document.querySelectorAll('.mode-tab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  selMode = el.dataset.mode;
}

/* ── File upload ── */
const fileInput = document.getElementById('zip-file');
const dropZone  = document.getElementById('drop-zone');
function setFile(file) {
  if (!file) return;
  const dt = new DataTransfer(); dt.items.add(file);
  fileInput.files = dt.files;
  document.getElementById('file-name').textContent = file.name;
  document.getElementById('up-icon').textContent = '✅';
  dropZone.classList.add('has-file');
}
fileInput.addEventListener('change', () => setFile(fileInput.files[0]));
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag'));
dropZone.addEventListener('drop', e => {
  e.preventDefault(); dropZone.classList.remove('drag');
  setFile(e.dataTransfer.files[0]);
});

/* ── Lang picker ── */
function setLangMode(mode) {
  langMode = mode;
  document.getElementById('ltog-all').classList.toggle('active', mode === 'all');
  document.getElementById('ltog-custom').classList.toggle('active', mode === 'custom');
  document.getElementById('lang-pills').classList.toggle('visible', mode === 'custom');
  document.getElementById('lang-actions').classList.toggle('hidden', mode !== 'custom');
  updateLangCount();
}
function toggleLang(el) {
  el.classList.toggle('selected');
  updateLangCount();
}
function selectAllLangs() {
  document.querySelectorAll('.lang-pill').forEach(p => p.classList.add('selected'));
  updateLangCount();
}
function clearAllLangs() {
  document.querySelectorAll('.lang-pill').forEach(p => p.classList.remove('selected'));
  updateLangCount();
}
function updateLangCount() {
  if (langMode !== 'custom') return;
  const n = document.querySelectorAll('.lang-pill.selected').length;
  document.getElementById('lang-count').textContent = n ? `выбрано: ${n}` : 'ни одного не выбрано';
}
function getSelectedLangs() {
  if (langMode === 'all') return 'all';
  const sel = [...document.querySelectorAll('.lang-pill.selected')].map(p => p.dataset.lang);
  return sel.length ? sel.join(',') : 'all';
}

/* ── Info toggle ── */
function toggleInfo() {
  const btn = document.getElementById('info-toggle');
  const steps = document.getElementById('info-steps');
  btn.classList.toggle('open');
  steps.classList.toggle('open');
}

/* ── Progress ── */
let _prog = 0;
function setProg(v) {
  _prog = v;
  document.getElementById('prog-bar').style.width = v + '%';
  document.getElementById('prog-wrap').classList.toggle('on', v > 0 && v < 100);
}

/* ── Start ── */
async function startJob() {
  const repo   = document.getElementById('repo').value.trim();
  const token  = document.getElementById('token').value.trim();
  const domain = document.getElementById('domain').value.trim();
  const langs  = getSelectedLangs();

  // Validate source
  let sourceOk = true;
  if (selSrc === 'zip' && !fileInput.files[0])                              { shake('drop-zone'); sourceOk = false; }
  if (selSrc === 'archive' && !document.getElementById('archive-url').value.trim()) { shake('archive-url'); sourceOk = false; }
  if (selSrc === 'github'  && !document.getElementById('github-src').value.trim())  { shake('github-src');  sourceOk = false; }
  if (!sourceOk) return;
  if (!domain) { shake('domain'); return; }

  if (langMode === 'custom' && langs === 'all') {
    alert('Выбери хотя бы один язык или переключись на «Все 21 язык»'); return;
  }

  const btn     = document.getElementById('start-btn');
  const btnText = document.getElementById('btn-text');
  btn.disabled = true; btn.classList.add('loading');
  btnText.textContent = selSrc === 'zip' ? 'Загружаю архив...' : 'Отправляю задание...';

  const logCard = document.getElementById('log-card');
  logCard.classList.remove('hidden');
  document.getElementById('log').innerHTML = '';
  document.getElementById('result').classList.add('hidden');
  document.getElementById('log-dom').textContent = domain;
  document.getElementById('log-dot').className = 'log-dot running';
  document.getElementById('log-titl').textContent = 'Выполняется...';
  setProg(5);
  logCard.scrollIntoView({ behavior:'smooth', block:'start' });

  const fd = new FormData();
  fd.append('source_type', selSrc);
  fd.append('repo',   repo);
  fd.append('token',  token);
  fd.append('domain', domain);
  fd.append('mode',   selMode);
  fd.append('langs',  langs);

  if (selSrc === 'zip')     fd.append('zipfile', fileInput.files[0]);
  if (selSrc === 'archive') fd.append('archive_url', document.getElementById('archive-url').value.trim());
  if (selSrc === 'github')  fd.append('github_src',  document.getElementById('github-src').value.trim());

  let resp;
  try { resp = await fetch('/start', { method:'POST', body:fd }); }
  catch (err) { showErr('Ошибка соединения: ' + err); resetBtn(btn, btnText); return; }

  const data = await resp.json();
  if (data.error) { showErr(data.error); resetBtn(btn, btnText); return; }

  btnText.textContent = 'Выполняется...';
  setProg(10);
  listenLogs(data.job_id, btn, btnText, domain);
}

function resetBtn(btn, btnText) {
  btn.disabled = false; btn.classList.remove('loading');
  btnText.textContent = 'Запустить pipeline';
  setProg(0);
}

/* ── SSE log stream ── */
function listenLogs(jobId, btn, btnText, domain) {
  const logEl = document.getElementById('log');

  function addLine(text, cls) {
    const ts  = new Date().toTimeString().slice(0, 8);
    const row = document.createElement('div');
    row.className = 'll' + (cls ? ' ' + cls : '');
    const tsEl  = document.createElement('span'); tsEl.className = 'ts'; tsEl.textContent = ts;
    const msgEl = document.createElement('span'); msgEl.className = 'msg'; msgEl.textContent = text;
    row.appendChild(tsEl); row.appendChild(msgEl);
    logEl.appendChild(row);
    logEl.scrollTop = logEl.scrollHeight;
    if (_prog < 90) setProg(Math.min(90, _prog + .4));
  }

  const es = new EventSource('/stream/' + jobId);
  es.onmessage = e => {
    const line = e.data; if (!line) return;

    if (line.startsWith('DONE:')) {
      es.close(); setProg(100); setTimeout(() => setProg(0), 900);
      const pr = line.slice(5).trim();
      const res = document.getElementById('result');
      res.classList.remove('hidden');
      res.className = 'res-banner ok';
      res.innerHTML = pr
        ? `<span class="res-ico">🎉</span><span class="res-txt">Pull Request создан для <strong>${domain}</strong><br><a href="${pr}" target="_blank">${pr}</a></span><a class="res-btn" href="${pr}" target="_blank">Открыть PR →</a>`
        : `<span class="res-ico">✅</span><span class="res-txt">Pipeline завершён для <strong>${domain}</strong> (без PR)</span>`;
      document.getElementById('log-dot').className = 'log-dot done';
      document.getElementById('log-titl').textContent = 'Завершено';
      btn.disabled = false; btn.classList.remove('loading'); btnText.textContent = 'Запустить ещё';
      return;
    }
    if (line.startsWith('ERROR:')) {
      es.close(); setProg(0);
      showErr('Pipeline завершился с ошибкой — проверь лог выше');
      document.getElementById('log-dot').className = 'log-dot err';
      document.getElementById('log-titl').textContent = 'Ошибка';
      btn.disabled = false; btn.classList.remove('loading'); btnText.textContent = 'Попробовать снова';
      return;
    }

    let cls = '';
    if (/✅|Готово|PR:|завершён/i.test(line))           cls = 'ok';
    else if (/❌|ОШИБКА|Error|Traceback/i.test(line))   cls = 'err';
    else if (/⚠|WARNING|warn|не создан/i.test(line))   cls = 'warn';
    else if (/^\s{4,}|File "|^\d{2}:\d{2}/.test(line)) cls = 'dim';
    addLine(line, cls);
  };
  let _errTimer = null;
  es.onerror = () => {
    // SSE reconnects automatically — wait 8s before declaring failure
    if (_errTimer) return;
    _errTimer = setTimeout(() => {
      if (es.readyState === EventSource.CLOSED) {
        addLine('⚠ Соединение прервано', 'warn');
        btn.disabled = false; btn.classList.remove('loading'); btnText.textContent = 'Попробовать снова';
      }
      _errTimer = null;
    }, 8000);
  };
}

function showErr(msg) {
  const res = document.getElementById('result');
  res.classList.remove('hidden');
  res.className = 'res-banner fail';
  res.innerHTML = `<span class="res-ico">❌</span><span class="res-txt">${msg}</span>`;
}

function shake(id) {
  const el = document.getElementById(id);
  el.style.animation = 'none'; el.offsetHeight;
  el.style.animation = 'shake .35s ease';
  el.addEventListener('animationend', () => el.style.animation = '', { once:true });
}
</script>
<style>
@keyframes shake {
  0%,100%{transform:translateX(0)} 20%{transform:translateX(-6px)}
  40%{transform:translateX(6px)}   60%{transform:translateX(-4px)} 80%{transform:translateX(4px)}
}
</style>
</body>
</html>"""


# ── Logging handler ───────────────────────────────────────────────────────────

class _QueueHandler(logging.Handler):
    def __init__(self, q):
        super().__init__()
        self.q = q
        self.setFormatter(logging.Formatter('%(asctime)s  %(message)s', '%H:%M:%S'))
    def emit(self, record):
        self.q.put(self.format(record))


# ── Stdout redirector (captures print() from pull.py etc.) ───────────────────

class _StdoutToQueue:
    """Write sys.stdout to the SSE queue AND the original stdout (Railway logs)."""
    def __init__(self, q, original):
        self.q = q
        self.original = original
        self._buf = ''

    def write(self, s):
        if self.original:
            self.original.write(s)
        self._buf += s
        while '\n' in self._buf:
            line, self._buf = self._buf.split('\n', 1)
            line = line.strip('\r')
            if line:
                self.q.put(line)

    def flush(self):
        if self.original:
            self.original.flush()
        if self._buf.strip():
            self.q.put(self._buf.strip())
            self._buf = ''

    def fileno(self):
        return self.original.fileno() if self.original else 1


# ── Sync PR creation ──────────────────────────────────────────────────────────

def _create_pr_sync(tmp_dir, site_dir, repo_slug, langs, token, log_fn):
    if not token:
        log_fn('⚠ Токен не указан — PR пропущен')
        return None
    try:
        from datetime import datetime
        from github import Github, Auth

        g = Github(auth=Auth.Token(token))
        gh_repo = g.get_repo(repo_slug)
        base_branch = gh_repo.default_branch

        branch_name = 'seo-fixes'
        try:
            gh_repo.get_branch(branch_name)
            branch_name = f'seo-fixes-{datetime.now().strftime("%Y%m%d-%H%M")}'
        except Exception:
            pass

        clone_dir = os.path.join(tmp_dir, '_git_clone')
        if os.path.exists(clone_dir):
            shutil.rmtree(clone_dir, ignore_errors=True)
        clone_url = f'https://x-access-token:{token}@github.com/{repo_slug}.git'
        log_fn(f'🔀 git clone {repo_slug}...')

        r = subprocess.run(['git', 'clone', '--depth=1', clone_url, clone_dir],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            log_fn(f'❌ git clone: {r.stderr.strip()}'); return None

        base_check = subprocess.run(
            ['git', 'ls-remote', '--exit-code', '--heads', 'origin', base_branch],
            cwd=clone_dir, capture_output=True, text=True)
        repo_is_empty = base_check.returncode != 0

        SKIP = {'.zip','.tar','.gz','.rar','.7z','.mp4','.mp3','.mov','.avi'}
        copied = 0
        for root, dirs, files in os.walk(site_dir):
            dirs[:] = [d for d in dirs if d not in ('.git','node_modules','_git_clone')]
            for fname in files:
                if os.path.splitext(fname)[1].lower() in SKIP: continue
                src = os.path.join(root, fname)
                dst = os.path.join(clone_dir, os.path.relpath(src, site_dir))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst); copied += 1
        log_fn(f'📋 Скопировано {copied} файлов')

        subprocess.run(['git','config','user.email','seobot@noreply.github.com'], cwd=clone_dir, capture_output=True)
        subprocess.run(['git','config','user.name','SEO Bot'], cwd=clone_dir, capture_output=True)

        push_target = base_branch if repo_is_empty else branch_name
        subprocess.run(['git','checkout','-b', push_target], cwd=clone_dir, capture_output=True)
        subprocess.run(['git','add','-A'], cwd=clone_dir, capture_output=True)

        msg = f'SEO fixes: translations ({", ".join(langs)}), schema, meta\n\nFiles: {copied}'
        r = subprocess.run(['git','commit','-m', msg], cwd=clone_dir, capture_output=True, text=True)
        if r.returncode != 0 and 'nothing to commit' in r.stdout + r.stderr:
            log_fn('⚠ Нет изменений для коммита'); return None

        log_fn(f'⬆ Push ветки {push_target}...')
        push_cmd = ['git','push','origin', push_target]
        if repo_is_empty: push_cmd.append('--set-upstream')
        r = subprocess.run(push_cmd, cwd=clone_dir, capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            log_fn(f'❌ git push: {r.stderr.strip()}'); return None

        if repo_is_empty:
            log_fn(f'✅ Репо было пустым — запушено в {base_branch}')
            return f'https://github.com/{repo_slug}/tree/{base_branch}'

        log_fn('📬 Создаю PR...')
        pr = gh_repo.create_pull(
            title='SEO improvements: translations, schema, meta',
            body=(f'## SEO Bot\n\n- 🌍 Переводы: {", ".join(langs).upper()}\n'
                  f'- 📝 title/description\n- 🗂️ Schema.org\n- 🔗 hreflang\n\nФайлов: {copied}'),
            head=branch_name, base=base_branch)
        return pr.html_url

    except Exception as e:
        log_fn(f'❌ Ошибка PR: {e}'); return None


# ── GitHub sync download ──────────────────────────────────────────────────────

def _download_github_sync(repo_slug, dest_dir, token, log_fn):
    import urllib.request
    log_fn(f'📥 Скачиваю {repo_slug} с GitHub...')
    headers = {'Authorization': f'Bearer {token}'} if token else {}

    for branch in ('main', 'master', 'gh-pages'):
        url = f'https://github.com/{repo_slug}/archive/refs/heads/{branch}.zip'
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = resp.read()
            log_fn(f'✅ Скачано ветка {branch}: {len(data)//1024} KB')
            break
        except Exception:
            continue
    else:
        raise RuntimeError(f'Не удалось скачать репо {repo_slug} (main/master/gh-pages)')

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        members = zf.namelist()
        top = members[0].split('/')[0] + '/'
        for member in members:
            target = os.path.join(dest_dir, member[len(top):])
            if member.endswith('/'):
                os.makedirs(target, exist_ok=True)
            else:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(member) as src, open(target, 'wb') as dst:
                    dst.write(src.read())
    return _find_site_root(dest_dir)


# ── Find site root in extracted ZIP ──────────────────────────────────────────

def _find_site_root(tmp_dir):
    entries = [e for e in os.listdir(tmp_dir) if not e.startswith('.') and e not in ('__MACOSX', '_git_clone')]
    if len(entries) == 1:
        candidate = os.path.join(tmp_dir, entries[0])
        if os.path.isdir(candidate):
            has_html = any(
                f.endswith(('.html', '.htm'))
                for f in os.listdir(candidate)
                if os.path.isfile(os.path.join(candidate, f))
            )
            if has_html:
                return candidate
    return tmp_dir


# ── Pipeline thread ───────────────────────────────────────────────────────────

def _pipeline_thread(job_id, source_type, source_value, tmp_dir,
                     domain, repo, token, mode, langs_str):
    job = _jobs[job_id]
    q   = job['log_queue']

    def log_fn(msg): q.put(msg)

    # Attach handlers to ROOT logger explicitly — logging.basicConfig() in
    # run_local.py is a no-op when Flask pre-initializes root handlers,
    # so without this, translation/SEO logging goes nowhere.
    root_log = logging.getLogger()
    root_log.setLevel(logging.INFO)
    handler = _QueueHandler(q)
    handler.setLevel(logging.DEBUG)
    root_log.addHandler(handler)
    # Also ensure Railway stdout gets the logs (not just the SSE queue).
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.INFO)
    stdout_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s', '%H:%M:%S'))
    root_log.addHandler(stdout_handler)

    # Redirect sys.stdout so that pull.py's print() calls also appear in SSE.
    old_stdout = sys.stdout
    sys.stdout = _StdoutToQueue(q, old_stdout)

    try:
        from run_local import run_pipeline, ALL_LANGS
        langs = ALL_LANGS if langs_str in ('all', '') else [l.strip() for l in langs_str.split(',')]

        # ── Phase 1: obtain site_dir ──────────────────────────────────────────
        log_fn(f'{"="*54}')
        if source_type == 'zip':
            site_dir = _find_site_root(tmp_dir)
            log_fn(f'📁 Архив распакован: {site_dir}')

        elif source_type == 'archive':
            log_fn(f'📥 Скачиваю снапшот из Wayback Machine...')
            log_fn(f'🌐 {source_value}')
            from pull import pull_snapshot
            site_dir = pull_snapshot(source_value, tmp_dir)
            log_fn(f'✅ Снапшот скачан: {site_dir}')

        elif source_type == 'github':
            site_dir = _download_github_sync(source_value, tmp_dir, token, log_fn)
            log_fn(f'📁 Репозиторий распакован: {site_dir}')

        else:
            raise ValueError(f'Unknown source_type: {source_type}')

        log_fn(f'{"="*54}')
        log_fn(f'▶ Домен: {domain}  |  Режим: {mode}  |  Языков: {len(langs)}')
        log_fn(f'{"="*54}')

        # ── Phase 2: SEO pipeline ─────────────────────────────────────────────
        result = run_pipeline(site_dir, domain, mode, langs)

        log_fn(f'{"="*54}')
        if result:
            log_fn(f'✅ Pipeline завершён. Проблем: {result["before"]} → {result["after"]}')
        else:
            log_fn('✅ Pipeline завершён')

        # ── Phase 3: Pull Request ─────────────────────────────────────────────
        pr_url = None
        if repo:
            log_fn('')
            pr_url = _create_pr_sync(tmp_dir, site_dir, repo, langs, token, log_fn)
            if pr_url:
                log_fn(f'✅ PR: {pr_url}')

        job['status'] = 'done'
        job['result'] = pr_url
        q.put(f'DONE:{pr_url or ""}')

    except Exception as e:
        import traceback
        log_fn(f'❌ Критическая ошибка: {e}')
        log_fn(traceback.format_exc())
        q.put('ERROR:')
        job['status'] = 'error'
    finally:
        sys.stdout = old_stdout
        root_log.removeHandler(handler)
        root_log.removeHandler(stdout_handler)
        q.put(None)


# ── Flask routes ──────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template_string(HTML)


@app.route('/start', methods=['POST'])
def start():
    source_type = request.form.get('source_type', 'zip')
    domain = request.form.get('domain', '').strip()
    repo   = request.form.get('repo', '').strip()
    token  = request.form.get('token', '').strip() or GITHUB_TOKEN
    mode   = request.form.get('mode', 'full')
    langs  = request.form.get('langs', 'all')

    if not domain:
        return jsonify({'error': 'Укажи домен сайта'}), 400

    tmp_dir = tempfile.mkdtemp(prefix='seoadmin_')
    source_value = ''

    try:
        if source_type == 'zip':
            if 'zipfile' not in request.files or not request.files['zipfile'].filename:
                return jsonify({'error': 'ZIP-файл не выбран'}), 400
            with zipfile.ZipFile(io.BytesIO(request.files['zipfile'].read())) as zf:
                zf.extractall(tmp_dir)

        elif source_type == 'archive':
            source_value = request.form.get('archive_url', '').strip()
            if not source_value:
                return jsonify({'error': 'Введи ссылку на снапшот web.archive.org'}), 400
            if 'web.archive.org' not in source_value:
                return jsonify({'error': 'Ссылка должна быть с web.archive.org'}), 400

        elif source_type == 'github':
            source_value = request.form.get('github_src', '').strip()
            if not source_value or '/' not in source_value:
                return jsonify({'error': 'Укажи репозиторий в формате username/repo'}), 400

    except zipfile.BadZipFile:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return jsonify({'error': 'Не удалось распаковать ZIP — файл повреждён'}), 400
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return jsonify({'error': str(e)}), 400

    job_id = str(uuid.uuid4())[:8]
    _jobs[job_id] = {'status': 'running', 'log_queue': queue.Queue(), 'result': None}

    threading.Thread(
        target=_pipeline_thread,
        args=(job_id, source_type, source_value, tmp_dir, domain, repo, token, mode, langs),
        daemon=True,
    ).start()

    return jsonify({'job_id': job_id})


@app.route('/stream/<job_id>')
def stream(job_id):
    if job_id not in _jobs:
        return 'Not found', 404

    def generate():
        q = _jobs[job_id]['log_queue']
        while True:
            try:
                line = q.get(timeout=25)
            except queue.Empty:
                yield ': keepalive\n\n'  # SSE comment — proxy stays alive, browser ignores
                continue
            if line is None: return
            yield f'data: {line}\n\n'

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


if __name__ == '__main__':
    port = int(os.environ.get('PORT') or os.environ.get('ADMIN_PORT', 8080))
    print(f'\n  SEO Admin Panel → http://localhost:{port}\n')
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
