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
    LANGS = ['ru', 'de', 'fr', 'es', 'it', 'pt']

    # Find HTML files, skip lang subdirs
    pages = []
    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs
                   if d not in LANGS + ['scripts', 'images', 'css', '.git', 'node_modules']]
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
                issues.append(f'⚠️ {rel}: title длинный ({len(t)} симв)')
                page_ok = False

        # 2. Description
        desc_m = re.search(r'<meta[^>]*name=["\']description["\'][^>]*content="([^"]+)"', html, re.IGNORECASE)
        if not desc_m:
            issues.append(f'❌ {rel}: нет description')
            page_ok = False
        else:
            d = desc_m.group(1).strip()
            if len(d) > 160:
                issues.append(f'⚠️ {rel}: description длинный ({len(d)} симв)')
                page_ok = False

        # 3. H1
        h1_m = re.search(r'<h1[^>]*>([^<]+)</h1>', html, re.IGNORECASE)
        if not h1_m:
            issues.append(f'❌ {rel}: нет H1')
            page_ok = False

        # 4. Canonical
        if not re.search(r'rel=["\']canonical["\']', html, re.IGNORECASE):
            issues.append(f'❌ {rel}: нет canonical')
            page_ok = False

        # 5. OG image
        if not re.search(r'og:image', html):
            issues.append(f'⚠️ {rel}: нет og:image')
            page_ok = False

        # 6. Schema.org
        if not re.search(r'application/ld\+json', html):
            issues.append(f'⚠️ {rel}: нет Schema.org')
            page_ok = False

        # 7. Word count
        main_m = re.search(r'<main[^>]*>(.*?)</main>', html, re.DOTALL | re.IGNORECASE)
        if main_m:
            text = re.sub(r'<[^>]+>', ' ', main_m.group(1))
            words = len(text.split())
            if words < 50:
                issues.append(f'⚠️ {rel}: мало текста ({words} слов)')
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
