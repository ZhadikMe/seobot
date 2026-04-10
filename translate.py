#!/usr/bin/env python3
"""
translate.py — SEO-aware HTML translator via wowaitranslate API (DeepL-compatible)

Usage:
  python scripts/translate.py --langs ru,de,fr,es
  python scripts/translate.py --langs ru --page blog/solitary/index.html
  python scripts/translate.py --langs ru,de,fr,es --dry-run

Requirements:
  pip install requests
"""

import os, re, sys, time, argparse
sys.stdout.reconfigure(encoding='utf-8')

import requests

# ── Config ────────────────────────────────────────────────────────────────────

SITE     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_URL = 'https://example.com'  # overridden by --base-url argument

WOWAI_API_URL = 'https://app.wowaitranslate.com/v2/translate'

SUPPORTED_LANGS = {
    'ru': 'RU',
    'de': 'DE',
    'fr': 'FR',
    'es': 'ES',
    'it': 'IT',
    'pt': 'PT',
    'pl': 'PL',
    'nl': 'NL',
    'cs': 'CS',
    'ro': 'RO',
    'sv': 'SV',
    'tr': 'TR',
    'el': 'EL',
    'uk': 'UA',
    'ko': 'KO',
    'zh': 'ZH',
    'ja': 'JA',
    'sk': 'SK',
    'fi': 'FI',
    'ar': 'AR',
    'hi': 'HI',
}

LANG_LOCALE = {
    'ru': 'ru_RU',
    'de': 'de_DE',
    'fr': 'fr_FR',
    'es': 'es_ES',
    'it': 'it_IT',
    'pt': 'pt_PT',
    'pl': 'pl_PL',
    'nl': 'nl_NL',
    'cs': 'cs_CZ',
    'ro': 'ro_RO',
    'sv': 'sv_SE',
    'tr': 'tr_TR',
    'el': 'el_GR',
    'uk': 'uk_UA',
    'ko': 'ko_KR',
    'zh': 'zh_CN',
    'ja': 'ja_JP',
    'sk': 'sk_SK',
    'fi': 'fi_FI',
    'ar': 'ar_SA',
    'hi': 'hi_IN',
}

LANG_NAME = {
    'ru': ('🇷🇺', 'Русский'),
    'de': ('🇩🇪', 'Deutsch'),
    'fr': ('🇫🇷', 'Français'),
    'es': ('🇪🇸', 'Español'),
    'it': ('🇮🇹', 'Italiano'),
    'pt': ('🇵🇹', 'Português'),
    'pl': ('🇵🇱', 'Polski'),
    'nl': ('🇳🇱', 'Nederlands'),
    'cs': ('🇨🇿', 'Čeština'),
    'ro': ('🇷🇴', 'Română'),
    'sv': ('🇸🇪', 'Svenska'),
    'tr': ('🇹🇷', 'Türkçe'),
    'el': ('🇬🇷', 'Ελληνικά'),
    'uk': ('🇺🇦', 'Українська'),
    'ko': ('🇰🇷', '한국어'),
    'zh': ('🇨🇳', '中文'),
    'ja': ('🇯🇵', '日本語'),
    'sk': ('🇸🇰', 'Slovenčina'),
    'fi': ('🇫🇮', 'Suomi'),
    'ar': ('🇸🇦', 'العربية'),
    'hi': ('🇮🇳', 'हिन्दी'),
}

# Languages that use non-Latin scripts — fake detection via character ratio
_NON_LATIN_LANGS = {'ru', 'uk', 'el', 'ko', 'zh', 'ja', 'ar', 'hi'}

# Languages written right-to-left — patch_html removes dir="rtl" for LTR translations
RTL_LANGS = {'ar'}

# ── Source language detection ─────────────────────────────────────────────────

def detect_source_lang(site_dir: str) -> str:
    """
    Detect the source language of the site from <html lang="..."> attribute.
    Walks up to 5 HTML files, returns the most common lang code (default 'en').
    """
    from collections import Counter
    counts: Counter = Counter()
    checked = 0
    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs
                   if d not in list(SUPPORTED_LANGS.keys()) + ['scripts', '.git', 'node_modules',
                                                                  'web.archive.org', 'web-static.archive.org', '_git_clone']]
        for fname in files:
            if not fname.endswith('.html'):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, encoding='utf-8', errors='ignore') as f:
                    snippet = f.read(2000)
                m = re.search(r'<html[^>]+\blang=["\']([a-z]{2,5})["\']', snippet, re.IGNORECASE)
                if m:
                    lang = m.group(1).lower().split('-')[0]  # "ar-SA" → "ar"
                    counts[lang] += 1
            except Exception:
                pass
            checked += 1
            if checked >= 10:
                break
        if checked >= 10:
            break
    if not counts:
        return 'en'
    top = counts.most_common(1)[0][0]
    return top if top in SUPPORTED_LANGS or top == 'en' else 'en'


# ── Translation API ───────────────────────────────────────────────────────────

def translate_batch(api_key: str, segments: list[str], target_lang: str, retries=3) -> dict:
    """
    Translate a list of text segments via wowaitranslate.
    Returns dict: {original: translated}
    Automatically chunks large segment lists to avoid API limits.
    """
    if not segments:
        return {}

    lang_code = SUPPORTED_LANGS.get(target_lang, target_lang.upper())
    result = {}

    # Chunk into batches of 50 segments to avoid API payload limits
    chunk_size = 50
    chunks = [segments[i:i+chunk_size] for i in range(0, len(segments), chunk_size)]

    for chunk in chunks:
        for attempt in range(retries):
            try:
                resp = requests.post(
                    WOWAI_API_URL,
                    headers={
                        'Authorization': f'DeepL-Auth-Key {api_key}',
                        'Content-Type': 'application/json',
                    },
                    json={'text': chunk, 'target_lang': lang_code},
                    timeout=60,
                )
                resp.raise_for_status()
                translations = resp.json().get('translations', [])
                for i in range(min(len(chunk), len(translations))):
                    result[chunk[i]] = translations[i]['text']
                break
            except Exception as e:
                if attempt == retries - 1:
                    print(f'    translate error after {retries} attempts: {e}')
                else:
                    time.sleep(2 ** attempt)

    return result


# ── Anti-fake detection ───────────────────────────────────────────────────────

def _is_fake_translation(original: str, translated: str, lang: str) -> bool:
    """
    Detect fake/failed translations.

    For non-Latin scripts (ru, el, ko, zh, ja, ar, hi): checks that the
    translated text actually contains the expected script characters.
    Less than 15% non-ASCII chars → the API returned the original unchanged.

    For Latin-script languages: only rejects identical strings. Short UI
    strings like "Home"→"Home" (DE keeps "Home") are valid, so we don't
    use a similarity threshold that would incorrectly discard them.
    """
    if not translated or not translated.strip():
        return True
    if original.strip() == translated.strip():
        return True
    if lang in _NON_LATIN_LANGS:
        text = re.sub(r'<[^>]+>', '', translated).replace(' ', '')
        if not text:
            return True
        non_ascii = sum(1 for c in text if ord(c) > 127)
        ratio = non_ascii / len(text)
        return ratio < 0.15  # < 15% target-script chars = not actually translated
    return False


# ── Segment pre-filter ────────────────────────────────────────────────────────

def _should_skip_segment(text: str) -> bool:
    """Return True for segments that don't need translation."""
    # Pure numbers, prices, dates, percentages
    if re.match(r'^[\d\s$€£¥.,:/%()\-+]+$', text):
        return True
    # URLs
    if re.match(r'^https?://', text):
        return True
    # Email addresses
    if re.match(r'^[\w.+-]+@[\w.-]+\.\w+$', text):
        return True
    # Copyright lines
    if re.match(r'^©|^Copyright\b', text, re.IGNORECASE):
        return True
    # All-caps short codes / abbreviations (CSS, HTML, API, SEO …)
    if re.match(r'^[A-Z0-9]{1,5}$', text):
        return True
    return False


# ── Text extraction from HTML ─────────────────────────────────────────────────

def _extract_text_nodes(inner_html: str, segments: list, min_len: int = 10):
    """
    Extract individual direct text nodes from an HTML fragment.
    Splitting by tags ensures each segment is a single contiguous text node
    that patch_html can locate and replace, even when the parent element
    contains inner tags like <br>, <span>, <strong>.
    """
    parts = re.split(r'<[^>]+>', inner_html)
    for part in parts:
        text = re.sub(r'\s+', ' ', part).strip()
        if (len(text) >= min_len
                and not text.startswith('http')
                and not re.match(r'^[\W\d]+$', text)
                and not _should_skip_segment(text)):
            segments.append(text)


def extract_translatable(html: str) -> list[str]:
    """
    Extract text segments that need translation.
    Returns list of unique, pre-filtered text strings.
    """
    segments = []

    def add(pattern, flags=re.IGNORECASE | re.DOTALL):
        for m in re.finditer(pattern, html, flags):
            text = m.group(1).strip()
            if not text or text.startswith('http') or len(text) < 3:
                continue
            pos = m.start()
            preceding = html[max(0, pos - 200):pos]
            if '<script' in preceding or '<style' in preceding:
                continue
            if not _should_skip_segment(text):
                segments.append(text)

    # ── Meta tags ──
    add(r'<title>([^<]+)</title>')
    add(r'<meta\s+name=["\']description["\']\s+content="([^"]+)"')
    add(r'<meta\s+property=["\']og:title["\']\s+content="([^"]+)"')
    add(r'<meta\s+property=["\']og:description["\']\s+content="([^"]+)"')
    add(r'<meta\s+name=["\']twitter:title["\']\s+content="([^"]+)"')
    add(r'<meta\s+name=["\']twitter:description["\']\s+content="([^"]+)"')

    # ── Alt text for images ──
    for m in re.finditer(r'<img[^>]+\balt="([^"]+)"', html, re.IGNORECASE):
        text = m.group(1).strip()
        if len(text) >= 3 and not text.startswith('http') and not re.match(r'^[\W\d]+$', text):
            if not _should_skip_segment(text):
                segments.append(text)

    # ── Headings — split by inner tags (<br/>, <span>, <a>…) so each text node
    #    is a separate segment that patch_html can locate and replace in-place ──
    for tag in ['h1', 'h2', 'h3', 'h4']:
        for m in re.finditer(rf'<{tag}[^>]*>(.*?)</{tag}>', html, re.IGNORECASE | re.DOTALL):
            pos = m.start()
            preceding = html[max(0, pos - 200):pos]
            if '<script' in preceding or '<style' in preceding:
                continue
            _extract_text_nodes(m.group(1), segments, min_len=3)

    # ── Body content: paragraphs, list items, table cells, buttons ──
    main_m = re.search(r'<(?:main|article)[^>]*>(.*?)</(?:main|article)>', html, re.DOTALL | re.IGNORECASE)
    if main_m:
        content_html = main_m.group(1)
    else:
        # Fallback for div-based layouts (no <main>/<article>): use entire <body>
        body_m = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL | re.IGNORECASE)
        if body_m:
            content_html = re.sub(
                r'<(script|style)[^>]*>.*?</(script|style)>', '', body_m.group(1),
                flags=re.DOTALL | re.IGNORECASE
            )
        else:
            content_html = None

    if content_html:
        for m in re.finditer(r'<p[^>]*>(.*?)</p>', content_html, re.IGNORECASE | re.DOTALL):
            _extract_text_nodes(m.group(1), segments, min_len=15)
        for m in re.finditer(r'<li[^>]*>(.*?)</li>', content_html, re.IGNORECASE | re.DOTALL):
            _extract_text_nodes(m.group(1), segments, min_len=8)
        # Table cells
        for m in re.finditer(r'<(?:td|th)[^>]*>(.*?)</(?:td|th)>', content_html, re.IGNORECASE | re.DOTALL):
            _extract_text_nodes(m.group(1), segments, min_len=5)
        # Buttons and labels
        for tag in ('button', 'label'):
            for m in re.finditer(rf'<{tag}[^>]*>(.*?)</{tag}>', content_html, re.IGNORECASE | re.DOTALL):
                _extract_text_nodes(m.group(1), segments, min_len=3)

    # ── figcaption ──
    if content_html:
        for m in re.finditer(r'<figcaption[^>]*>(.*?)</figcaption>', content_html, re.IGNORECASE | re.DOTALL):
            text = re.sub(r'<[^>]+>', '', m.group(1)).strip()
            if text and len(text) >= 5 and not _should_skip_segment(text):
                segments.append(text)

    # ── input placeholder ──
    for m in re.finditer(r'<input[^>]+\bplaceholder="([^"]+)"', html, re.IGNORECASE):
        text = m.group(1).strip()
        if len(text) >= 3 and not _should_skip_segment(text):
            segments.append(text)

    # ── card-title / card-text / card-body (common Bootstrap patterns) ──
    if content_html:
        for cls in ('card-title', 'card-text', 'card-body', 'card-header', 'card-subtitle'):
            for m in re.finditer(
                rf'<[a-z][^>]+\bclass="[^"]*{cls}[^"]*"[^>]*>(.*?)</[a-z]+>',
                content_html, re.IGNORECASE | re.DOTALL
            ):
                text = re.sub(r'<[^>]+>', '', m.group(1)).strip()
                if len(text) >= 5 and not text.startswith('http') and not _should_skip_segment(text):
                    segments.append(text)

    # ── standalone <span> with meaningful text (not nested deeply) ──
    if content_html:
        for m in re.finditer(r'<span[^>]*>([^<]{5,100})</span>', content_html, re.IGNORECASE):
            text = m.group(1).strip()
            if (len(text) >= 5
                    and not text.startswith('http')
                    and not re.match(r'^[\W\d]+$', text)
                    and not _should_skip_segment(text)):
                segments.append(text)

    # ── Navigation / header / footer — short UI strings ──
    # Also handles div-based navs: <div id/class containing nav/menu/navbar>
    nav_sections = []
    for section_tag in ('nav', 'header', 'footer'):
        m = re.search(rf'<{section_tag}[^>]*>(.*?)</{section_tag}>', html, re.DOTALL | re.IGNORECASE)
        if m:
            nav_sections.append(m.group(1))
    # Div-based nav fallback
    for m in re.finditer(
        r'<div[^>]+(?:id|class)=["\'][^"\']*(?:nav|menu|navbar)[^"\']*["\'][^>]*>(.*?)</div>',
        html, re.DOTALL | re.IGNORECASE
    ):
        nav_sections.append(m.group(1))

    for section_html in nav_sections:
        for m in re.finditer(r'<a[^>]*>(.*?)</a>', section_html, re.IGNORECASE | re.DOTALL):
            text = re.sub(r'<[^>]+>', '', m.group(1)).strip()
            if (2 <= len(text) <= 60
                    and not text.startswith('http')
                    and not re.match(r'^[\d\s.,:;!?]+$', text)
                    and not _should_skip_segment(text)):
                segments.append(text)
        for tag in ('button', 'span'):
            for m in re.finditer(rf'<{tag}[^>]*>(.*?)</{tag}>', section_html, re.IGNORECASE | re.DOTALL):
                text = re.sub(r'<[^>]+>', '', m.group(1)).strip()
                if (2 <= len(text) <= 80
                        and not text.startswith('http')
                        and not re.match(r'^[\d\s.,:;!?]+$', text)
                        and not _should_skip_segment(text)):
                    segments.append(text)

    # ── Deduplicate while preserving order ──
    seen = set()
    unique = []
    for s in segments:
        if s not in seen:
            seen.add(s)
            unique.append(s)

    return unique


# ── HTML patching ─────────────────────────────────────────────────────────────

def _is_flat_root(rel_path: str) -> bool:
    """True if the file is a flat HTML in site root (e.g. /index.html, /about-us.html)."""
    return rel_path.count('/') == 1


def _fix_flat_resources(html: str) -> str:
    """
    For flat HTML files translated into a /lang/ subdirectory,
    prefix all relative resource paths (CSS/JS/images) with ../
    so they resolve correctly from one level deeper.
    Navigation links (*.html) are left untouched.
    """
    def fix_attr(m):
        attr_eq_quote = m.group(1)
        path = m.group(2)
        closing_quote = m.group(3)
        if path.startswith(('http', '/', '#', 'data:', '..')):
            return m.group(0)
        if re.search(r'\.html?(\?|#|$)', path):
            return m.group(0)
        return f'{attr_eq_quote}../{path}{closing_quote}'

    html = re.sub(r'(<link[^>]+href=")([^"]+)(")', fix_attr, html)
    html = re.sub(r'(<link[^>]+href=\')([^\']+)(\')', fix_attr, html)
    html = re.sub(r'(<script[^>]+src=")([^"]+)(")', fix_attr, html)
    html = re.sub(r'(<img[^>]+src=")([^"]+)(")', fix_attr, html)
    html = re.sub(r'(<source[^>]+src=")([^"]+)(")', fix_attr, html)
    html = re.sub(
        r'(url\(["\']?)(?!http|/|data:)([^"\')\s]+)(["\']?\))',
        lambda m: m.group(1) + '../' + m.group(2) + m.group(3),
        html
    )
    return html


def patch_html(html: str, translations: dict, lang: str, original_rel_path: str) -> str:
    """Apply translations to HTML, update lang/hreflang/canonical/og:locale."""
    patched = html

    # Replace translatable strings — longest first to avoid partial matches
    for original, translated in sorted(translations.items(), key=lambda x: -len(x[0])):
        if not translated or original == translated:
            continue
        escaped = re.escape(original)
        # Segments are extracted with whitespace collapsed (newline → space).
        # HTML may retain original whitespace (newlines, tabs, extra spaces).
        # re.escape converts spaces to '\ ' — replace with \s+ to match any whitespace.
        escaped = escaped.replace(r'\ ', r'\s+')

        # 1. Text nodes between tags
        patched = re.sub(
            r'(>(?:[^<]*))' + escaped,
            lambda m, t=translated: m.group(1) + t,
            patched
        )
        # 2. content= attribute (meta description, og:title, etc.)
        patched = re.sub(
            r'(content=["\'])([^"\']*?)' + escaped,
            lambda m, t=translated: m.group(1) + m.group(2) + t,
            patched
        )
        # 3. Link text inside <a> tags
        patched = re.sub(
            r'(<a[^>]*>)([^<]*)' + escaped + r'([^<]*)(</a>)',
            lambda m, t=translated: m.group(1) + m.group(2) + t + m.group(3) + m.group(4),
            patched
        )
        # 4. alt= attribute on images
        patched = re.sub(
            r'(\balt=")([^"]*)' + escaped + r'([^"]*")',
            lambda m, t=translated: m.group(1) + m.group(2) + t + m.group(3),
            patched
        )
        # 5. title= attribute (tooltips, link titles)
        patched = re.sub(
            r'(\btitle=")([^"]*)' + escaped + r'([^"]*")',
            lambda m, t=translated: m.group(1) + m.group(2) + t + m.group(3),
            patched
        )

    # Fix absolute internal links: /path → /{lang}/path
    _all_lang_codes = '|'.join(re.escape(l) for l in SUPPORTED_LANGS)
    def _fix_a_href(m):
        pre, href, post = m.group(1), m.group(2), m.group(3)
        if re.match(rf'^/({_all_lang_codes})/', href):
            return m.group(0)
        return f'{pre}href="/{lang}{href}"{post}'

    patched = re.sub(
        r'(<a\b[^>]*?\s)href="(/[^"#][^"]*)"([^>]*>)',
        _fix_a_href,
        patched
    )

    # For Arabic/RTL target language: set dir="rtl" on html tag
    # For non-RTL target language translating an RTL source: remove dir="rtl"
    if lang in RTL_LANGS:
        patched = re.sub(r'(<html[^>]*)(?:\s+dir=["\'][^"\']*["\'])?', r'\1 dir="rtl"', patched, count=1)
    else:
        patched = re.sub(r'\s+dir=["\']rtl["\']', '', patched)

    # Fix relative resource paths for flat-root HTML moved into /lang/ subdir
    if _is_flat_root(original_rel_path):
        patched = _fix_flat_resources(patched)

    # Update <html lang="">
    patched = re.sub(r'(<html[^>]*)\blang=["\'][^"\']*["\']', rf'\1lang="{lang}"', patched)

    # Update og:locale
    patched = re.sub(
        r'<meta\s+property=["\']og:locale["\']\s+content="[^"]*">',
        f'<meta property="og:locale" content="{LANG_LOCALE.get(lang, lang)}">',
        patched
    )
    if 'og:locale' not in patched:
        patched = patched.replace(
            '<meta property="og:type"',
            f'<meta property="og:locale" content="{LANG_LOCALE.get(lang, lang)}">\n  <meta property="og:type"'
        )

    # Update canonical to translated URL
    page_path = original_rel_path.replace('/index.html', '/').replace('\\', '/')
    if not page_path.startswith('/'):
        page_path = '/' + page_path
    translated_url = f'{BASE_URL}/{lang}{page_path}'
    patched = re.sub(
        r'<link\s+rel=["\']canonical["\']\s+href="[^"]*">',
        f'<link rel="canonical" href="{translated_url}">',
        patched
    )

    # Update og:url
    patched = re.sub(
        r'<meta\s+property=["\']og:url["\']\s+content="[^"]*">',
        f'<meta property="og:url" content="{translated_url}">',
        patched
    )

    return patched


def add_hreflang(html: str, original_rel_path: str, available_langs: list,
                  source_lang: str = 'en') -> str:
    """Add hreflang alternate links for all confirmed translations + source lang."""
    page_path = original_rel_path.replace('/index.html', '/').replace('\\', '/')
    if not page_path.startswith('/'):
        page_path = '/' + page_path

    # Remove existing hreflang links
    html = re.sub(r'\n\s*<link\s+rel=["\']alternate["\']\s+hreflang=[^>]+>', '', html)

    # Source lang is always the root URL
    lines = [f'  <link rel="alternate" hreflang="{source_lang}" href="{BASE_URL}{page_path}">']
    for lang in sorted(available_langs):
        if lang == source_lang:
            continue  # already added above
        lines.append(f'  <link rel="alternate" hreflang="{lang}" href="{BASE_URL}/{lang}{page_path}">')
    lines.append(f'  <link rel="alternate" hreflang="x-default" href="{BASE_URL}{page_path}">')

    hreflang_block = '\n' + '\n'.join(lines)
    html = html.replace('</head>', hreflang_block + '\n</head>', 1)
    return html


# ── Nav/footer translation cache ─────────────────────────────────────────────

def _extract_nav_segments(html: str) -> list[str]:
    """
    Extract ONLY nav/header/footer UI strings (short, repeated across pages).
    Used for pre-building a session-level cache to avoid re-translating identical
    nav strings on every page.
    """
    segments = []
    sections = []

    for tag in ('nav', 'header', 'footer'):
        m = re.search(rf'<{tag}[^>]*>(.*?)</{tag}>', html, re.DOTALL | re.IGNORECASE)
        if m:
            sections.append(m.group(1))
    for m in re.finditer(
        r'<div[^>]+(?:id|class)=["\'][^"\']*(?:nav|menu|navbar|header|footer)[^"\']*["\'][^>]*>(.*?)</div>',
        html, re.DOTALL | re.IGNORECASE
    ):
        sections.append(m.group(1))

    for section_html in sections:
        for m in re.finditer(r'<a[^>]*>(.*?)</a>', section_html, re.IGNORECASE | re.DOTALL):
            text = re.sub(r'<[^>]+>', '', m.group(1)).strip()
            if 2 <= len(text) <= 60 and not text.startswith('http') and not _should_skip_segment(text):
                segments.append(text)
        for tag in ('button', 'span', 'li'):
            for m in re.finditer(rf'<{tag}[^>]*>([^<]{{2,80}})</{tag}>', section_html, re.IGNORECASE):
                text = m.group(1).strip()
                if 2 <= len(text) <= 80 and not _should_skip_segment(text):
                    segments.append(text)

    seen = set()
    return [s for s in segments if not (s in seen or seen.add(s))]


def build_nav_cache(api_key: str, site_dir: str, target_langs: list,
                    source_lang: str = 'en') -> dict:
    """
    Pre-translate all nav/header/footer strings from every page — once per language.
    Returns {lang: {original: translated}}.

    Saves ~30% API calls by avoiding re-translation of identical UI strings
    (nav links, buttons) that appear on every page.
    """
    all_nav_segs: set[str] = set()

    skip_dirs = set(SUPPORTED_LANGS.keys()) | {'scripts', '.git', 'node_modules',
                                                 'web.archive.org', 'web-static.archive.org', '_git_clone'}
    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fname in files:
            if not fname.endswith('.html'):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, encoding='utf-8', errors='ignore') as f:
                    html = f.read()
                for seg in _extract_nav_segments(html):
                    all_nav_segs.add(seg)
            except Exception:
                pass

    if not all_nav_segs:
        return {}

    nav_list = sorted(all_nav_segs)
    print(f'  [nav-cache] {len(nav_list)} unique nav/footer strings → pre-translating...')

    cache: dict[str, dict[str, str]] = {}
    for lang in target_langs:
        raw = translate_batch(api_key, nav_list, lang)
        filtered = {o: t for o, t in raw.items() if not _is_fake_translation(o, t, lang)}
        cache[lang] = filtered
        print(f'  [nav-cache] {lang}: {len(filtered)}/{len(nav_list)} strings cached')

    return cache


# ── Per-page translation ──────────────────────────────────────────────────────

def translate_page(api_key: str, src_path: str, rel_path: str, target_langs: list,
                   dry_run=False, skip_existing=False,
                   nav_cache: dict | None = None,
                   source_lang: str = 'en') -> list:
    """
    Translate one HTML page to all target languages. Returns list of confirmed langs.

    nav_cache: pre-built {lang: {original: translated}} for nav/footer strings.
               Avoids re-translating repeated UI strings on every page.
    source_lang: source language of the site (default 'en').
    """
    with open(src_path, encoding='utf-8') as f:
        html = f.read()

    segments = extract_translatable(html)
    if not segments:
        print(f'  skip (no segments): {rel_path}')
        return []

    print(f'\n  {rel_path} — {len(segments)} segments')

    confirmed_langs = []

    # Determine output path: flat HTML keeps filename, subdir HTML → index.html
    src_fname = os.path.basename(rel_path)
    is_flat   = _is_flat_root(rel_path)

    for lang in target_langs:
        if lang == source_lang:
            continue  # never translate into the source language

        if is_flat and src_fname != 'index.html':
            out_dir  = os.path.join(SITE, lang)
            out_path = os.path.join(out_dir, src_fname)
        else:
            page_dir = os.path.dirname(rel_path.lstrip('/'))
            out_dir  = os.path.join(SITE, lang, page_dir)
            out_path = os.path.join(out_dir, 'index.html')

        if skip_existing and not dry_run and os.path.exists(out_path):
            print(f'    → {lang}: skip (exists)')
            confirmed_langs.append(lang)
            continue

        print(f'    → {lang}...', end=' ', flush=True)

        # Start with nav/footer cache for this language (avoids repeated API calls)
        lang_cache = nav_cache.get(lang, {}) if nav_cache else {}

        # Only send segments not already in cache to the API
        uncached = [s for s in segments if s not in lang_cache]
        if uncached:
            raw_translations = translate_batch(api_key, uncached, lang)
        else:
            raw_translations = {}

        if not raw_translations and not lang_cache:
            print('FAILED')
            continue

        # Merge cache + fresh translations, filter fakes
        all_raw = {**lang_cache, **raw_translations}
        translations = {
            o: t for o, t in all_raw.items()
            if not _is_fake_translation(o, t, lang)
        }

        valid     = len(translations)
        total     = len(segments)
        from_cache = sum(1 for s in segments if s in lang_cache)
        fake      = len(all_raw) - valid
        cache_note = f', {from_cache} cached' if from_cache else ''
        fake_note  = f', {fake} fake' if fake else ''
        print(f'OK ({valid}/{total} translated{cache_note}{fake_note})')

        if dry_run:
            confirmed_langs.append(lang)
            continue

        translated_html = patch_html(html, translations, lang, rel_path)

        os.makedirs(out_dir, exist_ok=True)
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(translated_html)

        confirmed_langs.append(lang)

    # Update source page with hreflang — merge with existing langs to preserve
    # translations from previous runs (e.g. re-run with different languages)
    if confirmed_langs and not dry_run:
        existing_langs = re.findall(
            r'<link[^>]*hreflang=["\']([a-z]{2})["\'][^>]*>', html, re.IGNORECASE
        )
        all_langs = sorted(
            set(existing_langs + confirmed_langs) - {source_lang, 'x-default', 'x'}
        )
        updated_html = add_hreflang(html, rel_path, all_langs, source_lang=source_lang)
        with open(src_path, 'w', encoding='utf-8') as f:
            f.write(updated_html)
        print(f'    hreflang → {source_lang} page: {all_langs}')

    return confirmed_langs


# ── Sitemap update ────────────────────────────────────────────────────────────

def update_sitemap(translated_pages: dict):
    """Rebuild sitemap.xml with all EN pages + translated pages."""
    sitemap_path = os.path.join(SITE, 'sitemap.xml')

    if not os.path.exists(sitemap_path):
        sitemap = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            '</urlset>'
        )
    else:
        with open(sitemap_path, encoding='utf-8') as f:
            sitemap = f.read()

    if BASE_URL != 'https://example.com':
        def fix_loc(m):
            url = m.group(1)
            fixed = re.sub(r'^https?://[^/]+', BASE_URL, url)
            return f'<loc>{fixed}</loc>'
        sitemap = re.sub(r'<loc>(https?://[^<]+)</loc>', fix_loc, sitemap)

    new_entries = []

    for root, dirs, files in os.walk(SITE):
        dirs[:] = [d for d in dirs
                   if d not in ('scripts', 'images', 'node_modules', '.git', 'web.archive.org', 'web-static.archive.org', 'gmpg.org', '_git_clone')
                   + tuple(SUPPORTED_LANGS.keys())]
        for fname in files:
            if not fname.endswith('.html'):
                continue
            fpath     = os.path.join(root, fname)
            rel       = fpath.replace(SITE, '').replace(os.sep, '/')
            page_path = rel
            if page_path.endswith('/index.html'):
                page_path = page_path[:-len('index.html')]
            elif page_path == '/index.html':
                page_path = '/'
            if not page_path.startswith('/'):
                page_path = '/' + page_path
            url = BASE_URL.rstrip('/') + page_path
            if url not in sitemap:
                new_entries.append(
                    f'  <url>\n'
                    f'    <loc>{url}</loc>\n'
                    f'    <changefreq>monthly</changefreq>\n'
                    f'    <priority>0.8</priority>\n'
                    f'  </url>'
                )

    for rel_path, langs in translated_pages.items():
        page_path = rel_path.replace('/index.html', '/').replace('\\', '/')
        if not page_path.startswith('/'):
            page_path = '/' + page_path
        for lang in langs:
            url = f'{BASE_URL}/{lang}{page_path}'
            if url not in sitemap:
                new_entries.append(
                    f'  <url>\n'
                    f'    <loc>{url}</loc>\n'
                    f'    <changefreq>yearly</changefreq>\n'
                    f'    <priority>0.5</priority>\n'
                    f'  </url>'
                )

    if new_entries:
        block = '\n'.join(new_entries) + '\n'
        sitemap = sitemap.replace('</urlset>', block + '\n</urlset>')

    with open(sitemap_path, 'w', encoding='utf-8') as f:
        f.write(sitemap)

    print(f'\nSitemap: {len(new_entries)} URLs added')


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='SEO HTML translator via wowaitranslate')
    parser.add_argument('--langs',    default='ru,de,fr,es',
                        help='Comma-separated language codes (default: ru,de,fr,es)')
    parser.add_argument('--page',     default=None,
                        help='Translate single page (e.g. blog/post/index.html)')
    parser.add_argument('--key',      default=None,
                        help='wowaitranslate API key (or WOWAI_API_KEY env var)')
    parser.add_argument('--dry-run',  action='store_true')
    parser.add_argument('--skip-existing', action='store_true',
                        help='Skip languages where translated file already exists')
    parser.add_argument('--base-url', default=None,
                        help='Site base URL (e.g. https://example.com)')
    args = parser.parse_args()

    if args.base_url:
        global BASE_URL
        BASE_URL = args.base_url.rstrip('/')

    api_key = (args.key
               or os.environ.get('WOWAI_API_KEY')
               or os.environ.get('GROQ_API_KEY'))
    if not api_key:
        print('ERROR: no API key. Pass --key or set WOWAI_API_KEY env var.')
        sys.exit(1)

    target_langs = [l.strip() for l in args.langs.split(',') if l.strip() in SUPPORTED_LANGS]
    if not target_langs:
        print(f'No valid languages. Supported: {", ".join(SUPPORTED_LANGS)}')
        sys.exit(1)

    # Detect source language from HTML
    source_lang = detect_source_lang(SITE)
    print(f'wowaitranslate ready. Source: {source_lang}. Target: {", ".join(target_langs)}')
    if args.dry_run:
        print('DRY RUN — no files will be written')

    # Pre-build nav/footer translation cache (saves ~30% API calls)
    nav_cache = build_nav_cache(api_key, SITE, target_langs, source_lang)

    translated_pages = {}

    if args.page:
        src = os.path.join(SITE, args.page.replace('/', os.sep))
        rel = '/' + args.page.replace('\\', '/')
        langs = translate_page(api_key, src, rel, target_langs, args.dry_run,
                               args.skip_existing, nav_cache=nav_cache,
                               source_lang=source_lang)
        if langs:
            translated_pages[rel] = langs
    else:
        for root, dirs, files in os.walk(SITE):
            dirs[:] = [d for d in dirs
                       if d not in ('scripts', 'images', 'node_modules', '.git',
                                    'web.archive.org', 'web-static.archive.org', 'gmpg.org', '_git_clone')
                       + tuple(SUPPORTED_LANGS.keys())]
            for fname in files:
                if not fname.endswith('.html'):
                    continue
                fpath = os.path.join(root, fname)
                rel   = fpath.replace(SITE, '').replace(os.sep, '/')
                langs = translate_page(api_key, fpath, rel, target_langs,
                                       args.dry_run, args.skip_existing,
                                       nav_cache=nav_cache, source_lang=source_lang)
                if langs:
                    translated_pages[rel] = langs

    if not args.dry_run and translated_pages:
        update_sitemap(translated_pages)

    total = sum(len(v) for v in translated_pages.values())
    print(f'\nDone: {len(translated_pages)} pages × languages = {total} translated files')


if __name__ == '__main__':
    main()
