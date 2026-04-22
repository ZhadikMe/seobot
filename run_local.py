#!/usr/bin/env python3
"""
run_local.py — запускает полный SEO pipeline локально на одном или нескольких сайтах.

Использование:
  python run_local.py --site d:/loricarson --domain loricarson.com
  python run_local.py --site d:/thecarouser/site --domain thecarouser.com
  python run_local.py --site d:/autocarwallpapers/web.archive.org/web/20140606133252 --domain autocarwallpapers.com
  python run_local.py --all

Режимы:
  --mode full        SEO + перевод на все языки (по умолчанию)
  --mode seo_only    только SEO-исправления, без перевода
  --mode translate   только перевод (если SEO уже сделан)

Переведённые файлы создаются прямо в site_dir (не нужен GitHub).
"""

import os, sys, time, argparse, logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s:%(name)s:%(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

WOWAI_KEY   = os.environ.get('WOWAI_API_KEY') or os.environ.get('WOWAI_KEY') or 'sk_trans_o5Un1stZ7eEG5uXovdDK_XlwzGHnqHd5lPJl9RxmA5U'
GROQ_KEY    = os.environ.get('GROQ_API_KEY') or os.environ.get('GROQ_KEY', '')

ALL_LANGS = [
    'ru', 'de', 'fr', 'es', 'it', 'pt', 'pl', 'nl', 'cs', 'ro', 'sv', 'tr',
    'el', 'uk', 'ko', 'zh', 'ja', 'sk', 'fi', 'ar', 'hi',
]

# Известные сайты для --all
SITES = [
    {'path': 'd:/loricarson',            'domain': 'loricarson.com'},
    {'path': 'd:/autocarwallpapers/site','domain': 'autocarwallpapers.com'},
    {'path': 'd:/thecarouser/site',      'domain': 'thecarouser.com'},
]

SEO_STEPS = [
    ('Очищаю archive.org скрипты...',              'fix_archive_scripts'),
    ('Добавляю canonical URLs...',                  'fix_canonical'),
    ('Обновляю год в заголовках...',                'fix_title_refresh'),
    ('Генерирую уникальные descriptions...',         'fix_descriptions'),
    ('Добавляю Schema.org (BreadcrumbList)...',      'fix_schema'),
    ('Добавляю OG images...',                        'fix_og_image'),
    ('Добавляю nofollow на внешние ссылки...',       'fix_nofollow'),
    ('Генерирую robots.txt...',                      'fix_robots_txt'),
]
TRANSLATE_STEPS = [
    ('Запускаю переводы...',                         'fix_translations'),
    ('Добавляю hreflang на переведённые страницы...','fix_hreflang_translated'),
    ('Добавляю внутренние ссылки...',                'fix_internal_links'),
    ('Обновляю lang switcher...',                    'fix_lang_switcher'),
]


def run_pipeline(site_dir: str, domain: str, mode: str = 'full', langs: list = None):
    from fixes import run_all_fixes
    from translate import detect_source_lang
    from audit import run_audit_on_dir

    if langs is None:
        langs = ALL_LANGS

    if not os.path.isdir(site_dir):
        log.error(f'Директория не найдена: {site_dir}')
        return

    log.info(f'=== Начинаю: {site_dir} (domain={domain}, mode={mode}) ===')

    source_lang = detect_source_lang(site_dir)
    log.info(f'Исходный язык сайта: {source_lang}')

    audit_before = run_audit_on_dir(site_dir)
    log.info(f'Проблем до исправлений: {audit_before["failed"]} / {audit_before["total"]} страниц')

    translate_only = (mode == 'translate')
    seo_only = (mode == 'seo_only')

    if translate_only:
        steps = [
            ('Запускаю переводы...',                          'fix_translations'),
            ('Добавляю hreflang на переведённые страницы...', 'fix_hreflang_translated'),
            ('Обновляю lang switcher...',                     'fix_lang_switcher'),
        ]
    elif seo_only:
        steps = SEO_STEPS
    else:
        steps = SEO_STEPS + TRANSLATE_STEPS

    for label, step_key in steps:
        log.info(f'  [{step_key}] {label}')
        t0 = time.time()

        def progress_cb(done, total):
            log.info(f'    Перевод: {done}/{total} страниц...')

        result = run_all_fixes(
            site_dir, step_key, langs, GROQ_KEY,
            domain, WOWAI_KEY,
            progress_cb if step_key == 'fix_translations' else None,
            source_lang, translate_only,
        )
        elapsed = time.time() - t0

        if result and result.get('error'):
            log.warning(f'    ОШИБКА: {result["error"]}')
        else:
            log.info(f'    Готово за {elapsed:.1f}s')

    audit_after = run_audit_on_dir(site_dir)
    log.info(f'Проблем после исправлений: {audit_after["failed"]} / {audit_after["total"]} страниц')
    log.info(f'=== Завершено: {site_dir} ===\n')

    return {'before': audit_before['failed'], 'after': audit_after['failed']}


def main():
    parser = argparse.ArgumentParser(description='Локальный SEO pipeline')
    parser.add_argument('--site',   help='Путь к директории сайта')
    parser.add_argument('--domain', help='Домен сайта (например loricarson.com)')
    parser.add_argument('--mode',   default='full', choices=['full', 'seo_only', 'translate'],
                        help='Режим: full / seo_only / translate')
    parser.add_argument('--langs',  help='Языки через запятую (по умолч. все 21)')
    parser.add_argument('--all',    action='store_true', help='Запустить на всех трёх сайтах')
    args = parser.parse_args()

    langs = [l.strip() for l in args.langs.split(',')] if args.langs else ALL_LANGS

    if args.all:
        results = {}
        for site in SITES:
            r = run_pipeline(site['path'], site['domain'], args.mode, langs)
            results[site['domain']] = r
        print('\n=== ИТОГ ===')
        for domain, r in results.items():
            if r:
                print(f'  {domain}: {r["before"]} → {r["after"]} страниц с проблемами')
    elif args.site:
        domain = args.domain or os.path.basename(args.site.rstrip('/\\')) + '.com'
        run_pipeline(args.site, domain, args.mode, langs)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
