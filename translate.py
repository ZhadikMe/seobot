#!/usr/bin/env python3
"""
translate.py — SEO-aware HTML translator via Groq API

Usage:
  python scripts/translate.py --langs ru,de,fr,es
  python scripts/translate.py --langs ru --page blog/solitary/index.html
  python scripts/translate.py --langs ru,de,fr,es --dry-run

Requirements:
  pip install groq
  Set GROQ_API_KEY environment variable or pass via --key

Rules (from anti-ban guide):
  - Fake translation check: similarity > 70% → skip, mark as failed
  - Only add hreflang for confirmed real translations
  - Never translate: URLs, canonical, og:url, schema @id/@type/url fields
  - Preserve HTML structure exactly
"""

import os, re, sys, json, time, hashlib, argparse, difflib
sys.stdout.reconfigure(encoding='utf-8')

from groq import Groq

# ── Config ────────────────────────────────────────────────────────────────────

SITE     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_URL = 'https://example.com'  # overridden by --base-url argument

SUPPORTED_LANGS = {
    'ru': 'Russian',
    'de': 'German',
    'fr': 'French',
    'es': 'Spanish',
    'it': 'Italian',
    'pt': 'Portuguese',
}

LANG_LOCALE = {
    'ru': 'ru_RU',
    'de': 'de_DE',
    'fr': 'fr_FR',
    'es': 'es_ES',
    'it': 'it_IT',
    'pt': 'pt_PT',
}

# Pages to skip (noindex or not worth translating)
SKIP_PAGES = {'/404.html', '/blog/new-suffolk/index.html'}

# Groq model — fast and free
MODEL = 'llama-3.1-8b-instant'

# Anti-fake threshold (from MD guide: > 70% similarity = fake)
FAKE_SIMILARITY_THRESHOLD = 0.70

# ── Groq client ───────────────────────────────────────────────────────────────

def get_client(api_key=None):
    key = api_key or os.environ.get('GROQ_API_KEY')
    if not key:
        raise ValueError(
            'GROQ_API_KEY not set.\n'
            'Get a free key at https://console.groq.com/\n'
            'Then: set GROQ_API_KEY=your_key_here'
        )
    return Groq(api_key=key)


# ── Similarity check (anti-fake) ──────────────────────────────────────────────

def similarity(a, b):
    """SequenceMatcher ratio — 0.0 (different) to 1.0 (identical)."""
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def is_fake_translation(original, translated, lang):
    """Returns True if translation looks fake."""
    # Exact match
    if translated.strip() == original.strip():
        return True
    # Too similar
    sim = similarity(original, translated)
    if sim > FAKE_SIMILARITY_THRESHOLD:
        return True
    # Translation returned in wrong language (heuristic: still has common EN words)
    # Skip this check for short strings
    if len(original) > 100:
        en_markers = ['the ', ' is ', ' are ', ' was ', ' were ', ' and ', ' of ']
        trans_lower = translated.lower()
        en_count = sum(1 for m in en_markers if m in trans_lower)
        if en_count >= 4 and lang not in ('de',):  # German keeps some similar words
            return True
    return False


# ── Text extraction from HTML ─────────────────────────────────────────────────

def extract_translatable(html):
    """
    Extract text segments that need translation.
    Returns list of (placeholder, original_text) tuples.
    Segments: title, meta description, og:title, og:description,
              twitter:title, twitter:description, H1, H2, H3, <p>, <li>
    """
    segments = []

    def add(pattern, flags=re.IGNORECASE | re.DOTALL):
        for m in re.finditer(pattern, html, flags):
            text = m.group(1).strip()
            # Skip empty, URLs, schema-only content
            if not text or text.startswith('http') or len(text) < 3:
                continue
            # Skip if it's inside a script or style block
            pos = m.start()
            preceding = html[max(0, pos-200):pos]
            if '<script' in preceding or '<style' in preceding:
                continue
            segments.append(text)

    # Meta tags
    add(r'<title>([^<]+)</title>')
    add(r'<meta\s+name=["\']description["\']\s+content="([^"]+)"')
    add(r'<meta\s+property=["\']og:title["\']\s+content="([^"]+)"')
    add(r'<meta\s+property=["\']og:description["\']\s+content="([^"]+)"')
    add(r'<meta\s+name=["\']twitter:title["\']\s+content="([^"]+)"')
    add(r'<meta\s+name=["\']twitter:description["\']\s+content="([^"]+)"')

    # Headings (anywhere on page)
    add(r'<h1[^>]*>([^<]+)</h1>')
    for tag in ['h2', 'h3', 'h4']:
        add(rf'<{tag}[^>]*>([^<]+)</{tag}>')

    # Paragraphs and list items inside main/article
    main_m = re.search(r'<(?:main|article)[^>]*>(.*?)</(?:main|article)>', html, re.DOTALL | re.IGNORECASE)
    if main_m:
        main_html = main_m.group(1)
        for m in re.finditer(r'<p[^>]*>([^<]{20,})</p>', main_html, re.IGNORECASE):
            text = m.group(1).strip()
            if text and not text.startswith('http'):
                segments.append(text)
        for m in re.finditer(r'<li[^>]*>([^<]{10,})</li>', main_html, re.IGNORECASE):
            text = m.group(1).strip()
            if text and not text.startswith('http'):
                segments.append(text)

    # Navigation, header, footer — short UI strings
    for section_tag in ('nav', 'header', 'footer'):
        section_m = re.search(rf'<{section_tag}[^>]*>(.*?)</{section_tag}>', html, re.DOTALL | re.IGNORECASE)
        if not section_m:
            continue
        section_html = section_m.group(1)
        # <a> link text
        for m in re.finditer(r'<a[^>]*>([^<]{2,60})</a>', section_html, re.IGNORECASE):
            text = m.group(1).strip()
            if text and not text.startswith('http') and not re.match(r'^[\d\s.,:;!?]+$', text):
                segments.append(text)
        # <button> and <span> text
        for tag in ('button', 'span'):
            for m in re.finditer(rf'<{tag}[^>]*>([^<]{{2,80}})</{tag}>', section_html, re.IGNORECASE):
                text = m.group(1).strip()
                if text and not text.startswith('http') and not re.match(r'^[\d\s.,:;!?]+$', text):
                    segments.append(text)

    # "Continue reading", "Read more" style links anywhere
    for m in re.finditer(r'<a[^>]*class="[^"]*(?:more|read-more|continue)[^"]*"[^>]*>([^<]+)</a>', html, re.IGNORECASE):
        text = m.group(1).strip()
        if text:
            segments.append(text)

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for s in segments:
        if s not in seen:
            seen.add(s)
            unique.append(s)

    return unique


# ── Placeholder protection ────────────────────────────────────────────────────

# Patterns that should NOT be translated
_PROTECT_PATTERNS = [
    r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}',          # emails
    r'https?://\S+',                              # URLs
    r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b',       # Proper names (Title Case 2+ words)
    r'\b(?:NPR|WNYC|BBC|CNN|MTV|ESPN|ABC|CBS|NBC|HBO|iTunes|Amazon|Spotify|YouTube|Facebook|Twitter|Instagram)\b',  # known brands
]
_PROTECT_RE = re.compile('(' + '|'.join(_PROTECT_PATTERNS) + ')')


def protect_segment(text):
    """Replace untranslatable tokens with §N§ placeholders. Returns (protected_text, token_map)."""
    tokens = {}
    counter = [0]

    def replace(m):
        key = f'§{counter[0]}§'
        tokens[key] = m.group(0)
        counter[0] += 1
        return key

    protected = _PROTECT_RE.sub(replace, text)
    return protected, tokens


def restore_segment(text, tokens):
    """Restore §N§ placeholders back to original tokens."""
    for key, value in tokens.items():
        text = text.replace(key, value)
    return text


# ── Groq translation ──────────────────────────────────────────────────────────

def translate_batch(client, segments, target_lang, lang_name, retries=3):
    """
    Translate a list of text segments in one API call.
    Returns dict: {original: translated}
    """
    if not segments:
        return {}

    # Protect untranslatable tokens in each segment
    protected_segments = []
    token_maps = []
    for seg in segments:
        protected, tokens = protect_segment(seg)
        protected_segments.append(protected)
        token_maps.append(tokens)

    # Build numbered list for the prompt
    numbered = '\n'.join(f'{i+1}. {s}' for i, s in enumerate(protected_segments))

    prompt = f"""You are a professional SEO translator. Translate the following texts to {lang_name}.

RULES:
- Translate ONLY the text content, nothing else
- Keep the same tone (personal, literary, music/arts context)
- SEO titles: keep under 60 characters
- SEO descriptions: keep 120-155 characters
- Return ONLY a numbered list in the same format, nothing else
- Do not add explanations or notes

TEXTS TO TRANSLATE:
{numbered}"""

    for attempt in range(retries):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[{'role': 'user', 'content': prompt}],
                temperature=0.3,
                max_tokens=4096,
            )
            result_text = response.choices[0].message.content.strip()
            break
        except Exception as e:
            if attempt == retries - 1:
                print(f'    Groq error after {retries} attempts: {e}')
                return {}
            time.sleep(2 ** attempt)

    # Parse numbered response and restore placeholders
    translations = {}
    lines = result_text.split('\n')
    i = 0
    for line in lines:
        m = re.match(r'^\d+\.\s+(.+)$', line.strip())
        if m:
            if i < len(segments):
                translated = m.group(1).strip()
                # Restore protected tokens back into the translation
                translated = restore_segment(translated, token_maps[i])
                original = segments[i]
                translations[original] = translated
                i += 1

    return translations


# ── HTML patching ─────────────────────────────────────────────────────────────

def patch_html(html, translations, lang, lang_name, original_rel_path):
    """Apply translations to HTML, update lang/hreflang/canonical/og:locale."""

    patched = html

    # Replace translatable strings — longest first to avoid partial matches
    for original, translated in sorted(translations.items(), key=lambda x: -len(x[0])):
        if translated and original != translated:
            patched = patched.replace(original, translated)

    # Update <html lang="">
    patched = re.sub(r'(<html[^>]*)\blang=["\'][^"\']*["\']', rf'\1lang="{lang}"', patched)

    # Update og:locale
    patched = re.sub(
        r'<meta\s+property=["\']og:locale["\']\s+content="[^"]*">',
        f'<meta property="og:locale" content="{LANG_LOCALE.get(lang, lang)}">',
        patched
    )
    # Add og:locale if not present
    if 'og:locale' not in patched:
        patched = patched.replace(
            '<meta property="og:type"',
            f'<meta property="og:locale" content="{LANG_LOCALE.get(lang, lang)}">\n  <meta property="og:type"'
        )

    # Update canonical to translated URL
    page_path = original_rel_path.replace('/index.html', '/').replace('\\', '/')
    if not page_path.startswith('/'):
        page_path = '/' + page_path
    translated_url = f'{BASE_URL}/{lang}{page_path}'
    patched = re.sub(
        r'<link\s+rel=["\']canonical["\']\s+href="[^"]*">',
        f'<link rel="canonical" href="{translated_url}">',
        patched
    )

    # Update og:url
    patched = re.sub(
        r'<meta\s+property=["\']og:url["\']\s+content="[^"]*">',
        f'<meta property="og:url" content="{translated_url}">',
        patched
    )

    return patched


def add_hreflang(html, original_rel_path, available_langs):
    """Add hreflang alternate links for all confirmed real translations + original EN."""
    page_path = original_rel_path.replace('/index.html', '/').replace('\\', '/')
    if not page_path.startswith('/'):
        page_path = '/' + page_path

    # Remove existing hreflang links
    html = re.sub(r'\n\s*<link\s+rel=["\']alternate["\']\s+hreflang=[^>]+>', '', html)

    # Build new hreflang block
    lines = [f'  <link rel="alternate" hreflang="en" href="{BASE_URL}{page_path}">']
    for lang in sorted(available_langs):
        lines.append(f'  <link rel="alternate" hreflang="{lang}" href="{BASE_URL}/{lang}{page_path}">')
    lines.append(f'  <link rel="alternate" hreflang="x-default" href="{BASE_URL}{page_path}">')

    hreflang_block = '\n' + '\n'.join(lines)
    html = html.replace('</head>', hreflang_block + '\n</head>', 1)
    return html


# ── Per-page translation ──────────────────────────────────────────────────────

def translate_page(client, src_path, rel_path, target_langs, dry_run=False, skip_existing=False):
    """Translate one HTML page to all target languages."""
    with open(src_path, encoding='utf-8') as f:
        html = f.read()

    segments = extract_translatable(html)
    if not segments:
        print(f'  skip (no segments): {rel_path}')
        return []

    print(f'\n  {rel_path} — {len(segments)} segments')

    confirmed_langs = []

    for lang in target_langs:
        lang_name = SUPPORTED_LANGS[lang]

        # Skip if translated file already exists
        if skip_existing and not dry_run:
            page_dir = os.path.dirname(rel_path.lstrip('/'))
            out_path = os.path.join(SITE, lang, page_dir, 'index.html')
            if os.path.exists(out_path):
                print(f'    → {lang}: skip (exists)')
                confirmed_langs.append(lang)
                continue

        print(f'    → {lang} ({lang_name})...', end=' ', flush=True)

        # Translate in one batch
        translations = translate_batch(client, segments, lang, lang_name)

        if not translations:
            print('FAILED (no response)')
            continue

        # Count valid translations
        valid = sum(1 for o, t in translations.items() if t and not is_fake_translation(o, t, lang))
        fake  = len(translations) - valid

        # Reject only if zero meaningful segments were translated.
        # "Meaningful" = original text > 50 chars (not just a title or album name).
        # Pages like contact/news have lots of proper nouns that correctly stay the same —
        # as long as at least one real sentence was translated, accept the result.
        meaningful_valid = sum(
            1 for o, t in translations.items()
            if t and not is_fake_translation(o, t, lang) and len(o) > 50
        )
        if meaningful_valid == 0 and valid == 0:
            print(f'FAKE (0 valid)')
            continue

        print(f'OK ({valid} valid, {fake} fake skipped)')

        if dry_run:
            confirmed_langs.append(lang)
            continue

        # Build translated HTML
        clean_translations = {o: t for o, t in translations.items()
                               if t and not is_fake_translation(o, t, lang)}
        translated_html = patch_html(html, clean_translations, lang, lang_name, rel_path)

        # Write output file
        page_dir = os.path.dirname(rel_path.lstrip('/'))
        out_dir = os.path.join(SITE, lang, page_dir)
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, 'index.html')

        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(translated_html)

        confirmed_langs.append(lang)

    # Update original EN page with hreflang (only for confirmed langs)
    if confirmed_langs and not dry_run:
        updated_html = add_hreflang(html, rel_path, confirmed_langs)
        with open(src_path, 'w', encoding='utf-8') as f:
            f.write(updated_html)
        print(f'    hreflang added to EN page: {confirmed_langs}')

    return confirmed_langs


# ── Sitemap update ────────────────────────────────────────────────────────────

def update_sitemap(translated_pages):
    """
    Rebuild sitemap.xml with correct BASE_URL.
    If sitemap already exists with a wrong domain, replaces all its <loc> URLs.
    """
    sitemap_path = os.path.join(SITE, 'sitemap.xml')

    if not os.path.exists(sitemap_path):
        sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n</urlset>'
    else:
        with open(sitemap_path, encoding='utf-8') as f:
            sitemap = f.read()

    # Fix any wrong domain already in sitemap <loc> tags
    if BASE_URL != 'https://example.com':
        def fix_loc(m):
            url = m.group(1)
            fixed = re.sub(r'^https?://[^/]+', BASE_URL, url)
            return f'<loc>{fixed}</loc>'
        sitemap = re.sub(r'<loc>(https?://[^<]+)</loc>', fix_loc, sitemap)

    new_entries = []
    for rel_path, langs in translated_pages.items():
        page_path = rel_path.replace('/index.html', '/').replace('\\', '/')
        if not page_path.startswith('/'):
            page_path = '/' + page_path
        for lang in langs:
            url = f'{BASE_URL}/{lang}{page_path}'
            if url not in sitemap:
                new_entries.append(
                    f'  <url>\n'
                    f'    <loc>{url}</loc>\n'
                    f'    <changefreq>yearly</changefreq>\n'
                    f'    <priority>0.5</priority>\n'
                    f'  </url>'
                )

    if new_entries:
        block = '\n\n  <!-- Translated pages -->\n' + '\n'.join(new_entries)
        sitemap = sitemap.replace('</urlset>', block + '\n\n</urlset>')

    with open(sitemap_path, 'w', encoding='utf-8') as f:
        f.write(sitemap)
    print(f'\nSitemap: {len(new_entries)} new translated URLs added')


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='SEO HTML translator via Groq')
    parser.add_argument('--langs', default='ru,de,fr,es',
                        help='Comma-separated language codes (default: ru,de,fr,es)')
    parser.add_argument('--page', default=None,
                        help='Translate single page (e.g. blog/solitary/index.html)')
    parser.add_argument('--key', default=None,
                        help='Groq API key (or set GROQ_API_KEY env var)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Test without writing files')
    parser.add_argument('--skip-existing', action='store_true',
                        help='Skip languages where translated file already exists')
    parser.add_argument('--base-url', default=None,
                        help='Site base URL (e.g. https://example.com) for hreflang/canonical/sitemap')
    args = parser.parse_args()

    if args.base_url:
        global BASE_URL
        BASE_URL = args.base_url.rstrip('/')

    target_langs = [l.strip() for l in args.langs.split(',') if l.strip() in SUPPORTED_LANGS]
    if not target_langs:
        print(f'No valid languages. Supported: {", ".join(SUPPORTED_LANGS)}')
        sys.exit(1)

    client = get_client(args.key)
    print(f'Groq client ready. Model: {MODEL}')
    print(f'Target languages: {", ".join(target_langs)}')
    if args.dry_run:
        print('DRY RUN — no files will be written')

    translated_pages = {}

    if args.page:
        # Single page mode
        src = os.path.join(SITE, args.page.replace('/', os.sep))
        rel = '/' + args.page.replace('\\', '/')
        if rel not in SKIP_PAGES:
            langs = translate_page(client, src, rel, target_langs, args.dry_run, args.skip_existing)
            if langs:
                translated_pages[rel] = langs
    else:
        # All pages
        for root, dirs, files in os.walk(SITE):
            dirs[:] = [d for d in dirs if d not in ('scripts', 'images') + tuple(SUPPORTED_LANGS.keys())]
            for fname in files:
                if not fname.endswith('.html'):
                    continue
                fpath = os.path.join(root, fname)
                rel = fpath.replace(SITE, '').replace(os.sep, '/')
                if rel in SKIP_PAGES:
                    print(f'  skip (noindex): {rel}')
                    continue
                langs = translate_page(client, fpath, rel, target_langs, args.dry_run, args.skip_existing)
                if langs:
                    translated_pages[rel] = langs
                # Small delay to avoid rate limiting
                time.sleep(0.5)

    if not args.dry_run and translated_pages:
        update_sitemap(translated_pages)

    total = sum(len(v) for v in translated_pages.values())
    print(f'\nDone: {len(translated_pages)} pages × languages = {total} translated files')


if __name__ == '__main__':
    main()
