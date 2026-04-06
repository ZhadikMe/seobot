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
                _copy_and_fix_html(src_path, dest_path, timestamp)
                html_count += 1
            elif fname.endswith('.css'):
                _copy_and_fix_css(src_path, dest_path, timestamp)
                css_count += 1
            elif _is_image(fname):
                shutil.copy2(src_path, dest_path)
                img_count += 1

    # Also copy CSS from timestamp_cs_ dirs
    for entry in os.listdir(os.path.dirname(snapshot_dir)):
        if entry.startswith(timestamp) and entry.endswith('cs_'):
            css_dir = os.path.join(os.path.dirname(snapshot_dir), entry)
            _copy_css_resources(css_dir, output_dir, timestamp)
            css_count += 1

    return (
        f'web.archive.org снапшот от {_format_timestamp(timestamp)}\n'
        f'Извлечено: {html_count} HTML, {css_count} CSS, {img_count} изображений'
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


def _copy_and_fix_html(src: str, dest: str, timestamp: str):
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
    # Remove archive script tags
    html = re.sub(r'<script[^>]*src="[^"]*web\.archive\.org[^"]*"[^>]*>.*?</script>', '', html, flags=re.DOTALL)
    html = re.sub(r'<script[^>]*>\s*window\.RufflePlayer.*?</script>', '', html, flags=re.DOTALL)

    # Fix archive URLs in href/src attributes
    # /web/20220125170038/https://example.com/page → /page
    html = re.sub(
        r'(?:https?://web\.archive\.org)?/web/\d{14}(?:im_|cs_|js_)?/(https?://[^/"\']+)',
        lambda m: '',
        html
    )
    html = re.sub(
        r'(?:https?://web\.archive\.org)?/web/\d{14}(?:im_|cs_|js_)?/https?://[^/"\']+(/[^"\']*)',
        lambda m: m.group(1),
        html
    )
    html = re.sub(
        r'(?:https?://web\.archive\.org)?/web/\d{14}(?:im_|cs_|js_)?/https?://[^/"\']+/?(["\'])',
        lambda m: '/' + m.group(1),
        html
    )

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


def _copy_css_resources(css_archive_dir: str, output_dir: str, timestamp: str):
    """Copy CSS resources from timestampcs_ directory."""
    for root, dirs, files in os.walk(css_archive_dir):
        for fname in files:
            if fname.endswith('.css'):
                src = os.path.join(root, fname)
                # Try to preserve relative path
                rel = os.path.relpath(root, css_archive_dir)
                dest_dir = os.path.join(output_dir, 'css')
                os.makedirs(dest_dir, exist_ok=True)
                dest = os.path.join(dest_dir, fname)
                if not os.path.exists(dest):
                    _copy_and_fix_css(src, dest, timestamp)


def _is_image(fname: str) -> bool:
    ext = fname.lower().rsplit('.', 1)[-1] if '.' in fname else ''
    return ext in ('jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'ico')


def _format_timestamp(ts: str) -> str:
    """20220125170038 → 2022-01-25"""
    try:
        return f'{ts[0:4]}-{ts[4:6]}-{ts[6:8]}'
    except Exception:
        return ts
