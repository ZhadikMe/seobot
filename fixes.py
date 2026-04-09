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


LANG_DIRS = ['ru', 'de', 'fr', 'es', 'it', 'pt', 'pl', 'nl', 'cs', 'ro', 'sv', 'tr']


def run_all_fixes(site_dir: str, step_key: str, langs: list, groq_api_key: str,
                  site_domain: str = None, wowai_key: str = None) -> dict:
    """Dispatcher — runs a specific fix step."""
    try:
        if step_key == 'fix_archive_scripts':
            fix_archive_scripts(site_dir)
        elif step_key == 'fix_descriptions':
            fix_descriptions(site_dir, groq_api_key)
        elif step_key == 'fix_schema':
            fix_schema(site_dir, site_domain)
        elif step_key == 'fix_canonical':
            fix_canonical(site_dir, site_domain)
        elif step_key == 'fix_og_image':
            fix_og_image(site_dir, site_domain)
        elif step_key == 'fix_nofollow':
            fix_nofollow(site_dir)
        elif step_key == 'fix_robots_txt':
            fix_robots_txt(site_dir, site_domain)
        elif step_key == 'fix_hreflang_translated':
            fix_hreflang_translated(site_dir, langs, site_domain)
        elif step_key == 'fix_translations':
            fix_translations(site_dir, langs, wowai_key or groq_api_key, site_domain)
        elif step_key == 'fix_internal_links':
            fix_internal_links(site_dir)
        elif step_key == 'fix_title_refresh':
            fix_title_refresh(site_dir)
        elif step_key == 'fix_lang_switcher':
            fix_lang_switcher(site_dir)
        return {'ok': True}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def fix_archive_scripts(site_dir: str):
    """
    Remove web.archive.org injected scripts and styles from all HTML files.
    Handles sites stored as-is (site/ dir) that weren't processed by detector.py.
    """
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

    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if d not in ['.git', 'node_modules']]
        for fname in files:
            if not fname.endswith('.html'):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, encoding='utf-8', errors='ignore') as f:
                html = f.read()

            if 'archive.org' not in html:
                continue

            original = html
            for pattern in ARCHIVE_SCRIPT_PATTERNS:
                html = re.sub(pattern, '', html, flags=re.DOTALL | re.IGNORECASE)

            # Also fix archive URLs left in href/src attributes
            html = re.sub(
                r'(?:https?://web\.archive\.org)?/web/\d{14}[a-z_]*/https?://([^\s"\'<>]+)',
                lambda m: 'https://' + m.group(1),
                html
            )

            if html != original:
                with open(fpath, 'w', encoding='utf-8') as f:
                    f.write(html)


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

    # Extract main keyword from title (remove site name suffix, drop stop words)
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
        f'- End with a call-to-action (e.g. "Learn more", "Find out", "Discover", "Get started")\n'
        f'- No quotes, no markdown, no bullet points — plain text only\n\n'
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


def fix_schema(site_dir: str, site_domain: str = None):
    """Add BreadcrumbList schema to pages that don't have it."""
    BASE_URL = _detect_base_url(site_dir, site_domain)
    is_placeholder = (BASE_URL == 'https://example.com')

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
        dirs[:] = [d for d in dirs if d not in ['.git', 'node_modules']]
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
    """Add og:image to pages missing it, using first <img> found on the page."""
    BASE_URL = _detect_base_url(site_dir, site_domain)

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


def fix_robots_txt(site_dir: str, site_domain: str = None):
    """Generate robots.txt if missing. Overwrites if domain was example.com."""
    robots_path = os.path.join(site_dir, 'robots.txt')
    if os.path.exists(robots_path):
        with open(robots_path, encoding='utf-8', errors='ignore') as f:
            content = f.read()
        # Regenerate if it has wrong domain placeholder
        if 'example.com' not in content and site_domain is None:
            return

    BASE_URL = _detect_base_url(site_dir, site_domain)
    sitemap_url = BASE_URL.rstrip('/') + '/sitemap.xml'

    content = (
        'User-agent: *\n'
        'Allow: /\n'
        '\n'
        f'Sitemap: {sitemap_url}\n'
    )

    with open(robots_path, 'w', encoding='utf-8') as f:
        f.write(content)


def fix_hreflang_translated(site_dir: str, langs: list, site_domain: str = None):
    """
    Add hreflang tags to translated pages that are missing them.
    Mirrors the hreflang block from the corresponding English source page.
    """
    if not langs:
        return

    BASE_URL = _detect_base_url(site_dir, site_domain)

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


def fix_translations(site_dir: str, langs: list, api_key: str, site_domain: str = None):
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
            line = line.rstrip()
            if line:
                _log.info('[translate] %s', line)
                stderr_tail.append(line)
                if len(stderr_tail) > 50:
                    stderr_tail.pop(0)
        proc.wait(timeout=1800)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise RuntimeError('Translation timed out after 30 minutes')
    if proc.returncode != 0:
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
        dirs[:] = [d for d in dirs if d not in ['.git', 'node_modules']]
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
    LANG_DIRS = {'ru', 'de', 'fr', 'es', 'it', 'pt', 'pl', 'nl', 'cs', 'ro', 'sv', 'tr'}
    SKIP_DIRS = LANG_DIRS | {'scripts', 'images', 'css', '.git', 'node_modules'}

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


def fix_lang_switcher(site_dir: str):
    """
    Inject a floating language switcher dropdown into every HTML page.
    Detects available languages from subdirectories and builds relative links.
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
        'cs': ('🇨🇿', 'Čeština'),
        'ro': ('🇷🇴', 'Română'),
        'sv': ('🇸🇪', 'Svenska'),
    }

    # Detect which languages actually exist as subdirectories
    available_langs = ['en']  # English is always the root
    for entry in sorted(os.listdir(site_dir)):
        if entry in LANG_NAMES and os.path.isdir(os.path.join(site_dir, entry)):
            available_langs.append(entry)

    if len(available_langs) <= 1:
        return  # Nothing to switch between

    # Build CSS + JS (injected once per page)
    SWITCHER_STYLE = """
<style id="lang-switcher-style">
#lang-switcher{position:fixed;bottom:20px;right:20px;z-index:9999;font-family:sans-serif}
#lang-btn{background:#222;color:#fff;border:none;border-radius:24px;padding:8px 16px;
  font-size:14px;cursor:pointer;display:flex;align-items:center;gap:6px;
  box-shadow:0 2px 8px rgba(0,0,0,.35);transition:background .2s}
#lang-btn:hover{background:#444}
#lang-panel{display:none;position:absolute;bottom:44px;right:0;background:#fff;
  border-radius:12px;box-shadow:0 4px 20px rgba(0,0,0,.18);overflow:hidden;
  min-width:160px;max-height:320px;overflow-y:auto}
#lang-panel.open{display:block}
.lang-item{display:flex;align-items:center;gap:8px;padding:10px 16px;
  text-decoration:none;color:#222;font-size:14px;transition:background .15s}
.lang-item:hover{background:#f5f5f5}
.lang-item.active{background:#f0f7ff;font-weight:600}
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
        dirs[:] = [d for d in dirs if d not in ['.git', 'node_modules', 'scripts']]
        for fname in files:
            if not fname.endswith('.html'):
                continue
            fpath = os.path.join(root, fname)

            with open(fpath, encoding='utf-8', errors='ignore') as f:
                html = f.read()

            # Remove previously injected switcher so it gets regenerated with
            # up-to-date language list (new translations may have been added)
            if 'lang-switcher' in html:
                html = re.sub(r'<style id="lang-switcher-style">.*?</style>\s*', '', html, flags=re.DOTALL)
                html = re.sub(r'\s*<div id="lang-switcher">.*?</div>\s*<script id="lang-switcher-script">.*?</script>', '', html, flags=re.DOTALL)

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
                current_lang = 'en'
                slug = rel  # e.g. "index.html" or "about-us.html"
                to_root = '../' * depth if depth > 0 else ''

            # Build links for each language — only include langs where this page exists
            flag, name = LANG_NAMES.get(current_lang, ('🌐', current_lang.upper()))
            items_html = ''
            for lang in available_langs:
                lflag, lname = LANG_NAMES.get(lang, ('🌐', lang.upper()))
                is_active = (lang == current_lang)

                if lang == 'en':
                    href = to_root + slug if to_root else slug
                    # English is always the canonical — include it
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
            html = html.replace('</body>', switcher_html + '\n</body>', 1)

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
        dirs[:] = [d for d in dirs if d not in ['.git', 'node_modules', 'scripts']]
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
