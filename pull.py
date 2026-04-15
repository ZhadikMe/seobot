#!/usr/bin/env python3
"""
pull.py — Download a web.archive.org snapshot and prepare it for SEO bot processing.

Usage:
    python pull.py <archive_url> <target_dir>

Examples:
    python pull.py "https://web.archive.org/web/20180330095801/http://elizaroseandcompany.com/" D:/elizaroseandcompany
    python pull.py "https://web.archive.org/web/20180710220942/http://www.dialacarlondon.com/" D:/dialacarlondon
"""

import os
import re
import sys
import json
import time
import shutil
import subprocess
import urllib.request
import urllib.error

sys.stdout.reconfigure(encoding='utf-8')


# ---------------------------------------------------------------------------
# Find wget executable (handles Windows non-standard install paths)
# ---------------------------------------------------------------------------

def _find_wget() -> str:
    """Return path to wget executable, searching common locations on Windows."""
    # First try PATH
    for candidate in ('wget', 'wget.exe'):
        try:
            subprocess.run([candidate, '--version'], capture_output=True, check=True)
            return candidate
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass

    # Windows-specific fallback locations
    win_paths = [
        os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\WinGet\Packages'),
        r'C:\ProgramData\chocolatey\bin',
        r'C:\Program Files\GnuWin32\bin',
        r'C:\Program Files (x86)\GnuWin32\bin',
    ]
    for base in win_paths:
        if not os.path.isdir(base):
            continue
        for root, dirs, files in os.walk(base):
            for f in files:
                if f.lower() == 'wget.exe':
                    return os.path.join(root, f)

    raise FileNotFoundError(
        'wget not found. Install it with:\n'
        '  winget install JernejSimoncic.Wget\n'
        'or on Linux: apt install wget'
    )


_WGET: str | None = None  # resolved lazily on first download


def _get_wget() -> str:
    global _WGET
    if _WGET is None:
        _WGET = _find_wget()
    return _WGET


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------

def parse_archive_url(archive_url: str) -> tuple[str, str, str]:
    """
    Parse a web.archive.org URL.
    Returns (timestamp, domain_no_www, original_host_as_wget_sees_it).

    Examples:
      https://web.archive.org/web/20180330095801/http://elizaroseandcompany.com/
        → ('20180330095801', 'elizaroseandcompany.com', 'elizaroseandcompany.com')
      https://web.archive.org/web/20180710220942/http://www.dialacarlondon.com/
        → ('20180710220942', 'dialacarlondon.com', 'www.dialacarlondon.com')
    """
    m = re.match(
        r'https?://web\.archive\.org/web/(\d+)/'   # timestamp
        r'https?://(www\.)?([^/]+)',                # optional www + domain
        archive_url
    )
    if not m:
        raise ValueError(f'Not a valid web.archive.org URL: {archive_url}')

    timestamp      = m.group(1)
    www_prefix     = m.group(2) or ''
    domain_no_www  = m.group(3).rstrip('/')
    wget_host      = www_prefix + domain_no_www   # exactly as in the original URL

    return timestamp, domain_no_www, wget_host


# ---------------------------------------------------------------------------
# CDX estimate
# ---------------------------------------------------------------------------

def _cdx_estimate(domain: str, timestamp: str) -> int:
    """
    Query archive.org CDX API to count unique URLs for this domain snapshot.
    First tries with ±1 year date filter around the snapshot timestamp;
    falls back to no date filter if that returns 0.
    Returns file count, or 0 on failure.
    """
    year = int(timestamp[:4]) if timestamp else 0

    def _query(extra: str = '') -> int:
        cdx_url = (
            f'https://web.archive.org/cdx/search/cdx'
            f'?url={domain}/*&output=json&fl=urlkey'
            f'&collapse=urlkey&limit=2000{extra}'
        )
        try:
            req = urllib.request.Request(cdx_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
            return max(len(data) - 1, 0)
        except Exception:
            return 0

    if year:
        count = _query(f'&from={year - 1}&to={year + 1}')
        if count > 0:
            return count

    return _query()  # fallback: all-time


# ---------------------------------------------------------------------------
# wget download
# ---------------------------------------------------------------------------

def download_snapshot(archive_url: str, tmp_dir: str, domain_no_www: str, wget_host: str,
                      total_estimated: int = 0, progress_cb=None) -> None:
    """
    Run wget to mirror the snapshot into tmp_dir.
    Streams wget output line-by-line to show live download progress.
    """
    domains = f'{domain_no_www},www.{domain_no_www},web.archive.org'

    cmd = [
        _get_wget(),
        '--recursive',
        '--level=inf',
        '--page-requisites',
        '--no-convert-links',   # keep original archive.org URLs; fix_archive_scripts cleans them
        '--span-hosts',
        f'--domains={domains}',
        '--timeout=30',
        '--tries=3',
        '--reject-regex', r'/(wp-json|wp-admin|wp-login|xmlrpc|feed|rss|sitemap\.xml|\?s=|\?p=|/page/\d)',
        '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        '-e', 'robots=off',
        f'--directory-prefix={tmp_dir}',
        archive_url,
    ]

    # Time limit: CDX estimate * 10 sec/file * 1.2 buffer, min 30 min, max 3 hours
    if total_estimated:
        est_min = max(1, round(total_estimated * 10 / 60))
        limit_sec = max(1800, int(total_estimated * 10 * 1.5))
        limit_min = round(limit_sec / 60)
        print(f'Скачиваем {wget_host} (~{total_estimated} файлов, ~{est_min} мин, лимит {limit_min} мин)...')
    else:
        est_min = 0
        limit_sec = 5400  # 90 min default if no CDX estimate
        print(f'Скачиваем {wget_host} (лимит 90 мин)...')

    saved_re = re.compile(r'saved \[')
    count = 0
    start_time = time.time()
    timed_out = False

    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True,
                            encoding='utf-8', errors='replace')
    for line in proc.stderr:
        if saved_re.search(line):
            count += 1
            if total_estimated:
                pct = min(count * 100 // total_estimated, 99)
                print(f'\r  Скачано: {count}/{total_estimated} [{pct}%]',
                      end='', flush=True)
            else:
                print(f'\r  Скачано: {count} файлов', end='', flush=True)
            if progress_cb and count % 10 == 0:
                try:
                    progress_cb(count, total_estimated)
                except Exception:
                    pass

        if time.time() - start_time > limit_sec:
            proc.kill()
            timed_out = True
            break

    proc.wait()
    elapsed = round((time.time() - start_time) / 60, 1)
    if timed_out:
        print(f'\r  [warn] Лимит времени достигнут ({elapsed} мин) — wget остановлен на {count} файлах')
    else:
        print(f'\r  Скачано: {count} файлов за {elapsed} мин.           ')

    # wget exits non-zero on partial downloads — that's normal, don't raise
    if proc.returncode not in (0, 8):
        print(f'[warn] wget завершился с кодом {proc.returncode} (обычно не критично)')


# ---------------------------------------------------------------------------
# Locate downloaded site root inside wget output
# ---------------------------------------------------------------------------

def find_site_root(tmp_dir: str, timestamp: str, domain_no_www: str, wget_host: str) -> str:
    """
    wget puts files in:  tmp_dir/web.archive.org/web/{timestamp}/{host}/
    The timestamp folder name may differ slightly (redirects), and the host
    folder may be www.domain or domain. We search broadly.
    """
    archive_base = os.path.join(tmp_dir, 'web.archive.org', 'web')

    if not os.path.isdir(archive_base):
        raise FileNotFoundError(
            f'wget archive dir not found: {archive_base}\n'
            'wget may have failed or the URL structure is unexpected.'
        )

    # Collect all timestamp subdirs, newest first
    ts_dirs = sorted(os.listdir(archive_base), reverse=True)

    for ts_dir in ts_dirs:
        # Only look in the pure timestamp dir (not css_/js_/im_ variants)
        if not re.match(r'^\d+$', ts_dir):
            continue
        ts_path = os.path.join(archive_base, ts_dir)
        if not os.path.isdir(ts_path):
            continue

        # wget may store files as:
        #   ts/domain/                     (direct)
        #   ts/http%3A/domain/             (URL-encoded colon)
        #   ts/https%3A/domain/
        #   ts/http%3A/www.domain/
        host_variants = [wget_host, domain_no_www, 'www.' + domain_no_www]
        scheme_prefixes = ['', 'http%3A', 'https%3A', 'http%3A/', 'https%3A/']

        for prefix in scheme_prefixes:
            for host in host_variants:
                p = os.path.join(ts_path, prefix, host) if prefix else os.path.join(ts_path, host)
                if os.path.isdir(p):
                    print(f'Found site root: {p}')
                    return p

        # Fallback: walk one level deep looking for anything with domain in name
        for sub in os.listdir(ts_path):
            sub_path = os.path.join(ts_path, sub)
            if not os.path.isdir(sub_path):
                continue
            if domain_no_www in sub:
                print(f'Found site root (fuzzy L1): {sub_path}')
                return sub_path
            # One level deeper (e.g. ts/http%3A/domain)
            for sub2 in os.listdir(sub_path):
                if domain_no_www in sub2:
                    p = os.path.join(sub_path, sub2)
                    if os.path.isdir(p):
                        print(f'Found site root (fuzzy L2): {p}')
                        return p

    raise FileNotFoundError(
        f'Could not locate site directory for "{domain_no_www}" '
        f'under {archive_base}.\nAvailable: {os.listdir(archive_base)}'
    )


# ---------------------------------------------------------------------------
# Extract: copy site root → target dir
# ---------------------------------------------------------------------------

def _copy_tree_dedup(src_dir: str, dst_dir: str, seen: set, rel_prefix: str = '') -> int:
    """Recursively copy src_dir into dst_dir, skipping already-seen relative paths."""
    os.makedirs(dst_dir, exist_ok=True)
    count = 0
    for item in os.listdir(src_dir):
        src = os.path.join(src_dir, item)
        dst = os.path.join(dst_dir, item)
        rel = (rel_prefix + '/' + item).lstrip('/')
        if os.path.isdir(src):
            count += _copy_tree_dedup(src, dst, seen, rel)
        else:
            if rel not in seen:
                shutil.copy2(src, dst)
                seen.add(rel)
                count += 1
    return count


def extract_site(site_root: str, target_dir: str) -> None:
    """
    Copy all domain files into target_dir from ALL timestamp directories.
    wget follows redirects across timestamps, so assets may land in directories
    with different timestamps than the starting snapshot. We scan every ts-dir
    under web.archive.org/web/ and collect anything belonging to our domain.
    HTML files from the main timestamp take priority over duplicates.
    """
    os.makedirs(target_dir, exist_ok=True)

    # archive_web is the .../web.archive.org/web/ directory
    archive_web = os.path.dirname(os.path.dirname(os.path.dirname(site_root)))
    domain_basename = os.path.basename(site_root)  # e.g. elizaroseandcompany.com

    # Collect all ts-dirs sorted: main timestamp first (so HTML pages take priority),
    # then all others. This ensures original pages win over redirected duplicates.
    ts_dir_name = os.path.basename(os.path.dirname(os.path.dirname(site_root)))
    ts_base = re.match(r'(\d+)', ts_dir_name).group(1)

    all_entries = sorted(os.listdir(archive_web))
    # Put main timestamp entries first
    main_entries = [e for e in all_entries if e.startswith(ts_base)]
    other_entries = [e for e in all_entries if not e.startswith(ts_base)]
    ordered_entries = main_entries + other_entries

    seen_files: set[str] = set()  # relative paths already copied (skip duplicates)
    total = 0

    for entry in ordered_entries:
        entry_path = os.path.join(archive_web, entry)
        if not os.path.isdir(entry_path):
            continue
        domain_subdir = _find_domain_subdir(entry_path, domain_basename)
        if not domain_subdir or not os.path.isdir(domain_subdir):
            continue
        n = _copy_tree_dedup(domain_subdir, target_dir, seen_files)
        if n:
            total += n
            print(f'  Copied {n} files from ts-dir: {entry}')

    print(f'Extracted {total} total files to: {target_dir}')


def _find_domain_subdir(ts_asset_dir: str, domain_basename: str) -> str | None:
    """Find the domain folder inside a timestamp asset dir (handles http%3A/ prefix)."""
    # Direct: tsXX_/domain/
    direct = os.path.join(ts_asset_dir, domain_basename)
    if os.path.isdir(direct):
        return direct
    # Via scheme prefix: tsXX_/http%3A/domain/ or tsXX_/https%3A/domain/
    for scheme in ('http%3A', 'https%3A'):
        p = os.path.join(ts_asset_dir, scheme, domain_basename)
        if os.path.isdir(p):
            return p
        # Also try www variant
        p2 = os.path.join(ts_asset_dir, scheme, 'www.' + domain_basename)
        if os.path.isdir(p2):
            return p2
    return None


# ---------------------------------------------------------------------------
# PHP → HTML conversion (for PHP sites snapshotted as rendered HTML)
# ---------------------------------------------------------------------------

def rename_php_to_html(site_dir: str) -> None:
    """
    web.archive.org serves PHP pages as rendered HTML but keeps .php extensions.
    Rename all .php files to .html and update href/src references inside HTML files.
    """
    renamed: list[tuple[str, str]] = []

    # Pass 1: rename files
    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if d not in ['.git', 'web.archive.org']]
        for fname in files:
            if fname.lower().endswith('.php'):
                src = os.path.join(root, fname)
                dst = os.path.join(root, fname[:-4] + '.html')
                if os.path.exists(dst):
                    os.remove(dst)  # .html may be a redirect stub from wget
                os.rename(src, dst)
                renamed.append((fname, fname[:-4] + '.html'))

    if not renamed:
        return

    print(f'Renamed {len(renamed)} .php → .html files')

    # Pass 2: fix .php references in HTML files
    php_ref_re = re.compile(r'(href|src|action)=(["\'])([^"\']*?)\.php(\?[^"\']*)?(\2)', re.IGNORECASE)

    def _replace_php(m):
        attr, q, path, qs, q2 = m.groups()
        qs = qs or ''
        return f'{attr}={q}{path}.html{qs}{q2}'

    fixed_count = 0
    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if d not in ['.git', 'web.archive.org']]
        for fname in files:
            if not fname.lower().endswith('.html'):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, encoding='utf-8', errors='ignore') as f:
                content = f.read()
            new_content = php_ref_re.sub(_replace_php, content)
            if new_content != content:
                with open(fpath, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                fixed_count += 1

    print(f'Fixed .php references in {fixed_count} HTML files')


# ---------------------------------------------------------------------------
# Post-download: recover assets missing from the wget run
# ---------------------------------------------------------------------------

def _recover_missing_assets(site_dir: str, domain_no_www: str, timestamp: str) -> None:
    """
    Scan all HTML files in site_dir for referenced assets (img/link/script).
    Download any missing files directly from archive.org using appropriate
    timestamp modifiers (im_ for images, cs_ for CSS, js_ for JS).
    """
    missing: set[str] = set()

    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if d not in ['.git']]
        for fname in files:
            if not fname.lower().endswith(('.html', '.php')):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, encoding='utf-8', errors='ignore') as f:
                content = f.read()

            for m in re.finditer(
                r'(?:src|href|data-src)=["\']([^"\'#\s][^"\']*)["\']',
                content, re.IGNORECASE
            ):
                path = m.group(1).strip()
                # Skip external URLs, anchors, mailto, javascript, data URIs
                if re.match(r'^(?:https?://|//|#|javascript:|data:|mailto:)', path, re.I):
                    continue
                # Skip un-cleaned archive.org paths (fix_archive_scripts handles these)
                if re.match(r'^/web/\d{14}', path):
                    continue
                # Only care about static assets (images, css, js, fonts)
                ext = os.path.splitext(path.split('?')[0])[1].lower()
                if ext not in ('.jpg', '.jpeg', '.png', '.gif', '.svg', '.ico',
                               '.webp', '.css', '.js', '.woff', '.woff2', '.ttf', '.eot'):
                    continue

                # Resolve to an absolute root-relative path
                if path.startswith('/'):
                    abs_path = path.split('?')[0].split('@')[0]
                else:
                    rel_root = os.path.relpath(root, site_dir).replace('\\', '/')
                    combined = (rel_root + '/' + path) if rel_root != '.' else path
                    abs_path = '/' + combined.split('?')[0].split('@')[0].lstrip('/')

                local_path = os.path.join(site_dir, abs_path.lstrip('/').replace('/', os.sep))
                if not os.path.exists(local_path):
                    missing.add(abs_path)

    if not missing:
        print('  No missing assets detected.')
        return

    print(f'  Found {len(missing)} missing assets — downloading from archive.org...')

    # Timestamp modifier order per file type
    def _ts_order(path: str) -> list[str]:
        ext = os.path.splitext(path)[1].lower()
        if ext in ('.jpg', '.jpeg', '.png', '.gif', '.svg', '.ico', '.webp', '.woff', '.woff2', '.ttf', '.eot'):
            return [timestamp + 'im_', timestamp]
        if ext == '.css':
            return [timestamp + 'cs_', timestamp]
        if ext == '.js':
            return [timestamp + 'js_', timestamp]
        return [timestamp]

    recovered = 0
    failed: list[str] = []

    for abs_path in sorted(missing):
        local_path = os.path.join(site_dir, abs_path.lstrip('/').replace('/', os.sep))
        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        downloaded = False
        for ts in _ts_order(abs_path):
            url = f'https://web.archive.org/web/{ts}/http://{domain_no_www}{abs_path}'
            try:
                req = urllib.request.Request(
                    url, headers={'User-Agent': 'Mozilla/5.0 (compatible)'}
                )
                with urllib.request.urlopen(req, timeout=20) as resp:
                    data = resp.read()
                with open(local_path, 'wb') as f:
                    f.write(data)
                recovered += 1
                downloaded = True
                break
            except Exception:
                continue

        if not downloaded:
            failed.append(abs_path)

    print(f'  Recovered {recovered}/{len(missing)} missing assets.')
    if failed:
        print(f'  Still missing ({len(failed)}):')
        for p in failed[:20]:
            print(f'    {p}')
        if len(failed) > 20:
            print(f'    ... and {len(failed) - 20} more')


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def pull_snapshot(archive_url: str, target_dir: str, progress_cb=None) -> str:
    """
    Full pipeline: parse URL → wget → find root → extract → php→html → fix_archive_scripts.
    Returns target_dir path.
    """
    timestamp, domain_no_www, wget_host = parse_archive_url(archive_url)
    print(f'\n{"="*60}')
    print(f'Archive URL : {archive_url}')
    print(f'Domain      : {domain_no_www}  (wget host: {wget_host})')
    print(f'Timestamp   : {timestamp}')
    print(f'Target dir  : {target_dir}')
    print(f'{"="*60}\n')

    tmp_dir = target_dir.rstrip('/\\') + '_wget_tmp'

    # 1. CDX estimate (best-effort — used only for progress display)
    print('Запрашиваем CDX для оценки размера сайта...')
    total_estimated = _cdx_estimate(domain_no_www, timestamp)
    if total_estimated:
        est_min = max(1, round(total_estimated * 10 / 60))
        print(f'CDX: ~{total_estimated} уникальных URL, ожидаемое время ~{est_min} мин\n')
    else:
        print('CDX недоступен — прогресс без оценки\n')

    # 2. Download
    download_snapshot(archive_url, tmp_dir, domain_no_www, wget_host, total_estimated, progress_cb)

    # 3. Find downloaded root
    try:
        site_root = find_site_root(tmp_dir, timestamp, domain_no_www, wget_host)
    except FileNotFoundError as e:
        print(f'\n[error] {e}')
        print(f'Leaving tmp dir intact for inspection: {tmp_dir}')
        raise

    # 3. Extract to target
    extract_site(site_root, target_dir)

    # 4. Cleanup tmp (only after successful extraction)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    print(f'Cleaned up tmp dir: {tmp_dir}')

    # 5. PHP → HTML (only if .php files exist)
    php_files = [
        f for root, _, files in os.walk(target_dir)
        for f in files if f.lower().endswith('.php')
    ]
    if php_files:
        print(f'\nDetected PHP site ({len(php_files)} .php files) — converting...')
        rename_php_to_html(target_dir)

    # 6. Rename files with wget @param suffix → strip params (e.g. main.css@v=1 → main.css)
    #    MUST run before fix_archive_scripts so .css files are named correctly when processed
    print('\nFixing wget @param filenames...')
    _fix_wget_param_filenames(target_dir, domain_no_www)

    # 7. Clean archive.org scripts/toolbar/URLs from HTML/CSS
    print('\nCleaning archive.org artifacts...')
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from fixes import fix_archive_scripts
        fix_archive_scripts(target_dir)
        print('Archive cleanup done.')
    except ImportError:
        print('[warn] fixes.py not found — skipping archive cleanup')

    # 8. Convert absolute domain URLs → relative paths in HTML/CSS
    print('Converting absolute URLs to relative...')
    _fix_absolute_to_relative(target_dir, domain_no_www)

    # 9. Recover any assets still missing after all cleanups
    print('\nChecking for missing assets...')
    _recover_missing_assets(target_dir, domain_no_www, timestamp)

    print(f'\n✅ Done: {target_dir}')
    return target_dir


def _fix_wget_param_filenames(site_dir: str, domain: str) -> None:
    """
    wget saves 'file.css?v=1' as 'file.css@v=1'.
    Rename these files to strip the @param suffix, and update references in HTML/CSS.
    """
    renames: dict[str, str] = {}  # old_name → new_name (basename only)

    # Pass 1: find and rename files
    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if d not in ['.git']]
        for fname in files:
            if '@' in fname:
                new_fname = fname.split('@')[0]  # strip @param part
                src = os.path.join(root, fname)
                dst = os.path.join(root, new_fname)
                if not os.path.exists(dst):
                    os.rename(src, dst)
                    renames[fname] = new_fname
                else:
                    os.remove(src)  # duplicate, remove @param version

    if renames:
        print(f'  Renamed {len(renames)} files (stripped @param suffix)')

    # Pass 2: fix references in HTML and CSS files
    # Build replacement pattern: old filenames (with ? or @ variants) → new name
    fixed = 0
    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if d not in ['.git']]
        for fname in files:
            if not fname.endswith(('.html', '.css', '.js')):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, encoding='utf-8', errors='ignore') as f:
                content = f.read()
            new_content = content
            for old, new in renames.items():
                # Replace both @param and ?param variants in URLs
                param_part = old[len(new):]  # e.g. '@v=1'
                q_variant = '?' + param_part[1:]  # '?v=1'
                new_content = new_content.replace(old, new)
                new_content = new_content.replace(new + q_variant, new)
            if new_content != content:
                with open(fpath, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                fixed += 1
    if fixed:
        print(f'  Fixed @param references in {fixed} files')


def _fix_absolute_to_relative(site_dir: str, domain: str) -> None:
    """
    Convert absolute URLs pointing to this domain → relative paths.
    e.g. https://elizaroseandcompany.com/css/main.css → /css/main.css
    Also handles www. variant.
    """
    # Match both domain and www.domain
    domain_re = re.compile(
        r'https?://(www\.)?' + re.escape(domain),
        re.IGNORECASE
    )

    fixed = 0
    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if d not in ['.git']]
        for fname in files:
            if not fname.endswith(('.html', '.css')):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, encoding='utf-8', errors='ignore') as f:
                content = f.read()
            new_content = domain_re.sub('', content)
            if new_content != content:
                with open(fpath, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                fixed += 1
    if fixed:
        print(f'  Converted absolute URLs to relative in {fixed} files')


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)

    archive_url = sys.argv[1]
    target_dir  = sys.argv[2]
    pull_snapshot(archive_url, target_dir)
