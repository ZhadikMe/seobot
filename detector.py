#!/usr/bin/env python3
"""
Detects repository structure type and normalizes it to a working site/ directory.
"""
import os
import re
import shutil
import glob


def detect_and_normalize(repo_dir: str) -> tuple[str, str]:
    """
    Detect repo structure, normalize to site_dir.
    Returns: (site_dir, description)
    """
    # 1. web.archive.org dump
    archive_dir = os.path.join(repo_dir, 'web.archive.org')
    if os.path.isdir(archive_dir):
        site_dir = os.path.join(repo_dir, 'site')
        desc = restore_from_archive(archive_dir, site_dir)
        return site_dir, desc

    # 2. Already has site/ with HTML files
    site_dir = os.path.join(repo_dir, 'site')
    if os.path.isdir(site_dir):
        html_count = len(glob.glob(os.path.join(site_dir, '**', '*.html'), recursive=True))
        if html_count > 0:
            return site_dir, f'Готовый сайт в site/ ({html_count} HTML файлов)'

    # 3. HTML files in root
    root_html = glob.glob(os.path.join(repo_dir, '*.html'))
    root_html += glob.glob(os.path.join(repo_dir, '*', 'index.html'))
    if root_html:
        return repo_dir, f'HTML файлы в корне репо ({len(root_html)} найдено)'

    raise ValueError(
        'Не удалось определить структуру репозитория.\n'
        'Поддерживается: web.archive.org дамп, site/ папка, HTML в корне.'
    )


def restore_from_archive(archive_dir: str, output_dir: str) -> str:
    """
    Extract clean HTML/CSS/JS/images from web.archive.org dump.
    Fixes archive URLs, creates proper site structure.
    """
    os.makedirs(output_dir, exist_ok=True)

    # Find snapshot timestamp dirs (e.g. 20220125170038)
    snapshots = []
    web_dir = os.path.join(archive_dir, 'web')
    if os.path.isdir(web_dir):
        for entry in os.listdir(web_dir):
            full = os.path.join(web_dir, entry)
            if os.path.isdir(full) and re.match(r'^\d{14}$', entry):
                snapshots.append(full)

    if not snapshots:
        raise ValueError('Не найдено снапшотов в web.archive.org/web/')

    # Use the latest snapshot (largest timestamp)
    snapshot_dir = sorted(snapshots)[-1]
    timestamp = os.path.basename(snapshot_dir)

    # Find the actual site URL inside snapshot
    site_root = _find_site_root(snapshot_dir)
    if not site_root:
        raise ValueError('Не удалось найти корневые HTML файлы в снапшоте')

    # Extract site domain from site_root path (e.g. .../http\uf03a\kitcarsoncolorado.com\)
    site_domain = _extract_domain_from_path(site_root)

    # Copy and fix files
    html_count = 0
    css_count = 0
    img_count = 0

    for root, dirs, files in os.walk(site_root):
        # Skip archive resource dirs
        dirs[:] = [d for d in dirs if not re.match(r'^\d{14}[a-z_]*$', d)]

        for fname in files:
            src_path = os.path.join(root, fname)
            # Compute relative path from site_root
            rel = os.path.relpath(src_path, site_root)
            dest_path = os.path.join(output_dir, rel)
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)

            if fname.endswith('.html') or fname.endswith('.htm'):
                _copy_and_fix_html(src_path, dest_path, timestamp, site_domain)
                html_count += 1
            elif fname.endswith('.css'):
                _copy_and_fix_css(src_path, dest_path, timestamp)
                css_count += 1
            elif _is_image(fname):
                shutil.copy2(src_path, dest_path)
                img_count += 1

    # Copy CSS, images, JS from timestampXX_ resource dirs
    web_dir = os.path.dirname(snapshot_dir)
    resource_counts = {'cs_': 0, 'im_': 0, 'js_': 0}
    for suffix, is_css in (('cs_', True), ('im_', False), ('js_', False)):
        res_dir = os.path.join(web_dir, timestamp + suffix)
        if not os.path.isdir(res_dir):
            continue
        for root, dirs, files in os.walk(res_dir):
            for fname in files:
                src = os.path.join(root, fname)
                rel = os.path.relpath(src, res_dir)
                local = _archive_rel_to_local_path(rel)
                if not local:
                    continue
                dest = os.path.join(output_dir, local)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                if not os.path.exists(dest):
                    if is_css and local.endswith('.css'):
                        _copy_and_fix_css(src, dest, timestamp)
                    else:
                        shutil.copy2(src, dest)
                    resource_counts[suffix] += 1

    css_count = resource_counts['cs_']
    img_count = resource_counts['im_']
    js_count = resource_counts['js_']

    return (
        f'web.archive.org снапшот от {_format_timestamp(timestamp)}\n'
        f'Извлечено: {html_count} HTML, {css_count} CSS, {img_count} изображений, {js_count} JS'
    )


def _find_site_root(snapshot_dir: str) -> str | None:
    """
    Find the directory containing the actual site root (index.html at top level).
    Structure: snapshot_dir/http[:]example.com/index.html
    Note: on Windows, ':' in directory names is stored as U+F03A (private use char).
    """
    best = None
    best_depth = 99

    for root, dirs, files in os.walk(snapshot_dir):
        rel = os.path.relpath(root, snapshot_dir)
        # Normalize: replace U+F03A back to colon for analysis
        rel_norm = rel.replace('\uf03a', ':').replace('\\', '/')
        parts = [p for p in rel_norm.split('/') if p and p != '.']
        depth = len(parts)

        # Skip too deep (want site root, not subpages)
        if depth > 4:
            dirs.clear()
            continue

        # Skip archive resource dirs (e.g. 20220125170038cs_)
        dirs[:] = [d for d in dirs if not re.match(r'^\d{14}[a-z_]+$', d)]

        if 'index.html' in files and depth < best_depth:
            best = root
            best_depth = depth

    return best


def _extract_domain_from_path(path: str) -> str | None:
    """Extract site domain from web.archive path like .../http[colon]kitcarsoncolorado.com/"""
    normalized = path.replace('\uf03a', ':').replace('\\', '/')
    # Archive paths use http:/ (single slash) on Windows, http:// elsewhere
    m = re.search(r'https?:/+([^/\s]+)', normalized)
    return m.group(1) if m else None


def _copy_and_fix_html(src: str, dest: str, timestamp: str, site_domain: str | None = None):
    """Copy HTML file, removing web.archive injection and fixing paths."""
    try:
        with open(src, encoding='utf-8', errors='ignore') as f:
            html = f.read()
    except Exception:
        shutil.copy2(src, dest)
        return

    # Remove web.archive toolbar injection
    html = re.sub(
        r'<!-- BEGIN WAYBACK TOOLBAR INSERT -->.*?<!-- END WAYBACK TOOLBAR INSERT -->',
        '', html, flags=re.DOTALL
    )
    html = re.sub(r'<script[^>]*src="[^"]*web\.archive\.org[^"]*"[^>]*>.*?</script>', '', html, flags=re.DOTALL)
    html = re.sub(r'<script[^>]*>\s*window\.RufflePlayer.*?</script>', '', html, flags=re.DOTALL)

    # Fix archive URLs: /web/TIMESTAMP[modifier]/https://domain/path
    ARCHIVE_RE = r'(?:https?://web\.archive\.org)?/web/\d{14}(?:im_|cs_|js_)?/(https?://[^\s"\'<>]+)'

    def fix_archive_url(m):
        original = m.group(1)  # full original URL, e.g. https://fonts.googleapis.com/css?...
        dm = re.match(r'https?://([^/?#]+)(.*)', original)
        if not dm:
            return ''
        domain, path = dm.group(1), dm.group(2)
        if site_domain and domain == site_domain:
            # Same-domain resource → keep as relative path
            return path if path else '/'
        else:
            # External resource (CDN, fonts, etc.) → restore full URL
            return original

    html = re.sub(ARCHIVE_RE, fix_archive_url, html)

    with open(dest, 'w', encoding='utf-8') as f:
        f.write(html)


def _copy_and_fix_css(src: str, dest: str, timestamp: str):
    """Copy CSS, fixing archive URLs inside."""
    try:
        with open(src, encoding='utf-8', errors='ignore') as f:
            css = f.read()
    except Exception:
        shutil.copy2(src, dest)
        return

    # Fix url() references
    css = re.sub(
        r'url\(["\']?(?:https?://web\.archive\.org)?/web/\d{14}(?:im_|cs_)?/https?://[^/]+(/[^)"\']*)["\']?\)',
        lambda m: f'url({m.group(1)})',
        css
    )

    with open(dest, 'w', encoding='utf-8') as f:
        f.write(css)


def _archive_rel_to_local_path(rel: str) -> str | None:
    """
    Convert web.archive resource relative path to local site path.
    On Windows, ':' is stored as U+F03A and '?' as U+F03F.
    e.g. 'http[colon]kitcarsoncolorado.com/wp-content/themes/style.css[?]ver=1.4'
    becomes 'wp-content/themes/style.css'
    """
    # Decode private-use chars back to ASCII
    normalized = rel.replace('\uf03a', ':').replace('\uf03f', '?').replace('\\', '/')
    # Drop query string
    normalized = normalized.split('?')[0]
    # Extract path after domain: http(s)://domain/path or http:/domain/path
    m = re.match(r'https?:/?/?[^/]+(/.*)', normalized)
    if not m:
        return None
    return m.group(1).lstrip('/')


def _is_image(fname: str) -> bool:
    ext = fname.lower().rsplit('.', 1)[-1] if '.' in fname else ''
    return ext in ('jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'ico')


def _format_timestamp(ts: str) -> str:
    """20220125170038 → 2022-01-25"""
    try:
        return f'{ts[0:4]}-{ts[4:6]}-{ts[6:8]}'
    except Exception:
        return ts
