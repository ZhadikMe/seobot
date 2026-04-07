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


LANG_DIRS = ['ru', 'de', 'fr', 'es', 'it', 'pt']


def run_all_fixes(site_dir: str, step_key: str, langs: list, groq_api_key: str) -> dict:
    """Dispatcher — runs a specific fix step."""
    try:
        if step_key == 'fix_descriptions':
            fix_descriptions(site_dir, groq_api_key)
        elif step_key == 'fix_schema':
            fix_schema(site_dir)
        elif step_key == 'fix_canonical':
            fix_canonical(site_dir)
        elif step_key == 'fix_og_image':
            fix_og_image(site_dir)
        elif step_key == 'fix_nofollow':
            fix_nofollow(site_dir)
        elif step_key == 'fix_robots_txt':
            fix_robots_txt(site_dir)
        elif step_key == 'fix_hreflang_translated':
            fix_hreflang_translated(site_dir, langs)
        elif step_key == 'fix_translations':
            fix_translations(site_dir, langs, groq_api_key)
        elif step_key == 'fix_lang_switcher':
            fix_lang_switcher(site_dir)
        return {'ok': True}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def fix_descriptions(site_dir: str, groq_api_key: str = None):
    """
    Generate unique page-specific descriptions via Groq (if key provided),
    or trim descriptions longer than 155 chars.
    Also syncs og:description and twitter:description.
    """
    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs
                   if d not in LANG_DIRS + ['scripts', 'images', 'css', '.git', 'node_modules']]
        for fname in files:
            if not fname.endswith('.html'):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, encoding='utf-8', errors='ignore') as f:
                html = f.read()

            original = html

            # Try to generate a unique description via Groq
            new_desc = None
            if groq_api_key:
                try:
                    new_desc = _generate_description(html, groq_api_key)
                except Exception:
                    pass

            if new_desc:
                # Replace or insert meta description
                if re.search(r'<meta[^>]*name=["\']description["\']', html, re.IGNORECASE):
                    html = re.sub(
                        r'(<meta[^>]*name=["\']description["\'][^>]*content=")[^"]*(")',
                        lambda m: m.group(1) + new_desc + m.group(2),
                        html, flags=re.IGNORECASE
                    )
                else:
                    html = html.replace(
                        '</head>',
                        f'<meta name="description" content="{new_desc}">\n</head>', 1
                    )
            else:
                # Fallback: just trim if too long
                html = re.sub(
                    r'(<meta[^>]*name=["\']description["\'][^>]*content=")([^"]{156,})(")',
                    lambda m: m.group(1) + (m.group(2)[:152].rsplit(' ', 1)[0] + '...') + m.group(3),
                    html, flags=re.IGNORECASE
                )

            # Sync og:description
            desc_m = re.search(r'<meta[^>]*name=["\']description["\'][^>]*content="([^"]+)"', html, re.IGNORECASE)
            if desc_m:
                desc_val = desc_m.group(1)
                if re.search(r'og:description', html):
                    html = re.sub(
                        r'(<meta[^>]*property=["\']og:description["\'][^>]*content=")[^"]*(")',
                        lambda m: m.group(1) + desc_val + m.group(2),
                        html
                    )
                else:
                    html = html.replace(
                        '</head>',
                        f'<meta property="og:description" content="{desc_val}">\n</head>', 1
                    )

            if html != original:
                with open(fpath, 'w', encoding='utf-8') as f:
                    f.write(html)


def _generate_description(html: str, groq_api_key: str) -> str | None:
    """Use Groq to generate a unique 120-155 char page description."""
    import urllib.request

    # Extract page text
    title_m = re.search(r'<title>([^<]+)</title>', html)
    title = title_m.group(1).strip() if title_m else ''

    # Get main content text
    body_m = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL | re.IGNORECASE)
    if body_m:
        text = re.sub(r'<script[^>]*>.*?</script>', '', body_m.group(1), flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        text = text[:800]
    else:
        text = ''

    if not text or len(text) < 50:
        return None

    # Check if description already exists and is unique enough
    desc_m = re.search(r'<meta[^>]*name=["\']description["\'][^>]*content="([^"]+)"', html, re.IGNORECASE)
    existing = desc_m.group(1).strip() if desc_m else ''

    prompt = (
        f'Write a unique meta description for this webpage in 120-155 characters. '
        f'No quotes, no markdown. Just the description text.\n\n'
        f'Page title: {title}\n'
        f'Page content: {text}'
    )

    payload = json.dumps({
        'model': 'llama-3.1-8b-instant',
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': 80,
        'temperature': 0.7,
    }).encode()

    req = urllib.request.Request(
        'https://api.groq.com/openai/v1/chat/completions',
        data=payload,
        headers={
            'Authorization': f'Bearer {groq_api_key}',
            'Content-Type': 'application/json',
        }
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())

    result = data['choices'][0]['message']['content'].strip().strip('"').strip("'")

    # Validate: must be different from existing, right length
    if existing and result.lower()[:50] == existing.lower()[:50]:
        return None
    if len(result) < 80 or len(result) > 170:
        return None

    return result[:155]


def fix_schema(site_dir: str):
    """Add BreadcrumbList schema to pages that don't have it."""
    BASE_URL = _detect_base_url(site_dir)

    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs
                   if d not in LANG_DIRS + ['scripts', 'images', 'css', '.git', 'node_modules']]
        for fname in files:
            if not fname.endswith('.html'):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, encoding='utf-8', errors='ignore') as f:
                html = f.read()

            if 'BreadcrumbList' in html:
                # Fix wrong domain (example.com) in existing breadcrumbs
                if 'example.com' in html:
                    html = html.replace('https://example.com', BASE_URL)
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


def fix_canonical(site_dir: str):
    """Add <link rel="canonical"> to every HTML page that's missing it."""
    BASE_URL = _detect_base_url(site_dir)

    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if d not in ['.git', 'node_modules']]
        for fname in files:
            if not fname.endswith('.html'):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, encoding='utf-8', errors='ignore') as f:
                html = f.read()

            if re.search(r'rel=["\']canonical["\']', html, re.IGNORECASE):
                continue

            rel = fpath.replace(site_dir, '').replace(os.sep, '/').lstrip('/')
            # Build canonical URL: strip index.html, add trailing slash
            url_path = '/' + rel
            if url_path.endswith('/index.html'):
                url_path = url_path[:-len('index.html')]
            elif url_path.endswith('.html'):
                url_path = url_path[:-len('.html')] + '/'

            canonical_url = BASE_URL.rstrip('/') + url_path

            tag = f'<link rel="canonical" href="{canonical_url}">\n'
            html = html.replace('</head>', tag + '</head>', 1)

            with open(fpath, 'w', encoding='utf-8') as f:
                f.write(html)


def fix_og_image(site_dir: str):
    """Add og:image to pages missing it, using first <img> found on the page."""
    BASE_URL = _detect_base_url(site_dir)

    # Find a fallback image from the whole site
    fallback_img = _find_fallback_og_image(site_dir, BASE_URL)

    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if d not in ['.git', 'node_modules']]
        for fname in files:
            if not fname.endswith('.html'):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, encoding='utf-8', errors='ignore') as f:
                html = f.read()

            if re.search(r'og:image', html):
                continue

            # Try to find an image on this page
            img_m = re.search(r'<img[^>]+src=["\']([^"\']+\.(jpg|jpeg|png|webp|gif))["\']', html, re.IGNORECASE)
            if img_m:
                src = img_m.group(1)
                if src.startswith('http'):
                    img_url = src
                elif src.startswith('/'):
                    img_url = BASE_URL.rstrip('/') + src
                else:
                    img_url = BASE_URL.rstrip('/') + '/' + src
            elif fallback_img:
                img_url = fallback_img
            else:
                continue

            tag = f'<meta property="og:image" content="{img_url}">\n'
            html = html.replace('</head>', tag + '</head>', 1)

            # Also add og:title and og:url if missing
            if not re.search(r'og:title', html):
                title_m = re.search(r'<title>([^<]+)</title>', html)
                if title_m:
                    og_title = f'<meta property="og:title" content="{title_m.group(1).strip()}">\n'
                    html = html.replace('</head>', og_title + '</head>', 1)

            if not re.search(r'og:url', html):
                canon_m = re.search(r'<link[^>]*rel=["\']canonical["\'][^>]*href=["\']([^"\']+)["\']', html, re.IGNORECASE)
                if canon_m:
                    og_url = f'<meta property="og:url" content="{canon_m.group(1)}">\n'
                    html = html.replace('</head>', og_url + '</head>', 1)

            with open(fpath, 'w', encoding='utf-8') as f:
                f.write(html)


def _find_fallback_og_image(site_dir: str, base_url: str) -> str | None:
    """Find any image in the site to use as fallback og:image."""
    import glob
    for ext in ('*.jpg', '*.jpeg', '*.png', '*.webp'):
        imgs = glob.glob(os.path.join(site_dir, '**', ext), recursive=True)
        if imgs:
            rel = os.path.relpath(imgs[0], site_dir).replace(os.sep, '/')
            return base_url.rstrip('/') + '/' + rel
    return None


def fix_nofollow(site_dir: str):
    """Add rel="nofollow noopener noreferrer" to all external links."""
    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if d not in ['.git', 'node_modules']]
        for fname in files:
            if not fname.endswith('.html'):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, encoding='utf-8', errors='ignore') as f:
                html = f.read()

            original = html

            def add_nofollow(m):
                tag = m.group(0)
                href_m = re.search(r'href=["\']([^"\']+)["\']', tag)
                if not href_m:
                    return tag
                href = href_m.group(1)
                if not href.startswith('http'):
                    return tag
                # Already has rel
                if re.search(r'\brel=', tag):
                    # Add nofollow if not already there
                    if 'nofollow' not in tag:
                        tag = re.sub(
                            r'(rel=["\'])([^"\']*?)(["\'])',
                            lambda r: r.group(1) + r.group(2).strip() + ' nofollow noopener noreferrer' + r.group(3),
                            tag
                        )
                    return tag
                # No rel — add it
                return tag.rstrip('>').rstrip('/').rstrip() + ' rel="nofollow noopener noreferrer">'

            html = re.sub(r'<a\s[^>]+>', add_nofollow, html)

            if html != original:
                with open(fpath, 'w', encoding='utf-8') as f:
                    f.write(html)


def fix_robots_txt(site_dir: str):
    """Generate robots.txt if missing."""
    robots_path = os.path.join(site_dir, 'robots.txt')
    if os.path.exists(robots_path):
        return

    BASE_URL = _detect_base_url(site_dir)
    sitemap_url = BASE_URL.rstrip('/') + '/sitemap.xml'

    content = (
        'User-agent: *\n'
        'Allow: /\n'
        '\n'
        f'Sitemap: {sitemap_url}\n'
    )

    with open(robots_path, 'w', encoding='utf-8') as f:
        f.write(content)


def fix_hreflang_translated(site_dir: str, langs: list):
    """
    Add hreflang tags to translated pages that are missing them.
    Mirrors the hreflang block from the corresponding English source page.
    """
    if not langs:
        return

    BASE_URL = _detect_base_url(site_dir)

    for lang in langs:
        lang_dir = os.path.join(site_dir, lang)
        if not os.path.isdir(lang_dir):
            continue

        for root, dirs, files in os.walk(lang_dir):
            dirs[:] = [d for d in dirs if d not in ['.git', 'node_modules']]
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
                        f'<link rel="alternate" hreflang="en" href="{BASE_URL}/{rel_path}">',
                        f'<link rel="alternate" hreflang="{lang}" href="{BASE_URL}/{lang}/{rel_path}">',
                        f'<link rel="alternate" hreflang="x-default" href="{BASE_URL}/{rel_path}">',
                    ]

                block = '\n'.join(hreflang_tags) + '\n'
                html = html.replace('</head>', block + '</head>', 1)

                with open(fpath, 'w', encoding='utf-8') as f:
                    f.write(html)


def fix_translations(site_dir: str, langs: list, groq_api_key: str):
    """Run translation script on the site directory."""
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
    """Try to find the site's base URL from canonical tags or sitemap."""
    # Try sitemap.xml first
    sitemap = os.path.join(site_dir, 'sitemap.xml')
    if os.path.exists(sitemap):
        with open(sitemap, encoding='utf-8', errors='ignore') as f:
            content = f.read()
        m = re.search(r'<loc>(https?://[^/]+)', content)
        if m:
            return m.group(1)

    # Try canonical tags
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
                base = re.match(r'(https?://[^/]+)', url)
                if base:
                    return base.group(1)

    return 'https://example.com'
