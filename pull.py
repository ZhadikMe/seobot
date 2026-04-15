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
import shutil
import subprocess

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


WGET = _find_wget()


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
# wget download
# ---------------------------------------------------------------------------

def download_snapshot(archive_url: str, tmp_dir: str, domain_no_www: str, wget_host: str) -> None:
    """
    Run wget to mirror the snapshot into tmp_dir.
    Both www and non-www variants are allowed so assets resolve correctly.
    """
    domains = f'{domain_no_www},www.{domain_no_www},web.archive.org'

    cmd = [
        WGET,
        '--recursive',
        '--level=inf',
        '--page-requisites',
        '--no-convert-links',   # keep original archive.org URLs; fix_archive_scripts cleans them
        '--span-hosts',
        '--no-parent',
        f'--domains={domains}',
        '--timeout=15',
        '--tries=3',
        '--wait=1',
        '--random-wait',
        '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        '-e', 'robots=off',
        f'--directory-prefix={tmp_dir}',
        archive_url,
    ]

    print(f'Running wget for {wget_host} ...')
    print('  ' + ' '.join(cmd[:6]) + ' ...')
    result = subprocess.run(cmd)
    # wget exits non-zero on partial downloads — that's normal, don't raise
    if result.returncode not in (0, 8):
        print(f'[warn] wget exited with code {result.returncode} (usually fine)')


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

def _copy_tree(src_dir: str, dst_dir: str) -> int:
    """Recursively copy src_dir into dst_dir, merging contents. Returns file count."""
    os.makedirs(dst_dir, exist_ok=True)
    count = 0
    for item in os.listdir(src_dir):
        src = os.path.join(src_dir, item)
        dst = os.path.join(dst_dir, item)
        if os.path.isdir(src):
            count += _copy_tree(src, dst)
        else:
            shutil.copy2(src, dst)
            count += 1
    return count


def extract_site(site_root: str, target_dir: str) -> None:
    """
    Copy all site files into target_dir.
    wget splits assets into sibling dirs: ts/http%3A/domain/ for HTML,
    tscs_/http%3A/domain/ for CSS, tsjs_/ for JS, tsim_/ for images.
    We collect all of them.
    """
    os.makedirs(target_dir, exist_ok=True)

    # site_root is e.g. .../web/20180330095801/http%3A/elizaroseandcompany.com
    # sibling asset dirs are at the same depth with suffix: cs_, js_, im_, wd_, fw_, etc.
    archive_web = os.path.dirname(os.path.dirname(os.path.dirname(site_root)))  # .../web.archive.org/web
    ts_dir_name = os.path.basename(os.path.dirname(os.path.dirname(site_root)))  # e.g. 20180330095801
    # Strip numeric-only timestamp to get base
    ts_base = re.match(r'(\d+)', ts_dir_name).group(1)

    total = 0
    for entry in os.listdir(archive_web):
        # Match timestamp-based dirs: 20180330095801, 20180330095801cs_, etc.
        if not entry.startswith(ts_base):
            continue
        entry_path = os.path.join(archive_web, entry)
        if not os.path.isdir(entry_path):
            continue
        # Find the domain subdirectory within this asset dir
        # Structure: tsXX_/http%3A/domain/ or tsXX_/domain/
        domain_subdir = _find_domain_subdir(entry_path, os.path.basename(site_root))
        if domain_subdir and os.path.isdir(domain_subdir):
            n = _copy_tree(domain_subdir, target_dir)
            total += n
            suffix = entry[len(ts_base):] or '(html)'
            print(f'  Copied {n} files from {suffix} assets')

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
# Main entry point
# ---------------------------------------------------------------------------

def pull_snapshot(archive_url: str, target_dir: str) -> str:
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

    # 1. Download
    download_snapshot(archive_url, tmp_dir, domain_no_www, wget_host)

    # 2. Find downloaded root
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

    # 4. PHP → HTML (only if .php files exist)
    php_files = [
        f for root, _, files in os.walk(target_dir)
        for f in files if f.lower().endswith('.php')
    ]
    if php_files:
        print(f'\nDetected PHP site ({len(php_files)} .php files) — converting...')
        rename_php_to_html(target_dir)

    # 5. Clean archive.org scripts/toolbar/URLs from HTML
    print('\nCleaning archive.org artifacts...')
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from fixes import fix_archive_scripts
        fix_archive_scripts(target_dir)
        print('Archive cleanup done.')
    except ImportError:
        print('[warn] fixes.py not found — skipping archive cleanup')

    # 6. Rename files with wget @param suffix → strip params (e.g. main.css@v=1 → main.css)
    #    and fix references in HTML/CSS
    print('\nFixing wget @param filenames...')
    _fix_wget_param_filenames(target_dir, domain_no_www)

    # 7. Convert absolute domain URLs → relative paths in HTML/CSS
    print('Converting absolute URLs to relative...')
    _fix_absolute_to_relative(target_dir, domain_no_www)

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
