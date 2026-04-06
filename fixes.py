#!/usr/bin/env python3
"""
SEO fix modules — called by the bot to apply fixes to a cloned site directory.
Each function operates on the site_dir in-place.
"""
import os
import re
import sys
import subprocess


def run_all_fixes(site_dir: str, step_key: str, langs: list, groq_api_key: str) -> dict:
    """Dispatcher — runs a specific fix step."""
    try:
        if step_key == 'fix_descriptions':
            fix_descriptions(site_dir)
        elif step_key == 'fix_schema':
            fix_schema(site_dir)
        elif step_key == 'fix_translations':
            fix_translations(site_dir, langs, groq_api_key)
        elif step_key == 'fix_lang_switcher':
            fix_lang_switcher(site_dir)
        return {'ok': True}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def fix_descriptions(site_dir: str):
    """Trim descriptions longer than 155 chars and sync og/twitter."""
    LANGS = ['ru', 'de', 'fr', 'es', 'it', 'pt']

    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs
                   if d not in LANGS + ['scripts', 'images', 'css', '.git', 'node_modules']]
        for fname in files:
            if not fname.endswith('.html'):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, encoding='utf-8', errors='ignore') as f:
                html = f.read()

            original = html

            def trim_desc(m):
                content = m.group(1)
                if len(content) <= 155:
                    return m.group(0)
                trimmed = content[:152].rsplit(' ', 1)[0] + '...'
                return m.group(0).replace(content, trimmed)

            html = re.sub(
                r'(<meta[^>]*name=["\']description["\'][^>]*content=")([^"]{156,})(")',
                lambda m: m.group(1) + (m.group(2)[:152].rsplit(' ', 1)[0] + '...') + m.group(3),
                html, flags=re.IGNORECASE
            )

            if html != original:
                with open(fpath, 'w', encoding='utf-8') as f:
                    f.write(html)


def fix_schema(site_dir: str):
    """Add BreadcrumbList schema to pages that don't have it."""
    LANGS = ['ru', 'de', 'fr', 'es', 'it', 'pt']
    BASE_URL = _detect_base_url(site_dir)

    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs
                   if d not in LANGS + ['scripts', 'images', 'css', '.git', 'node_modules']]
        for fname in files:
            if not fname.endswith('.html'):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, encoding='utf-8', errors='ignore') as f:
                html = f.read()

            if 'BreadcrumbList' in html:
                continue
            if re.search(r'noindex', html):
                continue

            rel = fpath.replace(site_dir, '').replace(os.sep, '/').lstrip('/')
            page_path = '/' + rel.replace('index.html', '').rstrip('/')

            # Build breadcrumb
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


def fix_translations(site_dir: str, langs: list, groq_api_key: str):
    """Run translation script on the site directory."""
    # Always use our translate.py (ensures latest version with all fixes)
    translate_script = os.path.join(site_dir, 'scripts', 'translate.py')
    our_script = os.path.join(os.path.dirname(__file__), 'translate.py')
    if not os.path.exists(our_script):
        raise FileNotFoundError('translate.py not found in bot directory')
    import shutil
    os.makedirs(os.path.join(site_dir, 'scripts'), exist_ok=True)
    shutil.copy(our_script, translate_script)

    result = subprocess.run(
        [sys.executable, translate_script,
         '--key', groq_api_key,
         '--langs', ','.join(langs),
         '--skip-existing'],
        cwd=site_dir,
        capture_output=True,
        text=True,
        timeout=600
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-500:] if result.stderr else 'Translation failed')


def fix_lang_switcher(site_dir: str):
    """Run fix-translated-pages.py if it exists in the site."""
    script = os.path.join(site_dir, 'scripts', 'fix-translated-pages.py')
    if not os.path.exists(script):
        return
    subprocess.run(
        [sys.executable, script],
        cwd=site_dir,
        capture_output=True,
        text=True,
        timeout=120
    )


def _detect_base_url(site_dir: str) -> str:
    """Try to find the site's base URL from canonical tags."""
    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if d not in ['.git', 'node_modules']]
        for fname in files:
            if not fname.endswith('.html'):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, encoding='utf-8', errors='ignore') as f:
                html = f.read()
            m = re.search(r'<link[^>]*rel=["\']canonical["\'][^>]*href=["\']([^"\']+)["\']', html)
            if m:
                url = m.group(1)
                # Extract base: https://example.com
                base = re.match(r'(https?://[^/]+)', url)
                if base:
                    return base.group(1)
    return 'https://example.com'
