#!/usr/bin/env python3
"""
SEO audit module — runs on a cloned repo directory.
Returns structured results for the bot.
"""
import os
import re
import glob


def run_audit_on_dir(site_dir: str) -> dict:
    """
    Audit all HTML pages in site_dir.
    Returns: {total, passed, failed, issues: [...]}
    """
    LANGS = [
        'ru', 'de', 'fr', 'es', 'it', 'pt', 'pl', 'nl', 'cs', 'ro', 'sv', 'tr',
        'el', 'uk', 'ko', 'zh', 'ja', 'sk', 'fi', 'ar', 'hi',
    ]

    pages = []
    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs
                   if d not in LANGS + ['scripts', 'images', 'css', '.git', 'node_modules', '_git_clone']]
        for fname in files:
            if fname.endswith('.html'):
                pages.append(os.path.join(root, fname))

    total = 0
    passed = 0
    failed = 0
    issues = []

    for fpath in pages:
        rel = fpath.replace(site_dir, '').replace(os.sep, '/').lstrip('/')
        with open(fpath, encoding='utf-8', errors='ignore') as f:
            html = f.read()

        # Skip noindex pages
        if re.search(r'<meta[^>]*name=["\']robots["\'][^>]*noindex', html, re.IGNORECASE):
            continue

        total += 1
        page_ok = True

        # 1. Title
        title_m = re.search(r'<title>([^<]+)</title>', html)
        if not title_m:
            issues.append(f'❌ {rel}: нет title')
            page_ok = False
        else:
            t = title_m.group(1).strip()
            if len(t) > 65:
                issues.append(f'⚠️ {rel}: title длинный ({len(t)} симв, норма ≤60)')
                page_ok = False
            elif len(t) < 10:
                issues.append(f'⚠️ {rel}: title слишком короткий ({len(t)} симв)')
                page_ok = False

        # 2. Description
        desc_m = re.search(r'<meta[^>]*name=["\']description["\'][^>]*content="([^"]+)"', html, re.IGNORECASE)
        if not desc_m:
            issues.append(f'❌ {rel}: нет description')
            page_ok = False
        else:
            d = desc_m.group(1).strip()
            if len(d) > 160:
                issues.append(f'⚠️ {rel}: description длинный ({len(d)} симв, норма ≤155)')
                page_ok = False
            elif len(d) < 50:
                issues.append(f'⚠️ {rel}: description короткий ({len(d)} симв, норма ≥120)')
                page_ok = False

        # 3. H1 — exactly one
        h1_matches = re.findall(r'<h1[^>]*>.*?</h1>', html, re.IGNORECASE | re.DOTALL)
        if not h1_matches:
            issues.append(f'❌ {rel}: нет H1')
            page_ok = False
        elif len(h1_matches) > 1:
            issues.append(f'⚠️ {rel}: несколько H1 ({len(h1_matches)} шт, должен быть 1)')
            page_ok = False

        # 4. H2 — at least one (needed for content structure)
        if not re.search(r'<h2[^>]*>', html, re.IGNORECASE):
            issues.append(f'⚠️ {rel}: нет H2 (рекомендуется минимум 1 для структуры)')
            page_ok = False

        # 5. Canonical
        if not re.search(r'rel=["\']canonical["\']', html, re.IGNORECASE):
            issues.append(f'❌ {rel}: нет canonical')
            page_ok = False

        # 6. OG image
        if not re.search(r'og:image', html):
            issues.append(f'⚠️ {rel}: нет og:image')
            page_ok = False

        # 7. Schema.org
        if not re.search(r'application/ld\+json', html):
            issues.append(f'⚠️ {rel}: нет Schema.org (JSON-LD)')
            page_ok = False

        # 8. Word count — with body fallback (many sites don't use <main>)
        word_count = _count_words(html)
        if word_count < 200:
            if word_count < 50:
                issues.append(f'❌ {rel}: очень мало текста ({word_count} слов) — рекомендуется noindex')
            else:
                issues.append(f'⚠️ {rel}: мало текста ({word_count} слов, норма ≥200)')
            page_ok = False

        # 9. Internal links count (skip homepage index.html)
        is_homepage = rel in ('index.html', 'index.htm')
        if not is_homepage:
            internal_links = re.findall(
                r'<a[^>]+href=["\'](?!https?://|mailto:|tel:|#)([^"\']+)["\']',
                html, re.IGNORECASE
            )
            # Filter out lang-switcher links and empty hrefs
            real_links = [l for l in internal_links if l and l != '#' and not l.startswith('javascript')]
            if len(real_links) < 2:
                issues.append(f'⚠️ {rel}: мало внутренних ссылок ({len(real_links)} шт, норма ≥2)')
                page_ok = False

        if page_ok:
            passed += 1
        else:
            failed += 1

    return {
        'total': total,
        'passed': passed,
        'failed': failed,
        'issues': issues,
    }


def _count_words(html: str) -> int:
    """Count visible words in HTML, preferring <main>/<article> but falling back to <body>."""
    # Try <main> or <article> first
    for tag in ('main', 'article'):
        m = re.search(rf'<{tag}[^>]*>(.*?)</{tag}>', html, re.DOTALL | re.IGNORECASE)
        if m:
            return _words_in_html(m.group(1))

    # Fallback: full <body> minus script/style
    body_m = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL | re.IGNORECASE)
    if body_m:
        body = re.sub(r'<(script|style)[^>]*>.*?</(script|style)>', '', body_m.group(1),
                      flags=re.DOTALL | re.IGNORECASE)
        return _words_in_html(body)

    return 0


def _words_in_html(fragment: str) -> int:
    text = re.sub(r'<[^>]+>', ' ', fragment)
    text = re.sub(r'\s+', ' ', text).strip()
    return len(text.split()) if text else 0
