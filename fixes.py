#!/usr/bin/env python3
"""
SEO fix modules — called by the bot to apply fixes to a cloned site directory.
Each function operates on the site_dir in-place.
"""
import os
import re
import sys
import subprocess
import json
import time
import calendar

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False


LANG_DIRS = [
    'ru', 'de', 'fr', 'es', 'it', 'pt', 'pl', 'nl', 'cs', 'ro', 'sv', 'tr',
    'el', 'uk', 'ko', 'zh', 'ja', 'sk', 'fi', 'ar', 'hi',
]

LANG_LOCALE = {
    'en': 'en_US', 'ru': 'ru_RU', 'de': 'de_DE', 'fr': 'fr_FR', 'es': 'es_ES',
    'it': 'it_IT', 'pt': 'pt_PT', 'pl': 'pl_PL', 'nl': 'nl_NL', 'cs': 'cs_CZ',
    'ro': 'ro_RO', 'sv': 'sv_SE', 'tr': 'tr_TR', 'el': 'el_GR', 'uk': 'uk_UA',
    'ko': 'ko_KR', 'zh': 'zh_CN', 'ja': 'ja_JP', 'sk': 'sk_SK', 'fi': 'fi_FI',
    'ar': 'ar_AR', 'hi': 'hi_IN',
}


def _get_og_locale(html: str) -> str | None:
    lang_m = re.search(r'<html[^>]*\blang=["\']([a-z]{2})', html, re.IGNORECASE)
    if lang_m:
        return LANG_LOCALE.get(lang_m.group(1).lower())
    return None
ARCHIVE_DIRS = ['web.archive.org', 'web-static.archive.org', 'gmpg.org', '_git_clone']


def run_all_fixes(site_dir: str, step_key: str, langs: list, groq_api_key: str,
                  site_domain: str = None, wowai_key: str = None,
                  progress_callback=None, source_lang: str = 'en',
                  translate_only: bool = False, stop_event=None) -> dict:
    """Dispatcher — runs a specific fix step."""
    try:
        if step_key == 'fix_archive_scripts':
            fix_archive_scripts(site_dir)
        elif step_key == 'fix_preloader':
            fix_preloader(site_dir)
        elif step_key == 'fix_h1':
            fix_h1(site_dir, langs, groq_api_key)
        elif step_key == 'fix_descriptions':
            fix_descriptions(site_dir, groq_api_key)
        elif step_key == 'fix_schema':
            fix_schema(site_dir, site_domain)
        elif step_key == 'fix_canonical':
            fix_canonical(site_dir, site_domain)
        elif step_key == 'fix_og_image':
            fix_og_image(site_dir, site_domain)
        elif step_key == 'fix_twitter_card':
            fix_twitter_card(site_dir)
        elif step_key == 'fix_cloudflare_stubs':
            fix_cloudflare_stubs(site_dir)
        elif step_key == 'fix_external_links':
            fix_external_links(site_dir, site_domain)
        elif step_key == 'fix_nofollow':
            fix_nofollow(site_dir)
        elif step_key == 'fix_robots_txt':
            fix_robots_txt(site_dir, site_domain)
        elif step_key == 'fix_sitemap':
            fix_sitemap(site_dir, langs, site_domain)
        elif step_key == 'fix_hreflang_translated':
            fix_hreflang_translated(site_dir, langs, site_domain, source_lang=source_lang)
        elif step_key == 'fix_translations':
            fix_translations(site_dir, langs, wowai_key or groq_api_key, site_domain,
                             progress_callback, translate_only=translate_only,
                             stop_event=stop_event)
        elif step_key == 'fix_internal_links':
            fix_internal_links(site_dir)
        elif step_key == 'fix_title_refresh':
            fix_title_refresh(site_dir)
        elif step_key == 'fix_lang_switcher':
            fix_lang_switcher(site_dir, source_lang=source_lang)
        elif step_key == 'fix_h2':
            fix_h2(site_dir, langs, groq_api_key)
        elif step_key == 'fix_thin_content':
            fix_thin_content(site_dir, langs, groq_api_key)
        elif step_key == 'fix_lang_descriptions':
            fix_lang_descriptions(site_dir, langs, groq_api_key)
        return {'ok': True}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def fix_archive_scripts(site_dir: str):
    """
    Remove web.archive.org injected scripts/styles and cleanup artifacts from HTML files.
    Also removes .bak files and control characters left by archive processing.
    """
    # Step 1: Delete .bak files across the whole site tree
    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if d not in ['.git', 'node_modules'] + ARCHIVE_DIRS]
        for fname in files:
            if fname.endswith('.bak'):
                try:
                    os.remove(os.path.join(root, fname))
                except OSError:
                    pass

    ARCHIVE_SCRIPT_PATTERNS = [
        # Bundle playback and wombat scripts
        r'<script[^>]*web-static\.archive\.org[^>]*>.*?</script>',
        r'<script[^>]*web-static\.archive\.org[^>]*/?>',
        # Archive CSS injections
        r'<link[^>]*web-static\.archive\.org[^>]*/?>',
        # __wm.init / __wm.wombat inline blocks
        r'<script[^>]*>\s*__wm\.(init|wombat)\(.*?</script>',
        # Wayback toolbar
        r'<!-- BEGIN WAYBACK TOOLBAR INSERT -->.*?<!-- END WAYBACK TOOLBAR INSERT -->',
    ]

    # Control characters that don't belong in HTML (keep tab=\x09, LF=\x0a, CR=\x0d)
    _CONTROL_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')

    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if d not in ['.git', 'node_modules'] + ARCHIVE_DIRS]
        for fname in files:
            if not fname.endswith(('.html', '.css')):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, encoding='utf-8', errors='ignore') as f:
                html = f.read()

            original = html

            if fname.endswith('.html'):
                # Remove control characters from HTML files
                html = _CONTROL_RE.sub('', html)

                # Remove orphan </script> tags
                html = re.sub(r'</script>(\s*\n\s*)</script>', r'</script>\1', html)

                if 'archive.org' in html:
                    for pattern in ARCHIVE_SCRIPT_PATTERNS:
                        html = re.sub(pattern, '', html, flags=re.DOTALL | re.IGNORECASE)

                    # (https://|http://|//)web.archive.org/web/TIMESTAMP.../https://domain/path
                    # → https://domain/path
                    html = re.sub(
                        r'(?:https?:)?//web\.archive\.org/web/\d{14}[a-z_]*/https?://([^\s"\'<>]+)',
                        lambda m: 'https://' + m.group(1),
                        html
                    )
                    # bare /web/TIMESTAMP.../https://domain/path (no host prefix)
                    html = re.sub(
                        r'/web/\d{14}[a-z_]*/https?://([^\s"\'<>]+)',
                        lambda m: 'https://' + m.group(1),
                        html
                    )

            # Fix archive URLs in both HTML and CSS:
            # Pattern: /web/20180820183224im_//images/foo.jpg → /images/foo.jpg
            # (no domain — used in CSS url() and some inline styles)
            if re.search(r'/web/\d{14}[a-z_]*/', html):
                html = re.sub(
                    r'(?:https?:)?//web\.archive\.org/web/\d{14}[a-z_]*/+(?:https?://[^/\s"\'<>()]+/)?',
                    '/',
                    html
                )
                html = re.sub(
                    r'/web/\d{14}[a-z_]*/+(?:https?://[^/\s"\'<>()]+/)?',
                    '/',
                    html
                )

            if html != original:
                with open(fpath, 'w', encoding='utf-8') as f:
                    f.write(html)


def fix_descriptions(site_dir: str, groq_api_key: str = None):
    """
    Generate unique page-specific descriptions via Groq for root EN pages,
    then batch-translate to all lang subdirectories.
    Also syncs og:description.
    """
    # ── Pass 1: fix root (EN) pages ───────────────────────────────────────────
    root_descs = {}  # fname → description

    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs
                   if d not in LANG_DIRS + ARCHIVE_DIRS + ['scripts', 'images', 'css', '.git', 'node_modules']]
        for fname in files:
            if not fname.endswith('.html'):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, encoding='utf-8', errors='ignore') as f:
                html = f.read()

            original = html
            new_desc = None
            if groq_api_key:
                try:
                    new_desc = _generate_description(html, groq_api_key)
                    time.sleep(0.5)  # gentle rate-limit
                except Exception:
                    pass

            if new_desc:
                html = _upsert_description(html, new_desc)
            else:
                # Fallback: trim if too long
                html = re.sub(
                    r'(<meta[^>]*name=["\']description["\'][^>]*content=")([^"]{156,})(")',
                    lambda m: m.group(1) + (m.group(2)[:152].rsplit(' ', 1)[0] + '...') + m.group(3),
                    html, flags=re.IGNORECASE
                )

            html = _sync_og_description(html)

            if html != original:
                with open(fpath, 'w', encoding='utf-8') as f:
                    f.write(html)

            # Collect the final description for lang propagation
            rel = os.path.relpath(fpath, site_dir)
            desc_m = re.search(r'<meta[^>]*name=["\']description["\'][^>]*content="([^"]+)"',
                               html, re.IGNORECASE)
            if desc_m:
                root_descs[rel] = desc_m.group(1).strip()

    # ── Pass 2: propagate to lang directories ─────────────────────────────────
    if groq_api_key and root_descs:
        fix_lang_descriptions(site_dir, LANG_DIRS, groq_api_key, root_descs=root_descs)


def _upsert_description(html: str, desc: str) -> str:
    """Insert or replace meta description (no apostrophe escaping in double-quoted attrs)."""
    new_meta = f'<meta name="description" content="{desc}">'
    if re.search(r'<meta[^>]*name=["\']description["\']', html, re.IGNORECASE):
        return re.sub(
            r'<meta[^>]*name=["\']description["\'][^>]*>',
            new_meta, html, flags=re.IGNORECASE, count=1
        )
    head_close = html.find('</head>')
    if head_close >= 0:
        return html[:head_close] + new_meta + '\n' + html[head_close:]
    return html


def _sync_og_description(html: str) -> str:
    """Sync og:description to match meta description."""
    desc_m = re.search(r'<meta[^>]*name=["\']description["\'][^>]*content="([^"]+)"',
                       html, re.IGNORECASE)
    if not desc_m:
        return html
    desc_val = desc_m.group(1)
    new_og = f'<meta property="og:description" content="{desc_val}">'
    if re.search(r'og:description', html):
        return re.sub(
            r'<meta[^>]*property=["\']og:description["\'][^>]*>',
            new_og, html, flags=re.IGNORECASE, count=1
        )
    head_close = html.find('</head>')
    if head_close >= 0:
        return html[:head_close] + new_og + '\n' + html[head_close:]
    return html


def fix_lang_descriptions(site_dir: str, langs: list, groq_api_key: str,
                          root_descs: dict = None) -> None:
    """
    Translate root EN descriptions to all lang subdirs using batch Groq calls.
    root_descs: {relative_path → description} — collected from root pass.
                If None, reads from root files directly.
    """
    if root_descs is None:
        root_descs = {}
        for fname in os.listdir(site_dir):
            if not fname.endswith('.html'):
                continue
            fpath = os.path.join(site_dir, fname)
            try:
                html = open(fpath, encoding='utf-8', errors='ignore').read()
                m = re.search(r'<meta[^>]*name=["\']description["\'][^>]*content="([^"]+)"',
                              html, re.IGNORECASE)
                if m and 50 <= len(m.group(1)) <= 160:
                    root_descs[fname] = m.group(1).strip()
            except Exception:
                pass

    if not root_descs:
        return

    # Get unique descriptions to translate
    unique_descs = list(dict.fromkeys(root_descs.values()))  # deduplicated, order preserved
    desc_to_translation = {}  # en_desc → {lang → translated}

    for lang in langs:
        lang_dir = os.path.join(site_dir, lang)
        if not os.path.isdir(lang_dir):
            continue
        lang_name = _GROQ_LANG_NAMES.get(lang, lang)

        # Batch translate all unique descriptions at once
        try:
            translated = _groq_translate_batch(groq_api_key, unique_descs, lang_name)
            for en_desc, tr_desc in zip(unique_descs, translated):
                desc_to_translation.setdefault(en_desc, {})[lang] = tr_desc
            time.sleep(1.5)
        except Exception:
            continue

        # Apply to lang files
        for rel_path, en_desc in root_descs.items():
            lang_fpath = os.path.join(lang_dir, os.path.basename(rel_path))
            if not os.path.exists(lang_fpath):
                continue
            tr_desc = desc_to_translation.get(en_desc, {}).get(lang, en_desc)
            # Clamp to 160 chars
            if len(tr_desc) > 160:
                tr_desc = tr_desc[:157].rsplit(' ', 1)[0] + '...'
            if len(tr_desc) < 30:
                tr_desc = en_desc
            try:
                html = open(lang_fpath, encoding='utf-8', errors='ignore').read()
                # Only update if missing or bad length
                existing_m = re.search(
                    r'<meta[^>]*name=["\']description["\'][^>]*content="([^"]+)"', html, re.IGNORECASE)
                existing = existing_m.group(1).strip() if existing_m else ''
                if existing and 50 <= len(existing) <= 160:
                    continue
                html = _upsert_description(html, tr_desc)
                html = _sync_og_description(html)
                with open(lang_fpath, 'w', encoding='utf-8') as f:
                    f.write(html)
            except Exception:
                pass


def _find_content_insert_point(html: str) -> int | None:
    """
    Find the character position right after the opening tag of the main content area.
    Tries semantic and id/class-based markers, then falls back to after </header> or </nav>.
    Returns None if no suitable point found.
    """
    # Priority order: semantic tags first, then common id/class patterns (no closing > needed)
    MARKERS = [
        '<main',
        '<article',
        '<div class="entry-content"',
        '<div class="page-content"',
        '<div class="post-content"',
        '<div class="site-content"',
        '<div id="content"',
        '<div id="main"',
        '<div id="main-content"',
        '<div class="content"',
        '<div class="main"',
        '<div class="container"',
        '<div id="wrapper"',
        '<section',
    ]
    for marker in MARKERS:
        pos = html.find(marker)
        if pos >= 0:
            return html.find('>', pos) + 1

    # Fallback: insert after </header> or </nav>
    for end_tag in ('</header>', '</nav>'):
        pos = html.find(end_tag)
        if pos >= 0:
            return pos + len(end_tag)

    return None


def fix_h1(site_dir: str, langs: list, groq_api_key: str = None) -> None:
    """
    Add a page-specific H1 to pages that lack one (or only have a site-logo H1).

    WordPress themes often put an H1 on the site logo (class="site-title") — that's
    not a content H1. We detect that case and inject a proper H1 at the top of the
    main content area, generated from the page <title> + content snippet via Groq.
    Falls back to the page title slug when Groq is unavailable.
    """
    SKIP = set(LANG_DIRS + ARCHIVE_DIRS + ['scripts', 'images', 'css', '.git', 'node_modules'])
    SITE_TITLE_RE = re.compile(r'<h1[^>]*class=["\'][^"\']*site-title[^"\']*["\'][^>]*>', re.IGNORECASE)
    CONTENT_H1_RE = re.compile(r'<h1(?![^>]*class=["\'][^"\']*site-title)[^>]*>(.+?)</h1>', re.IGNORECASE | re.DOTALL)

    pages_fixed = {}  # rel → h1_text (EN)

    # ── Pass 1: fix root EN pages ──────────────────────────────────────────────
    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if os.path.basename(d) not in SKIP]
        for fname in files:
            if not fname.endswith('.html'):
                continue
            fpath = os.path.join(root, fname)
            html = open(fpath, encoding='utf-8', errors='ignore').read()

            # Skip if page already has a real content H1 (not site-title)
            if CONTENT_H1_RE.search(html):
                continue

            # Find insertion point: top of main content area
            insert_after = _find_content_insert_point(html)
            if insert_after is None:
                continue

            # Get page title for context
            title_m = re.search(r'<title>([^<]+)</title>', html)
            title = re.sub(r'\s*[|\-–—].*$', '', title_m.group(1)).strip() if title_m else ''
            if not title:
                continue

            # Build content snippet for Groq
            body_m = re.search(r'<body[^>]*>(.*)', html, re.DOTALL)
            body_text = re.sub(r'<[^>]+>', ' ', body_m.group(1) if body_m else html)
            body_text = re.sub(r'\s+', ' ', body_text).strip()
            snippet = body_text[:300]

            # Generate H1 text
            h1_text = _make_h1_text(title, snippet, groq_api_key)
            if not h1_text:
                continue

            # Inject H1
            html = html[:insert_after] + f'\n<h1 class="entry-h1">{h1_text}</h1>' + html[insert_after:]
            with open(fpath, 'w', encoding='utf-8') as f:
                f.write(html)

            rel = os.path.relpath(fpath, site_dir)
            pages_fixed[rel] = h1_text

    if not pages_fixed or not groq_api_key:
        return

    # ── Pass 2: translate H1 to lang versions ─────────────────────────────────
    unique_h1s = list(dict.fromkeys(pages_fixed.values()))

    for lang in langs:
        lang_dir = os.path.join(site_dir, lang)
        if not os.path.isdir(lang_dir):
            continue
        lang_name = _GROQ_LANG_NAMES.get(lang, lang)

        try:
            translated = _groq_translate_batch(groq_api_key, unique_h1s, lang_name)
            h1_map = dict(zip(unique_h1s, translated))
            time.sleep(1.5)
        except Exception:
            continue

        for rel, en_h1 in pages_fixed.items():
            lang_fpath = os.path.join(lang_dir, os.path.basename(rel))
            if not os.path.exists(lang_fpath):
                continue
            tr_h1 = h1_map.get(en_h1, en_h1)
            try:
                lhtml = open(lang_fpath, encoding='utf-8', errors='ignore').read()
                if CONTENT_H1_RE.search(lhtml):
                    continue
                insert_after = _find_content_insert_point(lhtml)
                if insert_after is None:
                    continue
                lhtml = lhtml[:insert_after] + f'\n<h1 class="entry-h1">{tr_h1}</h1>' + lhtml[insert_after:]
                with open(lang_fpath, 'w', encoding='utf-8') as f:
                    f.write(lhtml)
            except Exception:
                pass


def _generate_seo_content(page_name: str, site_name: str, context: str,
                          groq_api_key: str = None) -> str:
    """Generate 2-3 supplementary paragraphs for a thin page via Groq, or use a fallback."""
    if groq_api_key and context:
        prompt = (
            f'Write 2-3 short paragraphs (total ~150 words) to supplement this webpage.\n'
            f'Page: "{page_name}" on site "{site_name}"\n'
            f'Existing content excerpt: {context}\n'
            f'Requirements: plain HTML <p> tags only, relevant to the page topic, '
            f'natural reading flow, no markdown, no headings.'
        )
        try:
            result = _groq_call(groq_api_key, [{'role': 'user', 'content': prompt}],
                                max_tokens=300, temperature=0.7)
            result = result.strip()
            if len(result) > 80:
                if not result.startswith('<p'):
                    result = '\n'.join(
                        f'<p>{p.strip()}</p>'
                        for p in re.split(r'\n{2,}', result)
                        if p.strip()
                    )
                return result
        except Exception:
            pass
    # Fallback generic template
    return (
        f'<p>Welcome to {site_name}. Explore our {page_name.lower()} section '
        f'to discover more about what we offer.</p>\n'
        f'<p>We are committed to quality and customer satisfaction. '
        f'Feel free to browse our pages or contact us with any questions.</p>'
    )


def _make_h1_text(title: str, snippet: str, groq_api_key: str = None) -> str:
    """Generate an H1 from page title + content snippet via Groq, or fall back to title."""
    if groq_api_key and snippet:
        prompt = (
            f'Write a concise H1 heading (5-10 words) for this webpage.\n'
            f'Page title: "{title}"\n'
            f'Content excerpt: {snippet}\n'
            f'Requirements: plain text only, no markdown, no quotes, no punctuation at end, '
            f'must be more specific than just the site name.'
        )
        try:
            result = _groq_call(groq_api_key, [{'role': 'user', 'content': prompt}],
                                max_tokens=25, temperature=0.3)
            result = result.strip('"\'').strip()
            if 5 < len(result) < 100:
                return result
        except Exception:
            pass
    # Fallback: use the page title directly
    return title if title else ''


def fix_h2(site_dir: str, langs: list, groq_api_key: str = None) -> None:
    """
    Insert H2 headings in pages that lack them (posts, static pages).
    Generates H2 from H1 + content snippet, then translates for lang versions.
    """
    SKIP = set(LANG_DIRS + ARCHIVE_DIRS + ['scripts', 'images', 'css', '.git', 'node_modules'])

    pages_fixed = {}  # fname → h2_text (EN)

    # ── Pass 1: fix root EN pages ─────────────────────────────────────────────
    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if os.path.basename(d) not in SKIP]
        for fname in files:
            if not fname.endswith('.html'):
                continue
            fpath = os.path.join(root, fname)
            html = open(fpath, encoding='utf-8', errors='ignore').read()

            # Check if H2 is already in entry-content area
            ec_start = html.find('<div class="entry-content">')
            if ec_start < 0:
                ec_start = html.find('<main')
                if ec_start < 0:
                    ec_start = html.find('<article')
            if ec_start < 0:
                continue

            window = html[ec_start:ec_start + 600]
            if re.search(r'<h2[^>]*class=["\']entry-heading', window, re.IGNORECASE):
                continue  # already has our H2
            if re.search(r'<h2[^>]*>', window, re.IGNORECASE):
                continue  # already has H2

            # Generate H2 text
            h2_text = _make_h2_text(html, groq_api_key)
            if not h2_text:
                continue

            # Insert H2 right after entry-content opening tag
            insert_pos = html.find('>', ec_start) + 1
            html = html[:insert_pos] + f'\n<h2 class="entry-heading">{h2_text}</h2>' + html[insert_pos:]

            with open(fpath, 'w', encoding='utf-8') as f:
                f.write(html)

            rel = os.path.relpath(fpath, site_dir)
            pages_fixed[rel] = h2_text

    if not pages_fixed or not groq_api_key:
        return

    # ── Pass 2: translate H2 to lang versions ────────────────────────────────
    unique_h2s = list(dict.fromkeys(pages_fixed.values()))

    for lang in langs:
        lang_dir = os.path.join(site_dir, lang)
        if not os.path.isdir(lang_dir):
            continue
        lang_name = _GROQ_LANG_NAMES.get(lang, lang)

        try:
            translated_h2s = _groq_translate_batch(groq_api_key, unique_h2s, lang_name)
            h2_map = dict(zip(unique_h2s, translated_h2s))
            time.sleep(1.5)
        except Exception:
            continue

        for rel, en_h2 in pages_fixed.items():
            lang_fpath = os.path.join(lang_dir, os.path.basename(rel))
            if not os.path.exists(lang_fpath):
                continue
            tr_h2 = h2_map.get(en_h2, en_h2)
            try:
                html = open(lang_fpath, encoding='utf-8', errors='ignore').read()
                if 'entry-heading' in html:
                    continue
                ec_start = html.find('<div class="entry-content">')
                if ec_start < 0:
                    ec_start = html.find('<main')
                if ec_start < 0:
                    continue
                if re.search(r'<h2[^>]*>', html[ec_start:ec_start+600], re.IGNORECASE):
                    continue
                insert_pos = html.find('>', ec_start) + 1
                html = html[:insert_pos] + f'\n<h2 class="entry-heading">{tr_h2}</h2>' + html[insert_pos:]
                with open(lang_fpath, 'w', encoding='utf-8') as f:
                    f.write(html)
            except Exception:
                pass


def _make_h2_text(html: str, groq_api_key: str = None) -> str:
    """Generate a short H2 heading from page content."""
    h1_m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.IGNORECASE | re.DOTALL)
    h1 = re.sub(r'<[^>]+>', '', h1_m.group(1)).strip() if h1_m else ''

    # Archive pages: derive from H1 "Monthly Archives: Month Year"
    arch_m = re.search(r'(?:Monthly\s+)?Archives?:\s+(.+)', h1, re.IGNORECASE)
    if arch_m:
        return f'Posts from {arch_m.group(1).strip()}'

    # Use Groq if available
    if groq_api_key and h1:
        ec_start = html.find('<div class="entry-content">')
        if ec_start < 0:
            ec_start = html.find('<article')
        snippet = ''
        if ec_start >= 0:
            chunk = html[ec_start:ec_start + 2000]
            chunk = re.sub(r'<[^>]+>', ' ', chunk)
            chunk = re.sub(r'\s+', ' ', chunk).strip()
            words = chunk.split()
            snippet = ' '.join(words[:60])

        if snippet:
            prompt = (
                f'Write a short H2 subheading (4-8 words) for this page.\n'
                f'Page title/H1: "{h1}"\n'
                f'Content: {snippet}\n'
                f'Requirements: plain text only, no markdown, no quotes, no punctuation at end.'
            )
            try:
                result = _groq_call(groq_api_key, [{'role': 'user', 'content': prompt}],
                                    max_tokens=30, temperature=0.4)
                result = result.strip('"\'').strip()
                if 3 < len(result) < 80:
                    return result
            except Exception:
                pass

    # Fallback: derive from H1
    if not h1:
        return ''
    # Page-type fallbacks
    if re.search(r'\b(album|ep|single)\b', h1, re.IGNORECASE):
        return 'About the Album'
    if re.search(r'\bnovel\b', h1, re.IGNORECASE):
        return 'About the Novel'
    if re.search(r'\bcontact\b', h1, re.IGNORECASE):
        return 'Get in Touch'
    if re.search(r'\bnews\b', h1, re.IGNORECASE):
        return 'Latest News'
    if re.search(r'\bstore\b|\bshop\b', h1, re.IGNORECASE):
        return 'Shop Music and Books'
    if re.search(r'\bmusic\b|\bdiscograph', h1, re.IGNORECASE):
        return 'Albums and Music'
    if re.search(r'\bbio\b|\babout\b', h1, re.IGNORECASE):
        return 'About the Artist'
    return ''


def fix_thin_content(site_dir: str, langs: list, groq_api_key: str = None) -> None:
    """
    Add content to pages with thin content (<200 words).

    Two branches:
    1. WordPress archive pages (H1 contains "Archives:") — inserts hardcoded intro/outro.
    2. Generic thin pages (any site) — generates contextual content via Groq,
       falls back to a simple template.
    """
    from audit import _count_words

    SKIP = set(LANG_DIRS + ARCHIVE_DIRS + ['scripts', 'images', 'css', '.git', 'node_modules'])
    INTRO_MARKER  = 'class="archive-intro"'
    GENERIC_MARKER = 'class="seo-extra"'

    INTRO_EN = ('<p class="archive-intro">{site_name} publishes personal blog posts about music, '
                'creativity, and daily life. Browse the entries below from this archive period — '
                'each one a direct, intimate window into the artist\'s thoughts and experiences.</p>')
    OUTRO_EN = ('<p class="archive-outro">Explore more posts in other archive sections, '
                'or visit the main blog for the latest updates.</p>')

    pages_added  = {}  # rel → (intro_text, outro_text)   — WordPress branch
    generic_added = {}  # rel → block_html                 — generic branch

    # ── Pass 1: walk EN pages ─────────────────────────────────────────────────
    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if os.path.basename(d) not in SKIP]
        for fname in files:
            if not fname.endswith('.html'):
                continue
            fpath = os.path.join(root, fname)
            html  = open(fpath, encoding='utf-8', errors='ignore').read()

            if INTRO_MARKER in html or GENERIC_MARKER in html:
                continue  # already patched

            if _count_words(html) >= 200:
                continue

            title_m   = re.search(r'<title>([^<]+)</title>', html)
            title     = title_m.group(1).strip() if title_m else ''
            site_name = re.split(r'\s*[|»:–—]\s*', title)[-1].strip() if title else 'This site'
            page_name = re.split(r'\s*[|»:–—]\s*', title)[0].strip() if title else fname

            h1_m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.IGNORECASE | re.DOTALL)
            h1   = re.sub(r'<[^>]+>', '', h1_m.group(1)).strip() if h1_m else ''

            # ── Branch A: WordPress archive ───────────────────────────────────
            if re.search(r'[Aa]rchives?:', h1):
                intro = INTRO_EN.format(site_name=site_name)
                outro = OUTRO_EN

                h1_end = re.search(r'</h1>', html, re.IGNORECASE)
                if not h1_end:
                    continue
                html = html[:h1_end.end()] + '\n' + intro + html[h1_end.end():]
                nav_m = re.search(r'<nav[^>]*class="[^"]*navigation[^"]*"', html, re.IGNORECASE)
                if nav_m:
                    html = html[:nav_m.start()] + outro + '\n' + html[nav_m.start():]

                with open(fpath, 'w', encoding='utf-8') as f:
                    f.write(html)
                pages_added[os.path.relpath(fpath, site_dir)] = (intro, outro)

            # ── Branch B: generic thin page ───────────────────────────────────
            else:
                body_m    = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL | re.IGNORECASE)
                body_text = re.sub(r'<[^>]+>', ' ', body_m.group(1) if body_m else html)
                body_text = re.sub(r'\s+', ' ', body_text).strip()[:400]

                content = _generate_seo_content(page_name, site_name, body_text, groq_api_key)
                block   = f'<div class="seo-extra">\n{content}\n</div>'

                # Insert before </main>, </article>, or </body>
                inserted = False
                for end_tag in ('</main>', '</article>', '</body>'):
                    pos = html.lower().rfind(end_tag)
                    if pos >= 0:
                        html = html[:pos] + block + '\n' + html[pos:]
                        inserted = True
                        break
                if not inserted:
                    html = html + '\n' + block

                with open(fpath, 'w', encoding='utf-8') as f:
                    f.write(html)
                generic_added[os.path.relpath(fpath, site_dir)] = content

    # ── Pass 2: translate WordPress archive pages ─────────────────────────────
    if pages_added and groq_api_key:
        unique_intros = list(dict.fromkeys(i for i, _ in pages_added.values()))
        unique_outros = list(dict.fromkeys(o for _, o in pages_added.values()))

        for lang in langs:
            lang_dir = os.path.join(site_dir, lang)
            if not os.path.isdir(lang_dir):
                continue
            lang_name = _GROQ_LANG_NAMES.get(lang, lang)
            try:
                all_texts   = unique_intros + unique_outros
                all_tr      = _groq_translate_batch(groq_api_key, all_texts, lang_name)
                intro_map   = dict(zip(unique_intros, all_tr[:len(unique_intros)]))
                outro_map   = dict(zip(unique_outros, all_tr[len(unique_intros):]))
                time.sleep(1.5)
            except Exception:
                continue

            for rel, (en_intro, en_outro) in pages_added.items():
                lang_fpath = os.path.join(lang_dir, os.path.basename(rel))
                if not os.path.exists(lang_fpath):
                    continue
                try:
                    lhtml = open(lang_fpath, encoding='utf-8', errors='ignore').read()
                    if INTRO_MARKER in lhtml:
                        continue
                    tr_intro = intro_map.get(en_intro, en_intro)
                    tr_outro = outro_map.get(en_outro, en_outro)
                    h1_end   = re.search(r'</h1>', lhtml, re.IGNORECASE)
                    if not h1_end:
                        continue
                    lhtml = lhtml[:h1_end.end()] + '\n' + tr_intro + lhtml[h1_end.end():]
                    nav_m = re.search(r'<nav[^>]*class="[^"]*navigation[^"]*"', lhtml, re.IGNORECASE)
                    if nav_m:
                        lhtml = lhtml[:nav_m.start()] + tr_outro + '\n' + lhtml[nav_m.start():]
                    with open(lang_fpath, 'w', encoding='utf-8') as f:
                        f.write(lhtml)
                except Exception:
                    pass

    # ── Pass 3: translate generic thin pages ──────────────────────────────────
    if generic_added and groq_api_key:
        unique_blocks = list(dict.fromkeys(generic_added.values()))

        for lang in langs:
            lang_dir = os.path.join(site_dir, lang)
            if not os.path.isdir(lang_dir):
                continue
            lang_name = _GROQ_LANG_NAMES.get(lang, lang)
            try:
                translated   = _groq_translate_batch(groq_api_key, unique_blocks, lang_name)
                block_map    = dict(zip(unique_blocks, translated))
                time.sleep(1.5)
            except Exception:
                continue

            for rel, en_block in generic_added.items():
                lang_fpath = os.path.join(lang_dir, os.path.basename(rel))
                if not os.path.exists(lang_fpath):
                    continue
                try:
                    lhtml = open(lang_fpath, encoding='utf-8', errors='ignore').read()
                    if GENERIC_MARKER in lhtml:
                        continue
                    tr_block = block_map.get(en_block, en_block)
                    tr_div   = f'<div class="seo-extra">\n{tr_block}\n</div>'
                    inserted = False
                    for end_tag in ('</main>', '</article>', '</body>'):
                        pos = lhtml.lower().rfind(end_tag)
                        if pos >= 0:
                            lhtml = lhtml[:pos] + tr_div + '\n' + lhtml[pos:]
                            inserted = True
                            break
                    if not inserted:
                        lhtml = lhtml + '\n' + tr_div
                    with open(lang_fpath, 'w', encoding='utf-8') as f:
                        f.write(lhtml)
                except Exception:
                    pass


def _groq_call(groq_api_key: str, messages: list, max_tokens: int = 80,
               temperature: float = 0.7, retries: int = 4) -> str:
    """Call Groq API with exponential backoff on 429. Uses requests if available."""
    payload = {
        'model': 'llama-3.1-8b-instant',
        'messages': messages,
        'max_tokens': max_tokens,
        'temperature': temperature,
    }
    headers = {
        'Authorization': f'Bearer {groq_api_key}',
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0',
    }
    url = 'https://api.groq.com/openai/v1/chat/completions'
    delay = 3

    for attempt in range(retries):
        try:
            if _HAS_REQUESTS:
                r = _requests.post(url, headers=headers, json=payload, timeout=30)
                if r.status_code == 429:
                    time.sleep(delay * (2 ** attempt))
                    continue
                r.raise_for_status()
                return r.json()['choices'][0]['message']['content'].strip()
            else:
                import urllib.request
                req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read())['choices'][0]['message']['content'].strip()
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(delay * (2 ** attempt))
    raise RuntimeError('Groq: max retries exceeded')


def _groq_translate_batch(groq_api_key: str, texts: list, lang_name: str) -> list:
    """Translate a list of texts to lang_name in one Groq call. Returns list same length."""
    numbered = '\n'.join(f'{i+1}. {t}' for i, t in enumerate(texts))
    system = (f'Translate the following items to {lang_name}. '
              f'Return ONLY the translations, numbered the same way, one per line. '
              f'Keep each item under 160 characters. No extra text.')
    result = _groq_call(groq_api_key,
                        [{'role': 'system', 'content': system},
                         {'role': 'user', 'content': numbered}],
                        max_tokens=4000, temperature=0.1)
    out = []
    for line in result.split('\n'):
        line = re.sub(r'^\d+[.)]\s*', '', line.strip())
        if line:
            out.append(line)
    while len(out) < len(texts):
        out.append(texts[len(out)])
    return out[:len(texts)]


_GROQ_LANG_NAMES = {
    'ar': 'Arabic', 'cs': 'Czech', 'de': 'German', 'el': 'Greek',
    'es': 'Spanish', 'fi': 'Finnish', 'fr': 'French', 'hi': 'Hindi',
    'it': 'Italian', 'ja': 'Japanese', 'ko': 'Korean', 'nl': 'Dutch',
    'pl': 'Polish', 'pt': 'Portuguese', 'ro': 'Romanian', 'ru': 'Russian',
    'sk': 'Slovak', 'sv': 'Swedish', 'tr': 'Turkish', 'uk': 'Ukrainian',
    'zh': 'Chinese',
}


def _generate_description(html: str, groq_api_key: str) -> str | None:
    """Use Groq to generate a unique 120-155 char page description."""
    # Extract page text
    title_m = re.search(r'<title>([^<]+)</title>', html)
    title = title_m.group(1).strip() if title_m else ''

    # Get main content text
    body_m = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL | re.IGNORECASE)
    if body_m:
        text = re.sub(r'<(script|style)[^>]*>.*?</(script|style)>', '', body_m.group(1), flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        text = text[:800]
    else:
        text = ''

    if not text or len(text) < 50:
        return None

    # Check if description already exists and is good
    desc_m = re.search(r'<meta[^>]*name=["\']description["\'][^>]*content="([^"]+)"', html, re.IGNORECASE)
    existing = desc_m.group(1).strip() if desc_m else ''
    if existing and 50 <= len(existing) <= 155:
        return None  # Already fine — skip Groq call

    # Extract main keyword from title
    title_clean = re.split(r'\s+[|:–—]\s+', title)[0].strip()
    stop = {'the','a','an','in','on','at','for','to','of','and','or','is','are',
            'was','were','this','that','with','from','by','as','its','it','be'}
    kw_words = [w for w in title_clean.lower().split() if w not in stop and len(w) > 2]
    main_keyword = ' '.join(kw_words[:4]) if kw_words else title_clean

    prompt = (
        f'Write a compelling meta description for this webpage.\n'
        f'Requirements:\n'
        f'- Length: 120-155 characters (count carefully)\n'
        f'- Naturally include this keyword: "{main_keyword}"\n'
        f'- End with a call-to-action (e.g. "Learn more", "Find out", "Discover")\n'
        f'- No quotes, no markdown, no bullet points — plain text only\n\n'
        f'Page title: {title}\n'
        f'Page content: {text}'
    )

    result = _groq_call(groq_api_key, [{'role': 'user', 'content': prompt}],
                        max_tokens=80, temperature=0.7)
    result = result.strip('"').strip("'")

    if existing and result.lower()[:50] == existing.lower()[:50]:
        return None
    if len(result) < 50:
        return None
    if len(result) > 155:
        result = result[:152].rsplit(' ', 1)[0].rstrip('.,;') + '...'

    return result


def fix_schema(site_dir: str, site_domain: str = None):
    """Add BreadcrumbList schema to pages that don't have it."""
    BASE_URL = _detect_base_url(site_dir, site_domain)
    is_placeholder = (BASE_URL == 'https://example.com')

    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs
                   if d not in LANG_DIRS + ARCHIVE_DIRS + ['scripts', 'images', 'css', '.git', 'node_modules']]
        for fname in files:
            if not fname.endswith('.html'):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, encoding='utf-8', errors='ignore') as f:
                html = f.read()

            if 'BreadcrumbList' in html:
                if is_placeholder:
                    continue
                # Replace any wrong domain in existing BreadcrumbList JSON-LD
                changed = False
                def _fix_schema_domain(m):
                    nonlocal changed
                    block = m.group(0)
                    # Replace any https://... domain that isn't the correct one
                    fixed = re.sub(
                        r'https://(?!schema\.org)[^/\\"]+',
                        lambda dm: BASE_URL if dm.group(0) != BASE_URL else dm.group(0),
                        block
                    )
                    if fixed != block:
                        changed = True
                    return fixed
                html = re.sub(
                    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>.*?</script>',
                    _fix_schema_domain, html, flags=re.DOTALL
                )
                if changed:
                    with open(fpath, 'w', encoding='utf-8') as f:
                        f.write(html)
                continue

            if re.search(r'noindex', html):
                continue

            rel = fpath.replace(site_dir, '').replace(os.sep, '/').lstrip('/')
            page_path = '/' + rel.replace('index.html', '').rstrip('/')

            parts = [p for p in page_path.strip('/').split('/') if p]
            items = [{'pos': 1, 'name': 'Home', 'url': BASE_URL + '/'}]
            for i, part in enumerate(parts[:-1], 2):
                items.append({
                    'pos': i,
                    'name': part.replace('-', ' ').title(),
                    'url': BASE_URL + '/' + '/'.join(parts[:i-1]) + '/'
                })

            title_m = re.search(r'<title>([^<]+)</title>', html)
            page_name = title_m.group(1).split('—')[0].strip() if title_m else parts[-1] if parts else 'Home'
            items.append({'pos': len(items) + 1, 'name': page_name})

            list_items = []
            for item in items:
                if 'url' in item:
                    list_items.append(
                        f'{{"@type":"ListItem","position":{item["pos"]},'
                        f'"name":"{item["name"]}","item":"{item["url"]}"}}'
                    )
                else:
                    list_items.append(
                        f'{{"@type":"ListItem","position":{item["pos"]},"name":"{item["name"]}"}}'
                    )

            schema = (
                '\n<script type="application/ld+json">\n'
                '{"@context":"https://schema.org","@type":"BreadcrumbList",'
                '"itemListElement":[' + ','.join(list_items) + ']}\n'
                '</script>'
            )

            html = html.replace('</head>', schema + '\n</head>', 1)
            with open(fpath, 'w', encoding='utf-8') as f:
                f.write(html)


def fix_canonical(site_dir: str, site_domain: str = None):
    """
    Add or upgrade <link rel="canonical"> on every HTML page.
    - If missing: adds absolute canonical.
    - If relative (e.g. href="/about/"): upgrades to absolute when domain is known.
    - If already absolute with correct domain: skips.
    """
    BASE_URL = _detect_base_url(site_dir, site_domain)
    is_placeholder = (BASE_URL == 'https://example.com')

    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if d not in ['.git', 'node_modules'] + ARCHIVE_DIRS]
        for fname in files:
            if not fname.endswith('.html'):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, encoding='utf-8', errors='ignore') as f:
                html = f.read()

            rel = fpath.replace(site_dir, '').replace(os.sep, '/').lstrip('/')
            url_path = '/' + rel
            if url_path.endswith('/index.html'):
                url_path = url_path[:-len('index.html')]
            elif url_path.endswith('.html'):
                url_path = url_path[:-len('.html')] + '/'

            canonical_url = BASE_URL.rstrip('/') + url_path

            existing = re.search(
                r'<link[^>]*rel=["\']canonical["\'][^>]*href=["\']([^"\']*)["\']',
                html, re.IGNORECASE
            )

            if existing:
                current_href = existing.group(1)
                # Already absolute with non-placeholder domain — leave it
                if current_href.startswith('http') and 'example.com' not in current_href:
                    continue
                # Relative or placeholder — upgrade if we have a real domain
                if is_placeholder:
                    continue
                html = re.sub(
                    r'<link([^>]*)rel=["\']canonical["\']([^>]*)href=["\'][^"\']*["\']',
                    f'<link\\1rel="canonical"\\2href="{canonical_url}"',
                    html, flags=re.IGNORECASE
                )
            else:
                if is_placeholder:
                    # Add relative canonical as fallback
                    tag = f'<link rel="canonical" href="{url_path}">\n'
                else:
                    tag = f'<link rel="canonical" href="{canonical_url}">\n'
                html = html.replace('</head>', tag + '</head>', 1)

            with open(fpath, 'w', encoding='utf-8') as f:
                f.write(html)


def fix_og_image(site_dir: str, site_domain: str = None):
    """Add/normalize full OG markup on all pages."""
    from urllib.parse import urlparse
    BASE_URL = _detect_base_url(site_dir, site_domain)
    site_name = urlparse(BASE_URL).netloc.lstrip('www.') if BASE_URL else None
    fallback_img = _find_fallback_og_image(site_dir, BASE_URL)

    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if d not in ['.git', 'node_modules'] + ARCHIVE_DIRS]
        for fname in files:
            if not fname.endswith('.html'):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, encoding='utf-8', errors='ignore') as f:
                html = f.read()
            original_html = html

            # Normalize existing relative og:image URLs to absolute
            def _make_og_absolute(m):
                src = m.group(1)
                if src.startswith('http'):
                    return m.group(0)
                abs_src = (BASE_URL.rstrip('/') + src) if src.startswith('/') else (BASE_URL.rstrip('/') + '/' + src)
                return m.group(0).replace(src, abs_src)

            html = re.sub(
                r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']',
                _make_og_absolute, html, flags=re.IGNORECASE
            )
            html = re.sub(
                r'<meta[^>]*content=["\']([^"\']+)["\'][^>]*property=["\']og:image["\']',
                _make_og_absolute, html, flags=re.IGNORECASE
            )

            # Add og:image if missing
            if not re.search(r'og:image', html):
                img_url = None
                for img_m in re.finditer(r'<img[^>]+src=["\']([^"\']+\.(jpg|jpeg|png|webp|gif))["\']', html, re.IGNORECASE):
                    src = img_m.group(1)
                    if _is_bad_og_image(src):
                        continue
                    if src.startswith('http'):
                        img_url = src
                    elif src.startswith('/'):
                        img_url = BASE_URL.rstrip('/') + src
                    else:
                        img_url = BASE_URL.rstrip('/') + '/' + src
                    break
                if not img_url and fallback_img:
                    img_url = fallback_img
                if img_url:
                    html = html.replace('</head>', f'<meta property="og:image" content="{img_url}">\n</head>', 1)

            # Add missing og:title
            if not re.search(r'og:title', html):
                title_m = re.search(r'<title>([^<]+)</title>', html)
                if title_m:
                    html = html.replace('</head>', f'<meta property="og:title" content="{title_m.group(1).strip()}">\n</head>', 1)

            # Add missing og:url
            if not re.search(r'og:url', html):
                canon_m = re.search(r'<link[^>]*rel=["\']canonical["\'][^>]*href=["\']([^"\']+)["\']', html, re.IGNORECASE)
                if canon_m:
                    html = html.replace('</head>', f'<meta property="og:url" content="{canon_m.group(1)}">\n</head>', 1)

            # Add missing og:type
            if not re.search(r'og:type', html):
                html = html.replace('</head>', '<meta property="og:type" content="website">\n</head>', 1)

            # Add missing og:site_name
            if site_name and not re.search(r'og:site_name', html):
                html = html.replace('</head>', f'<meta property="og:site_name" content="{site_name}">\n</head>', 1)

            # Add missing og:locale
            if not re.search(r'og:locale', html):
                locale = _get_og_locale(html)
                if locale:
                    html = html.replace('</head>', f'<meta property="og:locale" content="{locale}">\n</head>', 1)

            if html != original_html:
                with open(fpath, 'w', encoding='utf-8') as f:
                    f.write(html)


_BAD_OG_IMAGE_RE = re.compile(
    r'(spacer|blank|pixel|1x1|transparent|clear|placeholder|dummy|example\.com'
    r'|favicon|\.ico$)',
    re.IGNORECASE
)

def _is_bad_og_image(src: str) -> bool:
    """Return True if the image URL is unsuitable for og:image (spacer, pixel, icon, etc.)."""
    name = src.split('/')[-1].split('?')[0].lower()
    # Reject known junk filenames
    if _BAD_OG_IMAGE_RE.search(src):
        return True
    # Reject GIFs that aren't from a real photo path (GIFs used for spacers are common)
    if name.endswith('.gif'):
        return True
    return False


def _find_fallback_og_image(site_dir: str, base_url: str) -> str | None:
    """Find a suitable image in the site to use as fallback og:image (skips spacers/icons)."""
    import glob
    for ext in ('*.jpg', '*.jpeg', '*.png', '*.webp'):
        imgs = glob.glob(os.path.join(site_dir, '**', ext), recursive=True)
        # Filter out web.archive dirs and bad images
        imgs = [
            i for i in imgs
            if 'web.archive.org' not in i
            and not _is_bad_og_image(i)
        ]
        if imgs:
            rel = os.path.relpath(imgs[0], site_dir).replace(os.sep, '/')
            return base_url.rstrip('/') + '/' + rel
    return None


def fix_twitter_card(site_dir: str):
    """
    Add twitter:card/title/description/image meta tags if missing.
    Copies values from existing og:* tags.
    """
    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if d not in ['.git', 'node_modules'] + LANG_DIRS + ARCHIVE_DIRS]
        for fname in files:
            if not fname.endswith('.html'):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, encoding='utf-8', errors='ignore') as f:
                html = f.read()

            if 'twitter:card' in html:
                continue

            head_close = html.find('</head>')
            if head_close < 0:
                continue

            def _og(prop):
                m = re.search(
                    r'<meta[^>]*property=["\']og:' + prop + r'["\'][^>]*content=["\']([^"\']*)["\']',
                    html, re.IGNORECASE
                )
                return m.group(1).strip() if m else None

            title = _og('title')
            desc  = _og('description')
            image = _og('image')

            if not title and not desc:
                continue

            tags = ['<meta name="twitter:card" content="summary_large_image">']
            if title:
                tags.append(f'<meta name="twitter:title" content="{title}">')
            if desc:
                tags.append(f'<meta name="twitter:description" content="{desc}">')
            if image:
                tags.append(f'<meta name="twitter:image" content="{image}">')

            inject = '\n'.join(tags) + '\n'
            html = html[:head_close] + inject + html[head_close:]

            with open(fpath, 'w', encoding='utf-8') as f:
                f.write(html)


def fix_preloader(site_dir: str):
    """
    Add display:none !important to preloader/loading-screen selectors in CSS files.
    Prevents sites from getting stuck on loading animations when JS fails to run
    (common with web-archive sites where script tags may be broken or missing).
    Targets: .preloader, #preloader, .loading, .loading-screen, .page-loader, etc.
    """
    PRELOADER_SELECTORS = [
        r'\.preloader', r'#preloader', r'\.loading-screen', r'\.page-loader',
        r'\.page-loading', r'\.site-preloader', r'\.site-loader',
    ]
    # Matches selector { ... } where display:none is not already present
    _sel_pattern = re.compile(
        r'(' + '|'.join(PRELOADER_SELECTORS) + r')(\s*\{)(?![^}]*display\s*:\s*none)',
        re.IGNORECASE
    )

    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if d not in ['.git', 'node_modules'] + ARCHIVE_DIRS]
        for fname in files:
            if not fname.endswith('.css'):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, encoding='utf-8', errors='ignore') as f:
                css = f.read()

            original = css
            css = _sel_pattern.sub(r'\1\2\n    display: none !important;', css)

            if css != original:
                with open(fpath, 'w', encoding='utf-8') as f:
                    f.write(css)


def fix_cloudflare_stubs(site_dir: str):
    """
    Delete HTML files that are Cloudflare challenge/waiting-room stubs.
    Detected by: 'window.location.reload()' or 'One moment, please' in content.
    The file is removed so the pipeline doesn't process a stub instead of real content.
    """
    STUB_PATTERNS = [
        r'window\.location\.reload\(\)',
        r'One moment,\s*please',
        r'Please wait while your request is being verified',
        r'Checking your browser before accessing',
        r'DDoS protection by\s+Cloudflare',
        r'Ray ID:.*Cloudflare',
    ]

    removed = 0
    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if d not in ['.git', 'node_modules'] + ARCHIVE_DIRS]
        for fname in files:
            if not fname.endswith('.html'):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, encoding='utf-8', errors='ignore') as f:
                html = f.read()

            for pattern in STUB_PATTERNS:
                if re.search(pattern, html, re.IGNORECASE):
                    os.remove(fpath)
                    removed += 1
                    break

    return removed


def fix_external_links(site_dir: str, site_domain: str = None):
    """
    Remove external links from all HTML pages.
    - <a href="https://...">text</a>  →  text  (strip tag, keep anchor text)
    - <a href="https://..."><img...></a>  →  <img...>  (strip tag, keep img)
    - mailto: / tel: links are left untouched
    - Links to site_domain are left untouched
    """
    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if d not in ['.git', 'node_modules'] + ARCHIVE_DIRS]
        for fname in files:
            if not fname.endswith('.html'):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, encoding='utf-8', errors='ignore') as f:
                html = f.read()

            original = html

            def strip_external(m):
                full = m.group(0)   # entire <a ...>...</a>
                open_tag = m.group(1)
                inner = m.group(2)

                href_m = re.search(r'href=["\']([^"\']*)["\']', open_tag)
                if not href_m:
                    return full
                href = href_m.group(1)

                # Leave internal, mailto, tel untouched
                if not href.startswith('http'):
                    return full
                # Leave own domain untouched
                if site_domain and site_domain.lower() in href.lower():
                    return full

                # Return just the inner content (text or img)
                return inner.strip()

            html = re.sub(
                r'(<a\s[^>]*>)(.*?)</a>',
                strip_external,
                html,
                flags=re.DOTALL | re.IGNORECASE
            )

            if html != original:
                with open(fpath, 'w', encoding='utf-8') as f:
                    f.write(html)


def fix_nofollow(site_dir: str):
    """Deprecated: use fix_external_links instead."""
    fix_external_links(site_dir)


def fix_sitemap(site_dir: str, langs: list, site_domain: str = None) -> None:
    """
    Generate sitemap.xml with hreflang alternate links for all EN pages and their translations.
    Skipped entirely if site_domain is not provided (can't build valid URLs without a real host).
    Overwrites existing sitemap only if it contains example.com or was generated by this bot.
    """
    if not site_domain:
        return

    BASE_URL = site_domain.rstrip('/')

    # Check if existing sitemap should be overwritten
    sitemap_path = os.path.join(site_dir, 'sitemap.xml')
    if os.path.exists(sitemap_path):
        with open(sitemap_path, encoding='utf-8', errors='ignore') as f:
            existing = f.read()
        if 'example.com' not in existing and BASE_URL in existing:
            return  # already correct, skip

    SKIP_DIRS = set(LANG_DIRS) | set(ARCHIVE_DIRS) | {'scripts', 'images', 'css', '.git', 'node_modules'}

    # Collect all EN pages (root-level, skip lang/archive dirs)
    url_entries = []

    for root, dirs, files in os.walk(site_dir):
        dirs[:] = sorted([d for d in dirs if d not in SKIP_DIRS])
        for fname in files:
            if not fname.endswith('.html'):
                continue
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, site_dir).replace(os.sep, '/')
            # Compute clean URL path (strip index.html)
            clean = rel[:-len('index.html')] if rel.endswith('index.html') else rel
            clean = clean.strip('/')
            page_url = BASE_URL + ('/' + clean if clean else '/')

            # Build hreflang alternate links for this page
            alternates = [
                f'    <xhtml:link rel="alternate" hreflang="en" href="{page_url}"/>',
                f'    <xhtml:link rel="alternate" hreflang="x-default" href="{page_url}"/>',
            ]
            for lang in sorted(langs):
                lang_fpath = os.path.join(site_dir, lang, rel)
                if os.path.exists(lang_fpath):
                    lang_url = BASE_URL + '/' + lang + ('/' + clean if clean else '/')
                    alternates.append(
                        f'    <xhtml:link rel="alternate" hreflang="{lang}" href="{lang_url}"/>'
                    )

            entry = (
                '  <url>\n'
                f'    <loc>{page_url}</loc>\n'
                + '\n'.join(alternates) + '\n'
                '  </url>'
            )
            url_entries.append(entry)

    if not url_entries:
        return

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"\n'
        '        xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
        + '\n'.join(url_entries) + '\n'
        '</urlset>\n'
    )

    with open(sitemap_path, 'w', encoding='utf-8') as f:
        f.write(xml)


def fix_robots_txt(site_dir: str, site_domain: str = None):
    """Supplement robots.txt: remove Host directive, ensure correct Sitemap URL."""
    robots_path = os.path.join(site_dir, 'robots.txt')
    BASE_URL = _detect_base_url(site_dir, site_domain)
    sitemap_url = BASE_URL.rstrip('/') + '/sitemap.xml'

    _FRESH = (
        'User-agent: *\n'
        'Allow: /\n'
        '\n'
        f'Sitemap: {sitemap_url}\n'
    )

    if not os.path.exists(robots_path):
        with open(robots_path, 'w', encoding='utf-8') as f:
            f.write(_FRESH)
        return

    with open(robots_path, encoding='utf-8', errors='ignore') as f:
        content = f.read()

    # Stale placeholder — regenerate fully
    if 'example.com' in content:
        with open(robots_path, 'w', encoding='utf-8') as f:
            f.write(_FRESH)
        return

    # Remove Host: directive (Yandex legacy, deprecated)
    content = re.sub(r'^Host:[^\n]*\n?', '', content, flags=re.MULTILINE | re.IGNORECASE)

    # Update or append Sitemap line
    sitemap_re = re.compile(r'^Sitemap:.*$', re.MULTILINE | re.IGNORECASE)
    if sitemap_re.search(content):
        content = sitemap_re.sub(f'Sitemap: {sitemap_url}', content)
    else:
        content = content.rstrip('\n') + f'\n\nSitemap: {sitemap_url}\n'

    with open(robots_path, 'w', encoding='utf-8') as f:
        f.write(content)


def fix_hreflang_translated(site_dir: str, langs: list, site_domain: str = None,
                            source_lang: str = 'en'):
    """
    Add hreflang tags to translated pages that are missing them.
    Mirrors the hreflang block from the corresponding source page.
    """
    if not langs:
        return

    BASE_URL = _detect_base_url(site_dir, site_domain)

    for lang in langs:
        lang_dir = os.path.join(site_dir, lang)
        if not os.path.isdir(lang_dir):
            continue

        for root, dirs, files in os.walk(lang_dir):
            dirs[:] = [d for d in dirs if d not in ['.git', 'node_modules'] + ARCHIVE_DIRS]
            for fname in files:
                if not fname.endswith('.html'):
                    continue
                fpath = os.path.join(root, fname)
                with open(fpath, encoding='utf-8', errors='ignore') as f:
                    html = f.read()

                if re.search(r'hreflang', html, re.IGNORECASE):
                    continue

                # Find corresponding source page
                rel_from_lang = os.path.relpath(fpath, lang_dir)
                source_path = os.path.join(site_dir, rel_from_lang)
                if not os.path.exists(source_path):
                    source_path = os.path.join(site_dir, 'index.html')
                if not os.path.exists(source_path):
                    continue

                with open(source_path, encoding='utf-8', errors='ignore') as f:
                    source_html = f.read()

                # Extract hreflang block from source
                hreflang_tags = re.findall(
                    r'<link[^>]*hreflang[^>]*>', source_html, re.IGNORECASE
                )

                if not hreflang_tags:
                    # Build hreflang block from scratch
                    rel_path = rel_from_lang.replace(os.sep, '/').replace('index.html', '')
                    hreflang_tags = [
                        f'<link rel="alternate" hreflang="{source_lang}" href="{BASE_URL}/{rel_path}">',
                        f'<link rel="alternate" hreflang="{lang}" href="{BASE_URL}/{lang}/{rel_path}">',
                        f'<link rel="alternate" hreflang="x-default" href="{BASE_URL}/{rel_path}">',
                    ]

                block = '\n'.join(hreflang_tags) + '\n'
                html = html.replace('</head>', block + '</head>', 1)

                with open(fpath, 'w', encoding='utf-8') as f:
                    f.write(html)


def _count_translatable_pages(site_dir: str) -> int:
    """Count HTML pages that will be translated (excluding lang dirs)."""
    count = 0
    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if d not in LANG_DIRS + ARCHIVE_DIRS + ['.git', 'node_modules', 'scripts']]
        for fname in files:
            if fname.endswith('.html'):
                count += 1
    return count


def fix_translations(site_dir: str, langs: list, api_key: str, site_domain: str = None,
                     progress_callback=None, translate_only: bool = False,
                     stop_event=None):
    """Run translation script on the site directory."""
    translate_script = os.path.join(site_dir, 'scripts', 'translate.py')
    our_script = os.path.join(os.path.dirname(__file__), 'translate.py')
    if not os.path.exists(our_script):
        raise FileNotFoundError('translate.py not found in bot directory')
    import shutil
    os.makedirs(os.path.join(site_dir, 'scripts'), exist_ok=True)
    shutil.copy(our_script, translate_script)

    if not api_key:
        raise ValueError('Translation API key not provided (WOWAI_API_KEY)')

    cmd = [
        sys.executable, '-u', translate_script,
        '--key', api_key,
        '--langs', ','.join(langs),
        '--skip-existing',
    ]
    if site_domain:
        cmd += ['--base-url', site_domain.rstrip('/')]

    import logging
    _log = logging.getLogger(__name__)

    total_pages = _count_translatable_pages(site_dir) if progress_callback else 0
    done_pages = 0

    # Run with stdout streamed line-by-line so Railway logs show translation progress
    proc = subprocess.Popen(
        cmd,
        cwd=site_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding='utf-8',
    )
    stderr_tail = []
    try:
        for line in proc.stdout:
            if stop_event and stop_event.is_set():
                proc.terminate()
                _log.info('[translate] ⏹ Cancellation requested — translation stopped')
                return
            line = line.rstrip()
            if line:
                _log.info('[translate] %s', line)
                stderr_tail.append(line)
                if len(stderr_tail) > 50:
                    stderr_tail.pop(0)
                if progress_callback and 'hreflang' in line and 'en page' in line.lower():
                    done_pages += 1
                    try:
                        progress_callback(done_pages, total_pages)
                    except Exception:
                        pass
        proc.wait(timeout=1800)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise RuntimeError('Translation timed out after 30 minutes')
    if proc.returncode not in (0, -15):  # -15 = SIGTERM (cancelled)
        tail = '\n'.join(stderr_tail[-20:])
        raise RuntimeError(f'Translation failed (exit {proc.returncode}):\n{tail}')


def fix_title_refresh(site_dir: str):
    """
    Update year references in title and meta description across all HTML pages
    (EN and translated). Replaces the previous year with the current year.
    Ported from HELP project's title-refresh.js.
    """
    import datetime
    current_year = datetime.datetime.now().year
    prev_year    = current_year - 1

    if prev_year == current_year:
        return

    year_pattern = re.compile(r'\b' + str(prev_year) + r'\b')
    updated = 0

    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if d not in ['.git', 'node_modules'] + ARCHIVE_DIRS]
        for fname in files:
            if not fname.endswith('.html'):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, encoding='utf-8', errors='ignore') as f:
                html = f.read()

            original = html

            def refresh_attr(m):
                """Replace year only inside content="" and <title> values."""
                return year_pattern.sub(str(current_year), m.group(0))

            # Only update year inside <title> tags
            html = re.sub(r'<title>[^<]+</title>', refresh_attr, html)
            # Only update year inside meta name="description" content="..."
            html = re.sub(
                r'(<meta[^>]*name=["\']description["\'][^>]*content=")[^"]*(")',
                refresh_attr, html, flags=re.IGNORECASE
            )
            # Only update year inside og:title and og:description content
            html = re.sub(
                r'(<meta[^>]*property=["\']og:(?:title|description)["\'][^>]*content=")[^"]*(")',
                refresh_attr, html, flags=re.IGNORECASE
            )

            if html != original:
                with open(fpath, 'w', encoding='utf-8') as f:
                    f.write(html)
                updated += 1


def fix_internal_links(site_dir: str):
    """
    Auto-linker: insert 2-3 contextual internal links per page.

    Algorithm (ported from HELP project's auto-linker.js):
    1. Build keyword → URL map from EN page titles and H1 headings
    2. For each EN page, find keyword matches in paragraph text
    3. Wrap first occurrence (not inside an existing <a>) with a link
    4. Limit: 3 insertions per page, no self-links, no duplicate targets
    """
    STOP_WORDS = {
        'the','a','an','in','on','at','for','to','of','and','or','is','are',
        'was','were','this','that','with','from','by','as','its','it','be',
        'has','have','had','we','you','your','our','their','not','but','if',
        'so','about','how','what','when','where','who','which','can','will',
        'all','also','more','other','new','use','used','using','get','our',
        'page','click','here','read','view','find','see','learn','check',
    }
    LANG_DIRS = {
        'ru', 'de', 'fr', 'es', 'it', 'pt', 'pl', 'nl', 'cs', 'ro', 'sv', 'tr',
        'el', 'uk', 'ko', 'zh', 'ja', 'sk', 'fi', 'ar', 'hi',
    }
    SKIP_DIRS = LANG_DIRS | set(ARCHIVE_DIRS) | {'scripts', 'images', 'css', '.git', 'node_modules'}

    # ── Step 1: collect all EN HTML pages ──
    html_files = []
    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            if fname.endswith('.html'):
                fpath = os.path.join(root, fname)
                rel = fpath.replace(site_dir, '').replace(os.sep, '/').lstrip('/')
                html_files.append((fpath, rel))

    if len(html_files) < 2:
        return  # Not enough pages to link between

    # ── Step 2: build keyword → (url, anchor_text) map ──
    page_map = {}  # keyword → (url_path, display_title)

    for fpath, rel in html_files:
        with open(fpath, encoding='utf-8', errors='ignore') as f:
            html = f.read()

        title_m = re.search(r'<title>([^<]+)</title>', html)
        h1_m    = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.IGNORECASE | re.DOTALL)

        title_raw = title_m.group(1).strip() if title_m else ''
        h1_raw    = re.sub(r'<[^>]+>', '', h1_m.group(1)).strip() if h1_m else ''

        # Strip site name suffix from title ("Page :: Site Name" → "Page")
        title_clean = re.split(r'\s+[|:–—]\s+', title_raw)[0].strip()
        label = h1_raw or title_clean

        # Compute URL path
        url_path = '/' + rel
        if url_path.endswith('/index.html'):
            url_path = url_path[:-len('index.html')]
        elif url_path == '/index.html':
            url_path = '/'

        # Generate keyword phrases from title/H1
        words = [w.lower() for w in re.findall(r'\b[a-zA-Z]{3,}\b', title_clean)
                 if w.lower() not in STOP_WORDS]

        # 2-word phrases (higher specificity, preferred)
        for i in range(len(words) - 1):
            kw = f'{words[i]} {words[i+1]}'
            if len(kw) > 7 and kw not in page_map:
                page_map[kw] = (url_path, label)

        # Single meaningful words (length > 5, as fallback)
        for w in words:
            if len(w) > 5 and w not in page_map:
                page_map[w] = (url_path, label)

    # Sort by keyword length descending (longer phrases matched first)
    sorted_kw = sorted(page_map.items(), key=lambda x: -len(x[0]))

    # ── Step 3: insert links in each page ──
    for fpath, rel in html_files:
        with open(fpath, encoding='utf-8', errors='ignore') as f:
            html = f.read()

        url_path = '/' + rel
        if url_path.endswith('/index.html'):
            url_path = url_path[:-len('index.html')]
        elif url_path == '/index.html':
            url_path = '/'

        inserted   = 0
        used_urls  = {url_path}       # no self-links
        used_kws   = set()
        new_html   = html

        for kw, (target_url, target_label) in sorted_kw:
            if inserted >= 3:
                break
            if target_url in used_urls or kw in used_kws:
                continue

            # Match keyword inside <p> text but NOT inside an existing <a> tag
            # Strategy: split HTML into link / non-link segments, only replace in non-link parts
            def _insert_in_paragraphs(html_in, keyword, url, label):
                """Replace first bare occurrence of keyword in a <p> with an <a> link."""
                pattern = re.compile(
                    r'(<p(?:\s[^>]*)?>)(.*?)(</p>)',
                    re.IGNORECASE | re.DOTALL
                )
                replaced = [False]

                def replace_in_p(m):
                    if replaced[0]:
                        return m.group(0)
                    open_tag, inner, close_tag = m.group(1), m.group(2), m.group(3)

                    # Only modify if keyword appears outside existing <a> tags
                    # Split inner HTML into [non-link, link, non-link, link, ...]
                    parts = re.split(r'(<a\b[^>]*>.*?</a>)', inner,
                                     flags=re.IGNORECASE | re.DOTALL)
                    new_parts = []
                    done = False
                    for part in parts:
                        if not done and not part.startswith('<a'):
                            new_part = re.sub(
                                r'\b(' + re.escape(keyword) + r')\b',
                                lambda mo, u=url, l=label: f'<a href="{u}" title="{l}">{mo.group(1)}</a>',
                                part, count=1, flags=re.IGNORECASE
                            )
                            if new_part != part:
                                done = True
                                replaced[0] = True
                            new_parts.append(new_part)
                        else:
                            new_parts.append(part)

                    return open_tag + ''.join(new_parts) + close_tag

                return pattern.sub(replace_in_p, html_in), replaced[0]

            new_html, did_insert = _insert_in_paragraphs(new_html, kw, target_url, target_label)
            if did_insert:
                inserted += 1
                used_urls.add(target_url)
                used_kws.add(kw)

        if inserted > 0 and new_html != html:
            with open(fpath, 'w', encoding='utf-8') as f:
                f.write(new_html)


def fix_translated_relative_links(site_dir: str):
    """
    Fix relative href/src links in translated pages (lang subfolders) so they
    resolve correctly after being moved into a subdirectory.

    Problem: translated pages are created by copying root HTML into de/, fr/, etc.
    A root page at depth 0 has links like href="p/page.html".
    A translated page at de/ (depth 1) inherits the same href, but now
    href="p/page.html" resolves to de/p/page.html (wrong).

    Fix: for each relative link in a translated page that doesn't resolve to an
    existing file, climb up to the root and check — if it exists there, prepend
    the appropriate number of '../' to reach the root.
    """
    LANG_DIRS = {
        'ru', 'de', 'fr', 'es', 'it', 'pt', 'pl', 'nl', 'cs', 'ro', 'sv', 'tr',
        'el', 'uk', 'ko', 'zh', 'ja', 'sk', 'fi', 'ar', 'hi',
    }
    ATTR_PATTERN = re.compile(
        r'((?:href|src|action)=["\'])(?!https?://|//|#|mailto:|javascript:|data:)([^"\']+)(["\'])',
        re.IGNORECASE
    )
    # Pattern for absolute paths like /de/path that might not exist in translated versions
    ABS_LANG_PATTERN = re.compile(
        r'((?:href|src|action)=["\'])(/' + '|/'.join(
            ['ru', 'de', 'fr', 'es', 'it', 'pt', 'pl', 'nl', 'cs', 'ro', 'sv', 'tr',
             'el', 'uk', 'ko', 'zh', 'ja', 'sk', 'fi', 'ar', 'hi']
        ) + r')(/[^"\'?#]*)(["\'])',
        re.IGNORECASE
    )

    for lang in sorted(os.listdir(site_dir)):
        lang_dir = os.path.join(site_dir, lang)
        if lang not in LANG_DIRS or not os.path.isdir(lang_dir):
            continue

        for root, dirs, files in os.walk(lang_dir):
            dirs[:] = [d for d in dirs if d not in ['scripts', '.git', 'node_modules']]
            for fname in files:
                if not fname.endswith('.html'):
                    continue
                fpath = os.path.join(root, fname)

                with open(fpath, encoding='utf-8', errors='ignore') as f:
                    html = f.read()

                # How many levels deep is this file inside the lang dir?
                rel_in_lang = os.path.relpath(fpath, lang_dir)
                depth_in_lang = len(rel_in_lang.replace(os.sep, '/').split('/'))
                # depth_in_lang = 1 for lang/index.html, 2 for lang/a/b.html, etc.
                to_root = '../' * depth_in_lang  # e.g. '../' or '../../'

                changed = False

                # Relative path of this file within the lang dir
                # e.g. "2021/01/index.html" for de/2021/01/index.html
                rel_in_lang = os.path.relpath(fpath, lang_dir).replace(os.sep, '/')
                # Equivalent directory at the site root
                root_equiv_dir = os.path.normpath(
                    os.path.join(site_dir, os.path.dirname(rel_in_lang))
                )

                def fix_attr(m):
                    nonlocal changed
                    attr_prefix = m.group(1)   # e.g. 'href="'
                    href = m.group(2)           # the path value
                    attr_suffix = m.group(3)   # closing quote

                    # Skip empty or absolute paths
                    if not href or href.startswith('/'):
                        return m.group(0)

                    # Separate anchor fragment (e.g. "page.html#section")
                    if '#' in href:
                        file_part, anchor = href.split('#', 1)
                        anchor_suffix = '#' + anchor
                    else:
                        file_part, anchor_suffix = href, ''

                    if not file_part:
                        return m.group(0)  # pure anchor link like "#id"

                    # Resolve the file part relative to the current file's directory
                    current_dir = os.path.dirname(fpath)
                    resolved = os.path.normpath(os.path.join(current_dir, file_part))

                    # Already resolves to an existing file/dir → leave it
                    if os.path.exists(resolved):
                        return m.group(0)

                    # Strategy 1: check in the SAME directory at the site root
                    root_same_dir = os.path.normpath(os.path.join(root_equiv_dir, file_part))
                    if os.path.exists(root_same_dir):
                        root_rel = os.path.relpath(root_same_dir, site_dir).replace(os.sep, '/')
                        new_href = to_root + root_rel + anchor_suffix
                        changed = True
                        return attr_prefix + new_href + attr_suffix

                    # Strategy 2: check at the site root directly
                    root_top = os.path.normpath(os.path.join(site_dir, file_part))
                    if os.path.exists(root_top):
                        new_href = to_root + file_part + anchor_suffix
                        changed = True
                        return attr_prefix + new_href + attr_suffix

                    return m.group(0)

                new_html = ATTR_PATTERN.sub(fix_attr, html)

                # Fix absolute /lang/path links that point to non-existent translated pages
                # Fall back to /path (source lang at root) when translation doesn't exist,
                # or to the nearest parent directory index when neither exists.
                def fix_abs_lang(m):
                    nonlocal changed
                    attr_prefix = m.group(1)   # e.g. 'href="'
                    lang_seg = m.group(2)       # e.g. '/de'
                    rest = m.group(3)           # e.g. '/p/privacy-policy.html'
                    attr_suffix = m.group(4)   # closing quote
                    abs_path = lang_seg + rest  # e.g. '/de/p/privacy-policy.html'
                    translated_file = site_dir.rstrip('/\\') + abs_path.replace('/', os.sep)
                    if os.path.exists(translated_file):
                        return m.group(0)  # translation exists, leave it
                    # Strategy 1: root-level fallback (Arabic source)
                    root_file = site_dir.rstrip('/\\') + rest.replace('/', os.sep)
                    if os.path.exists(root_file):
                        changed = True
                        return attr_prefix + rest + attr_suffix
                    # Strategy 2: parent directory index in same lang
                    parent_rest = os.path.dirname(rest.rstrip('/'))
                    if parent_rest and parent_rest != '/':
                        parent_idx = lang_seg + parent_rest + '/index.html'
                        parent_file = site_dir.rstrip('/\\') + parent_idx.replace('/', os.sep)
                        if os.path.exists(parent_file):
                            changed = True
                            return attr_prefix + parent_idx + attr_suffix
                    # Strategy 3: lang root index
                    lang_index = lang_seg + '/index.html'
                    lang_index_file = site_dir.rstrip('/\\') + lang_index.replace('/', os.sep)
                    if os.path.exists(lang_index_file):
                        changed = True
                        return attr_prefix + lang_index + attr_suffix
                    return m.group(0)

                new_html = ABS_LANG_PATTERN.sub(fix_abs_lang, new_html)

                if changed and new_html != html:
                    with open(fpath, 'w', encoding='utf-8') as f:
                        f.write(new_html)


def fix_lang_switcher(site_dir: str, source_lang: str = 'en'):
    """
    Inject a floating language switcher dropdown into every HTML page.
    Detects available languages from subdirectories and builds relative links.
    source_lang: the root language of the site (default 'en').
    """
    LANG_NAMES = {
        'en': ('🇬🇧', 'English'),
        'ru': ('🇷🇺', 'Русский'),
        'de': ('🇩🇪', 'Deutsch'),
        'fr': ('🇫🇷', 'Français'),
        'es': ('🇪🇸', 'Español'),
        'it': ('🇮🇹', 'Italiano'),
        'pt': ('🇵🇹', 'Português'),
        'zh': ('🇨🇳', '中文'),
        'ja': ('🇯🇵', '日本語'),
        'ko': ('🇰🇷', '한국어'),
        'ar': ('🇸🇦', 'العربية'),
        'nl': ('🇳🇱', 'Nederlands'),
        'pl': ('🇵🇱', 'Polski'),
        'tr': ('🇹🇷', 'Türkçe'),
        'uk': ('🇺🇦', 'Українська'),
        'el': ('🇬🇷', 'Ελληνικά'),
        'cs': ('🇨🇿', 'Čeština'),
        'ro': ('🇷🇴', 'Română'),
        'sv': ('🇸🇪', 'Svenska'),
        'sk': ('🇸🇰', 'Slovenčina'),
        'fi': ('🇫🇮', 'Suomi'),
        'hi': ('🇮🇳', 'हिन्दी'),
    }

    # Detect which languages actually exist as subdirectories
    available_langs = [source_lang]  # source lang is always the root
    for entry in sorted(os.listdir(site_dir)):
        if entry in LANG_NAMES and entry != source_lang and os.path.isdir(os.path.join(site_dir, entry)):
            available_langs.append(entry)

    if len(available_langs) <= 1:
        return  # Nothing to switch between

    # Always place switcher on the left — bottom-right conflicts with scroll-to-top
    # buttons, cookie banners, and chat widgets common across themes.
    h_side = 'left'
    panel_side = 'left'

    # Build CSS + JS (injected once per page)
    SWITCHER_STYLE = f"""
<style id="lang-switcher-style">
#lang-switcher{{position:fixed;bottom:20px;{h_side}:20px;z-index:9999;font-family:sans-serif}}
#lang-btn{{background:#222;color:#fff;border:none;border-radius:24px;padding:8px 16px;
  font-size:14px;cursor:pointer;display:flex;align-items:center;gap:6px;
  box-shadow:0 2px 8px rgba(0,0,0,.35);transition:background .2s}}
#lang-btn:hover{{background:#444}}
#lang-panel{{display:none;position:absolute;bottom:44px;{panel_side}:0;background:#fff;
  border-radius:12px;box-shadow:0 4px 20px rgba(0,0,0,.18);overflow:hidden;
  min-width:160px;max-height:320px;overflow-y:auto}}
#lang-panel.open{{display:block}}
.lang-item{{display:flex;align-items:center;gap:8px;padding:10px 16px;
  text-decoration:none;color:#222;font-size:14px;transition:background .15s}}
.lang-item:hover{{background:#f5f5f5}}
.lang-item.active{{background:#f0f7ff;font-weight:600}}
</style>"""

    SWITCHER_JS = """
<script id="lang-switcher-script">
(function(){
  var btn=document.getElementById('lang-btn');
  var panel=document.getElementById('lang-panel');
  if(!btn||!panel)return;
  btn.addEventListener('click',function(e){e.stopPropagation();panel.classList.toggle('open');});
  document.addEventListener('click',function(){panel.classList.remove('open');});
})();
</script>"""

    # Walk all HTML files
    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if d not in ['.git', 'node_modules', 'scripts'] + ARCHIVE_DIRS]
        for fname in files:
            if not fname.endswith('.html'):
                continue
            fpath = os.path.join(root, fname)

            with open(fpath, encoding='utf-8', errors='ignore') as f:
                html = f.read()

            # Remove previously injected switcher so it gets regenerated with
            # up-to-date language list (new translations may have been added)
            if 'lang-switcher' in html:
                html = re.sub(r'<style id="lang-switcher-style">.*?</style>\n?', '', html, flags=re.DOTALL)
                # Match from opening <div> all the way through the closing </script>
                # (avoids the bug where .*?</div> stops at the first nested </div>)
                html = re.sub(r'\n?<div id="lang-switcher">.*?<script id="lang-switcher-script">.*?</script>', '', html, flags=re.DOTALL)

            # Determine current page's language and slug
            rel = os.path.relpath(fpath, site_dir).replace(os.sep, '/')
            parts = rel.split('/')

            # Depth from site_dir root (0 = root level)
            depth = len(parts) - 1

            # Is this page inside a lang subdir?
            if depth >= 1 and parts[0] in LANG_NAMES:
                current_lang = parts[0]
                slug = '/'.join(parts[1:])  # e.g. "index.html" or "about/index.html"
                to_root = '../' * depth
            else:
                current_lang = source_lang
                slug = rel  # e.g. "index.html" or "about-us.html"
                to_root = '../' * depth if depth > 0 else ''

            # Build links for each language — only include langs where this page exists
            flag, name = LANG_NAMES.get(current_lang, ('🌐', current_lang.upper()))
            items_html = ''
            for lang in available_langs:
                lflag, lname = LANG_NAMES.get(lang, ('🌐', lang.upper()))
                is_active = (lang == current_lang)

                if lang == source_lang:
                    candidate_path = os.path.normpath(os.path.join(site_dir, slug))
                    if not is_active and not os.path.exists(candidate_path):
                        # Source lang page doesn't exist (e.g. archive index not captured)
                        # Fall back to the root homepage instead
                        href = to_root + 'index.html' if to_root else 'index.html'
                    else:
                        href = to_root + slug if to_root else slug
                    # Source lang is always at root — include it
                else:
                    # Check this page actually exists in the lang dir before linking
                    translated_path = os.path.join(site_dir, lang, slug.replace('/', os.sep))
                    if not is_active and not os.path.exists(translated_path):
                        continue  # Skip languages where this specific page doesn't exist
                    href = to_root + lang + '/' + slug

                active_class = ' active' if is_active else ''
                items_html += (
                    f'<a href="{href}" class="lang-item{active_class}">'
                    f'{lflag} {lname}</a>\n'
                )

            switcher_html = (
                f'\n<div id="lang-switcher">'
                f'<button id="lang-btn"><span>{flag}</span> {name} ▾</button>'
                f'<div id="lang-panel">{items_html}</div>'
                f'</div>\n'
                + SWITCHER_JS
            )

            html = html.replace('</head>', SWITCHER_STYLE + '\n</head>', 1)
            # Insert switcher AFTER the LAST </body> tag (as sibling of <body> under <html>)
            # Using rfind avoids the Blogger/CMS pattern where a fake </body> appears
            # inside a <noscript> block earlier in the file.
            # Placing it outside <body> also bypasses overflow:hidden / transform on body.
            last_body = html.rfind('</body>')
            if last_body != -1:
                html = html[:last_body + len('</body>')] + '\n' + switcher_html + html[last_body + len('</body>'):]
            else:
                html += '\n' + switcher_html

            with open(fpath, 'w', encoding='utf-8') as f:
                f.write(html)


def _detect_base_url(site_dir: str, site_domain: str = None) -> str:
    """
    Return the site's base URL.
    Priority: user-provided domain > canonical tags in HTML > sitemap.xml > fallback.
    Sitemap is checked last because it may contain a wrong domain from a previous run.
    """
    if site_domain:
        return site_domain.rstrip('/')

    # Try absolute canonical tags in HTML (more reliable than sitemap)
    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if d not in ['.git', 'node_modules', 'scripts'] + ARCHIVE_DIRS]
        for fname in files:
            if not fname.endswith('.html'):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, encoding='utf-8', errors='ignore') as f:
                html = f.read()
            m = re.search(r'<link[^>]*rel=["\']canonical["\'][^>]*href=["\']([^"\']+)["\']', html)
            if m:
                url = m.group(1)
                base = re.match(r'(https?://[^/]+)', url)
                if base and 'example.com' not in base.group(1):
                    return base.group(1)

    return 'https://example.com'
