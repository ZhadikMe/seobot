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
    Uses the snapshot's calendar year (YYYYMMDD format) + statuscode:200 filter.
    Falls back to all-time query if the year query returns 0.
    Returns file count, or 0 on failure.
    """
    year = timestamp[:4] if timestamp else ''

    def _query(extra: str = '') -> int:
        cdx_url = (
            f'https://web.archive.org/cdx/search/cdx'
            f'?url={domain}/*&output=json&fl=urlkey'
            f'&collapse=urlkey&matchType=domain'
            f'&filter=statuscode:200&limit=1000{extra}'
        )
        for attempt in range(3):
            try:
                req = urllib.request.Request(cdx_url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read())
                return max(len(data) - 1, 0)
            except Exception:
                if attempt < 2:
                    time.sleep(3)
        return 0

    if year:
        return _query(f'&from={year}0101&to={year}1231')

    return 0


# ---------------------------------------------------------------------------
# wget download
# ---------------------------------------------------------------------------

def download_snapshot(archive_url: str, tmp_dir: str, domain_no_www: str, wget_host: str,
                      total_estimated: int = 0, progress_cb=None) -> None:
    """
    Single-pass wget download: HTML pages + CSS/JS/images together.
    --page-requisites fetches assets for every page in one run, halving
    the number of requests to archive.org vs the old two-pass approach
    (which triggered archive.org IP rate-limiting before pass 2 started).
    """
    domains = f'{domain_no_www},www.{domain_no_www},web.archive.org'

    base_flags = [
        '--no-convert-links',
        '--span-hosts',
        f'--domains={domains}',
        '--timeout=30',
        '--tries=3',
        '--wait=3',           # 3 sec between requests — avoids archive.org rate limiting on cloud IPs
        '--random-wait',      # randomise 0.5x–1.5x wait (1.5–4.5 sec) so it looks less like a bot
        '--reject-regex', r'/(wp-json|wp-admin|wp-login|xmlrpc|feed|rss|sitemap\.xml|\?s=|\?p=|/page/\d)',
        '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        '-e', 'robots=off',
        f'--directory-prefix={tmp_dir}',
    ]

    # Asset time limit: estimate * 1.5 + 5 min buffer, min 10 min
    if total_estimated:
        asset_limit_sec = max(600, int(total_estimated * 2 * 1.5) + 300)
        print(f'Скачиваем {wget_host} (лимит ресурсов {round(asset_limit_sec/60)} мин)...')
    else:
        asset_limit_sec = 1800  # 30 min default
        print(f'Скачиваем {wget_host} (лимит ресурсов 30 мин)...')

    saved_re = re.compile(r'saved \[')
    start_time = time.time()

    def _run_wget(extra_flags: list, phase_limit_sec: float | None,
                  label: str, count_offset: int = 0) -> int:
        """Run wget with extra_flags, return number of files saved."""
        cmd = [_get_wget()] + base_flags + extra_flags + [archive_url]
        print(f'  wget cmd: {" ".join(cmd[:8])} ...')  # log first 8 args for debugging
        count = 0
        timed_out = False
        phase_start = time.time()   # each pass has its own clock
        proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True,
                                encoding='utf-8', errors='replace')
        saved_files: list[str] = []
        error_re = re.compile(r'ERROR\s+\d+|failed:|Unable to|cannot open', re.IGNORECASE)
        for line in proc.stderr:
            line_s = line.rstrip()
            if saved_re.search(line_s):
                count += 1
                total_done = count_offset + count
                # Extract saved filename for logging
                fname_m = re.search(r'"([^"]+)"', line_s)
                if fname_m:
                    saved_files.append(fname_m.group(1))
                print(f'\r  {label}: {count} файлов', end='', flush=True)
                if progress_cb and total_done % 10 == 0:
                    try:
                        progress_cb(total_done, total_estimated)
                    except Exception:
                        pass
            elif error_re.search(line_s):
                print(f'\n  [wget error] {line_s}')
            if phase_limit_sec and (time.time() - phase_start) > phase_limit_sec:
                proc.kill()
                timed_out = True
                break
        proc.wait()
        if timed_out:
            elapsed = round((time.time() - phase_start) / 60, 1)
            print(f'\r  [warn] Лимит времени ({elapsed} мин) — остановлено на {count} файлах')
        # Log sample of what was saved (useful for diagnosing missing CSS/images)
        css_saved = [f for f in saved_files if f.endswith(('.css', '.js', '.png', '.jpg', '.svg'))]
        if css_saved:
            print(f'\n  Примеры скачанных ресурсов ({len(css_saved)}):')
            for f in css_saved[:10]:
                print(f'    {f}')
            if len(css_saved) > 10:
                print(f'    ... и ещё {len(css_saved) - 10}')
        elif count > 0:
            print(f'\n  CSS/images в этом проходе: 0 (скачано только HTML/прочее)')
        return count

    # ── Single pass: HTML + CSS/JS/images together ───────────────────────────
    # --page-requisites fetches CSS/JS/images for every page it crawls.
    # Single pass halves the number of requests to archive.org compared to the
    # old two-pass approach (pass1: pages only, pass2: assets), which was
    # triggering archive.org IP rate-limiting before pass 2 even started.
    print('Скачиваем страницы и ресурсы (один проход)...')
    total_count = _run_wget(
        extra_flags=[
            '--recursive', '--level=inf',
            '--page-requisites',
        ],
        phase_limit_sec=asset_limit_sec,
        label='Файлов',
    )

    elapsed = round((time.time() - start_time) / 60, 1)
    print(f'\r  Итого: {total_count} файлов за {elapsed} мин.           ')

    # wget exits non-zero on partial downloads — that's normal, ignore here


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
    print(f'[find_site_root] archive_base={archive_base}')
    print(f'[find_site_root] ts_dirs: {ts_dirs[:5]}')

    for ts_dir in ts_dirs:
        # Only look in the pure timestamp dir (not css_/js_/im_ variants)
        if not re.match(r'^\d+$', ts_dir):
            continue
        ts_path = os.path.join(archive_base, ts_dir)
        if not os.path.isdir(ts_path):
            continue

        try:
            ts_children = os.listdir(ts_path)
            print(f'[find_site_root] {ts_dir}/ children: {ts_children}')
        except Exception:
            pass

        # wget may store files as:
        #   ts/domain/                     (direct)
        #   ts/http%3A/domain/             (URL-encoded colon)
        #   ts/https%3A/domain/
        #   ts/http%3A/www.domain/
        host_variants = [wget_host, domain_no_www, 'www.' + domain_no_www]
        # http%3A = Windows wget URL-encoding; http: = Linux wget real colon
        scheme_prefixes = ['', 'http%3A', 'https%3A', 'http%3A/', 'https%3A/', 'http:', 'https:']

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
    """Recursively copy src_dir into dst_dir, skipping already-seen relative paths.

    Directories are always processed before files so that when wget downloads
    both an extensionless file (e.g. 'events') AND a directory ('events/')
    for the same CMS slug, the directory wins.

    If a FILE already exists at dst_dir when we need to create a DIRECTORY
    (happens when http%3A has an extensionless page and https%3A has a
    same-named directory with subpages), the file is promoted to become
    directory/index.html so both the page content and subpages are preserved.
    """
    if os.path.isfile(dst_dir):
        # Promote the existing file to directory/index.html
        tmp_path = dst_dir + '.__promoting__'
        os.rename(dst_dir, tmp_path)
        os.makedirs(dst_dir)
        index_path = os.path.join(dst_dir, 'index.html')
        if not os.path.exists(index_path):
            os.rename(tmp_path, index_path)
            rel_idx = (rel_prefix + '/index.html').lstrip('/')
            seen.add(rel_idx)
        else:
            os.remove(tmp_path)
    os.makedirs(dst_dir, exist_ok=True)
    count = 0
    items = os.listdir(src_dir)
    # Process directories first so they take precedence over same-named files
    items.sort(key=lambda x: (0 if os.path.isdir(os.path.join(src_dir, x)) else 1, x))
    for item in items:
        src = os.path.join(src_dir, item)
        dst = os.path.join(dst_dir, item)
        rel = (rel_prefix + '/' + item).lstrip('/')
        if os.path.isdir(src):
            count += _copy_tree_dedup(src, dst, seen, rel)
        else:
            if rel not in seen and not os.path.isdir(dst):
                shutil.copy2(src, dst)
                seen.add(rel)
                count += 1
    return count


def extract_site(site_root: str, target_dir: str, archive_web: str | None = None) -> None:
    """
    Copy all domain files into target_dir from ALL timestamp directories.
    wget follows redirects across timestamps, so assets may land in directories
    with different timestamps than the starting snapshot. We scan every ts-dir
    under web.archive.org/web/ and collect anything belonging to our domain.
    HTML files from the main timestamp take priority over duplicates.

    archive_web: the .../web.archive.org/web/ directory. If not given, computed
                 from site_root by walking up until a 'web.archive.org' segment is found.
    """
    os.makedirs(target_dir, exist_ok=True)

    domain_basename = os.path.basename(site_root)  # e.g. elizaroseandcompany.com

    if archive_web is None:
        # Walk up from site_root to find the .../web.archive.org/web/ directory.
        # site_root may be 2 or 3 levels below archive_web depending on whether
        # wget created a scheme prefix dir (http: / http%3A) or not.
        p = site_root
        while True:
            parent = os.path.dirname(p)
            if parent == p:
                raise FileNotFoundError(f'Could not find web.archive.org/web in: {site_root}')
            if os.path.basename(parent) == 'web' and os.path.basename(os.path.dirname(parent)) == 'web.archive.org':
                archive_web = parent
                break
            p = parent

    # Collect all ts-dirs sorted: main timestamp first (so HTML pages take priority),
    # then all others. This ensures original pages win over redirected duplicates.
    # Find which ts-dir contains site_root
    rel = os.path.relpath(site_root, archive_web)  # e.g. TIMESTAMP/http:/domain or TIMESTAMP/domain
    ts_dir_name = rel.split(os.sep)[0]
    ts_base = re.match(r'(\d+)', ts_dir_name).group(1)

    all_entries = sorted(os.listdir(archive_web))
    # Put main timestamp entries first
    main_entries = [e for e in all_entries if e.startswith(ts_base)]
    other_entries = [e for e in all_entries if not e.startswith(ts_base)]
    ordered_entries = main_entries + other_entries

    seen_files: set[str] = set()  # relative paths already copied (skip duplicates)
    total = 0

    print(f'[extract] archive_web={archive_web}')
    print(f'[extract] domain_basename={domain_basename}, ts_base={ts_base}')
    print(f'[extract] ts-dirs to scan ({len(ordered_entries)}): {ordered_entries[:10]}{"..." if len(ordered_entries) > 10 else ""}')

    for entry in ordered_entries:
        entry_path = os.path.join(archive_web, entry)
        if not os.path.isdir(entry_path):
            continue
        domain_subdirs = _find_domain_subdirs(entry_path, domain_basename)
        if not domain_subdirs:
            # Log what's actually in this timestamp dir (for debugging)
            try:
                children = os.listdir(entry_path)[:6]
                print(f'  [extract] {entry}: no domain dirs found (has: {children})')
                # Show one level deeper so we can see what's inside scheme dirs
                for child in children:
                    child_path = os.path.join(entry_path, child)
                    if os.path.isdir(child_path):
                        grandchildren = os.listdir(child_path)[:8]
                        print(f'  [extract]   {child}/: {grandchildren}')
            except Exception:
                pass
        for domain_subdir in domain_subdirs:
            n = _copy_tree_dedup(domain_subdir, target_dir, seen_files)
            print(f'  [extract] {entry}/{os.path.relpath(domain_subdir, entry_path)}: copied {n} files')
            total += n

    print(f'Extracted {total} total files to: {target_dir}')


def _find_domain_subdirs(ts_asset_dir: str, domain_basename: str) -> list[str]:
    """Find ALL domain folders inside a timestamp asset dir.

    wget may save http:// and https:// URLs into separate scheme subdirs
    (http%3A/ and https%3A/ on Windows; http:/ and https:/ on Linux).
    Both may contain different content (e.g. http has extensionless files,
    https has subpage directories), so we must return ALL of them.

    Handles both Linux wget (http:/ prefix) and Windows wget (http%3A/ prefix).
    """
    found: list[str] = []
    seen: set[str] = set()

    def _add(p: str) -> None:
        if os.path.isdir(p) and p not in seen:
            seen.add(p)
            found.append(p)

    # Direct: tsXX_/domain/ (no scheme prefix)
    _add(os.path.join(ts_asset_dir, domain_basename))

    # Via scheme prefix — collect ALL matching variants
    no_www = domain_basename[4:] if domain_basename.startswith('www.') else domain_basename
    www_variant = 'www.' + no_www
    for scheme in ('http:', 'https:', 'http%3A', 'https%3A'):
        for host in (domain_basename, no_www, www_variant):
            _add(os.path.join(ts_asset_dir, scheme, host))

    return found


# Keep old name as shim for any callers that expect a single result
def _find_domain_subdir(ts_asset_dir: str, domain_basename: str) -> str | None:
    results = _find_domain_subdirs(ts_asset_dir, domain_basename)
    return results[0] if results else None


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
# Extensionless HTML detection and rename (WordPress / CMS sites)
# ---------------------------------------------------------------------------

def rename_extensionless_html(site_dir: str) -> None:
    """
    WordPress and other CMSes use extensionless URLs like /events/, /galleries/.
    wget saves these as files with no extension (e.g. 'events', 'galleries').
    Detect them by checking file content, rename to .html, and update references.
    """
    # Extensions we know are not HTML — skip anything that already has these
    SKIP_EXTS = {
        '.html', '.htm', '.php', '.css', '.js', '.json', '.xml', '.txt',
        '.jpg', '.jpeg', '.png', '.gif', '.ico', '.svg', '.webp', '.bmp',
        '.woff', '.woff2', '.ttf', '.eot', '.otf',
        '.mp4', '.mp3', '.avi', '.mov', '.pdf', '.zip', '.gz', '.tar',
        '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    }

    HTML_SIGS = (b'<!DOCTYPE', b'<!doctype', b'<html', b'<HTML')

    renamed: list[tuple[str, str]] = []  # (old_rel_path, new_rel_path)

    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if d not in ['.git']]
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext in SKIP_EXTS:
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, 'rb') as f:
                    header = f.read(64)
            except OSError:
                continue
            # Check if it looks like HTML
            if not any(header.startswith(sig) for sig in HTML_SIGS):
                continue
            new_fname = fname + '.html'
            new_path = os.path.join(root, new_fname)
            if os.path.exists(new_path):
                continue
            os.rename(fpath, new_path)
            rel_old = os.path.relpath(fpath, site_dir).replace('\\', '/')
            rel_new = os.path.relpath(new_path, site_dir).replace('\\', '/')
            renamed.append((rel_old, rel_new))

    if renamed:
        print(f'Renamed {len(renamed)} extensionless → .html files')

    # Fix ALL extensionless href links regardless of whether any files were renamed.
    # Checks what actually exists on disk: if /slug.html exists → /slug.html,
    # if /slug/ is a directory → /slug/ (trailing slash), otherwise leave as-is.
    # renamed_basenames kept for reference but disk-check is authoritative.
    renamed_basenames = {os.path.splitext(os.path.basename(r[1]))[0] for r in renamed}  # noqa

    # Fix ALL extensionless href links: /slug or /slug/ → /slug.html or /slug/
    # We look at what actually exists on disk to decide which form to use.
    ref_re = re.compile(
        r'(href)=(["\'])(/[^"\'?#]*?)(/)?(\2)',
        re.IGNORECASE
    )

    def _fix_ref(m):
        attr, q, path, slash, q2 = m.groups()
        # Only touch paths whose last segment has no extension
        bare = path.rstrip('/')
        seg = bare.split('/')[-1]
        if '.' in seg:
            return m.group(0)  # already has extension, leave alone
        # Resolve to local filesystem path
        local_as_file = os.path.join(site_dir, bare.lstrip('/').replace('/', os.sep))
        local_as_dir  = local_as_file  # same path, but we check isdir vs isfile
        html_file     = local_as_file + '.html'
        if os.path.isfile(html_file):
            # Extensionless file was renamed to .html
            return f'{attr}={q}{bare}.html{q2}'
        if os.path.isdir(local_as_dir):
            # It's a directory — ensure trailing slash so web server finds index.html
            return f'{attr}={q}{bare}/{q2}'
        # Unknown — leave as-is
        return m.group(0)

    fixed = 0
    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if d not in ['.git']]
        for fname in files:
            if not fname.lower().endswith('.html'):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, encoding='utf-8', errors='ignore') as f:
                content = f.read()
            new_content = ref_re.sub(_fix_ref, content)
            if new_content != content:
                with open(fpath, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                fixed += 1
    if fixed:
        print(f'  Fixed extensionless href links in {fixed} HTML files')


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

    # Try both no-www and www variants — archive.org may store assets under either hostname
    www_domain = f'www.{domain_no_www}' if not domain_no_www.startswith('www.') else domain_no_www
    domain_variants = [domain_no_www, www_domain] if www_domain != domain_no_www else [domain_no_www]

    # Brief pause before starting recovery — wget may have triggered rate limiting
    print('  Пауза 10 сек перед recover (anti-rate-limit)...')
    time.sleep(10)

    for abs_path in sorted(missing):
        local_path = os.path.join(site_dir, abs_path.lstrip('/').replace('/', os.sep))
        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        downloaded = False
        for ts in _ts_order(abs_path):
            for host in domain_variants:
                url = f'https://web.archive.org/web/{ts}/http://{host}{abs_path}'
                try:
                    req = urllib.request.Request(
                        url, headers={'User-Agent': 'Mozilla/5.0 (compatible)'}
                    )
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        data = resp.read()
                    with open(local_path, 'wb') as f:
                        f.write(data)
                    recovered += 1
                    downloaded = True
                    time.sleep(0.5)  # polite pause between successful downloads
                    break
                except Exception as e:
                    print(f'  [recover] {url}: {e}')
                    time.sleep(1.0)  # wait longer on error before retrying
                    continue
            if downloaded:
                break

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

def pull_snapshot(archive_url: str, target_dir: str, progress_cb=None,
                  total_estimated: int = 0) -> str:
    """
    Full pipeline: parse URL → wget → find root → extract → php→html → fix_archive_scripts.
    Returns target_dir path.

    total_estimated: pre-computed CDX count (pass from bot to avoid a second CDX call).
                     If 0, will query CDX here (used when called from CLI).
    """
    timestamp, domain_no_www, wget_host = parse_archive_url(archive_url)
    print(f'\n{"="*60}')
    print(f'Archive URL : {archive_url}')
    print(f'Domain      : {domain_no_www}  (wget host: {wget_host})')
    print(f'Timestamp   : {timestamp}')
    print(f'Target dir  : {target_dir}')
    print(f'{"="*60}\n')

    tmp_dir = target_dir.rstrip('/\\') + '_wget_tmp'

    # 1. CDX estimate — only query if not already provided by bot
    if not total_estimated:
        print('Запрашиваем CDX для оценки размера сайта...')
        total_estimated = _cdx_estimate(domain_no_www, timestamp)
    if total_estimated:
        est_min = max(1, round(total_estimated * 2 / 60))
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

    # 4. Extract to target
    # Pass archive_web explicitly so extract_site doesn't have to guess depth from site_root
    archive_web_dir = os.path.join(tmp_dir, 'web.archive.org', 'web')
    extract_site(site_root, target_dir, archive_web=archive_web_dir)

    # 5. Cleanup tmp (only after successful extraction)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    print(f'Cleaned up tmp dir: {tmp_dir}')

    # 6. PHP → HTML (only if .php files exist)
    php_files = [
        f for root, _, files in os.walk(target_dir)
        for f in files if f.lower().endswith('.php')
    ]
    if php_files:
        print(f'\nDetected PHP site ({len(php_files)} .php files) — converting...')
        rename_php_to_html(target_dir)

    # 6. Rename extensionless HTML files (WordPress /events/, /galleries/, etc.)
    print('\nDetecting extensionless HTML files...')
    rename_extensionless_html(target_dir)

    # 7. Rename files with wget @param suffix → strip params (e.g. main.css@v=1 → main.css)
    #    MUST run before fix_archive_scripts so .css files are named correctly when processed
    print('\nFixing wget @param filenames...')
    _fix_wget_param_filenames(target_dir, domain_no_www)

    # 8. Clean archive.org scripts/toolbar/URLs from HTML/CSS
    print('\nCleaning archive.org artifacts...')
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from fixes import fix_archive_scripts
        fix_archive_scripts(target_dir)
        print('Archive cleanup done.')
    except ImportError:
        print('[warn] fixes.py not found — skipping archive cleanup')

    # 9. Convert absolute domain URLs → relative paths in HTML/CSS
    print('Converting absolute URLs to relative...')
    _fix_absolute_to_relative(target_dir, domain_no_www)

    # 10. Recover any assets still missing after all cleanups
    print('\nChecking for missing assets...')
    _recover_missing_assets(target_dir, domain_no_www, timestamp)

    print(f'\n✅ Done: {target_dir}')
    return target_dir


def _fix_wget_param_filenames(site_dir: str, domain: str) -> None:
    """
    wget saves query-param filenames differently per OS:
      Windows: 'file.css?v=1' → 'file.css@v=1'  (? replaced with @)
      Linux:   'file.css?v=1' → 'file.css?v=1'  (? kept as-is, valid on Linux fs)
    Rename these files to strip the param suffix, and update references in HTML/CSS.
    """
    renames: dict[str, str] = {}  # old_name → new_name (basename only)

    # Pass 1: find and rename files
    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if d not in ['.git']]
        for fname in files:
            # Detect separator: @ (Windows wget) or ? (Linux wget)
            sep = None
            if '@' in fname:
                sep = '@'
            elif '?' in fname:
                sep = '?'
            if sep is None:
                continue
            new_fname = fname.split(sep)[0]  # strip @param or ?param part
            if not new_fname:
                continue
            src = os.path.join(root, fname)
            dst = os.path.join(root, new_fname)
            if not os.path.exists(dst):
                os.rename(src, dst)
                renames[fname] = new_fname
            else:
                os.remove(src)  # duplicate, remove param version

    if renames:
        print(f'  Renamed {len(renames)} files (stripped query-param suffix)')

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
