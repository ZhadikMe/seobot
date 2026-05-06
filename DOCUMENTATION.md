# SEO Bot — Документация

**Версия:** v5 (май 2026)  
**Сервер:** `207.154.195.96` (DigitalOcean), systemd + nginx  
**Auto-deploy:** push в `master` → GitHub webhook → перезапуск сервиса

---

## Что такое SEO Bot

Web-инструмент для автоматической SEO-оптимизации статических сайтов. Скачивает сайт из Wayback Machine (или берёт ZIP / GitHub-репо), применяет SEO-исправления, переводит на до 21 языка и разворачивает на сервере — всё через браузерный интерфейс.

---

## Архитектура

```
admin.py        — Web UI (Flask): источник, настройки, живой лог, деплой
pull.py         — Скачивание сайта из web.archive.org (wget + recover)
audit.py        — SEO-аудит: сканирует HTML, выдаёт таблицу проблем по типам
fixes.py        — Шаги исправлений (in-place)
translate.py    — Перевод через WowAI API + Groq fallback, hreflang, lang switcher
deploy.py       — SSH-деплой на сервер, nginx конфиг, port-based preview
run_local.py    — Локальный запуск pipeline без UI
detector.py     — Определяет тип репо (для GitHub-потока)
```

---

## Web Admin Panel (admin.py)

Основной интерфейс. Запускается на `http://207.154.195.96/` (nginx проксирует 80 → 8080).

**Локальный запуск:**
```bash
python admin.py        # → http://localhost:8080
ADMIN_PORT=9000 python admin.py
```

**Что умеет:**
- Три источника сайта: ZIP-архив (drag & drop), ссылка на web.archive.org, GitHub-репозиторий
- Режимы: Полный / Только SEO / Только перевод
- Выбор языков: все 21 или кастомный набор через кнопки с флагами
- Output target: GitHub PR / Сервер / Оба
- Живой лог через Server-Sent Events
- После деплоя — кнопка "Открыть →" с прямой ссылкой на превью

**Переменные окружения:**
```
GROQ_API_KEY    — генерация descriptions, H1, H2
WOWAI_API_KEY   — перевод
GITHUB_TOKEN    — создание PR (можно вводить вручную)
SSH_DEPLOY_HOST — хост сервера (по умолч. 207.154.195.96)
SSH_DEPLOY_USER — пользователь (по умолч. root)
SSH_DEPLOY_KEY  — PEM-ключ для SSH
ADMIN_PORT      — порт (по умолч. 8080)
```

---

## Деплой сайтов: port-based система

Каждый сайт разворачивается на своём порту начиная с 8081. DNS не нужен — превью доступно сразу по IP.

**Текущие сайты:**

| URL | Домен |
|-----|-------|
| http://207.154.195.96:8081/ | autocarwallpappers.com |
| http://207.154.195.96:8082/ | dialacarlondon.com |
| http://207.154.195.96:8083/ | elizaroseandcompany.com |
| http://207.154.195.96:8084/ | kitcarsoncolorado.com |
| http://207.154.195.96:8085/ | loricarson.com |
| http://207.154.195.96:8086/ | maisonducarrelage.com |
| http://207.154.195.96:8087/ | sailingsuncharters.com |
| http://207.154.195.96:8088/ | thecarouser.com |
| http://207.154.195.96:8089/ | travellingfast.com |
| http://207.154.195.96:8090/ | wilsonclassiccar.com |
| http://207.154.195.96:8091/ | yourcarwala.com |

Новый сайт автоматически получает следующий свободный порт. При повторном деплое того же домена порт сохраняется.

---

## Шаги SEO-исправлений

| # | Шаг | Что делает |
|---|-----|------------|
| 1 | `fix_archive_scripts` | Удаляет Wayback Toolbar инъекции из HTML |
| 2 | `fix_canonical` | Добавляет `<link rel="canonical">` с реальным доменом |
| 3 | `fix_title_refresh` | Обновляет год в title/description |
| 4 | `fix_descriptions` | Генерирует уникальный meta description через Groq (120–155 симв.) |
| 5 | `fix_h1` | Добавляет H1 на страницы без заголовков (Groq) |
| 6 | `fix_h2` | Добавляет H2 в страницы без подзаголовков (Groq) |
| 7 | `fix_thin_content` | Для WordPress archive-страниц добавляет intro/outro абзацы |
| 8 | `fix_schema` | Добавляет BreadcrumbList Schema.org (JSON-LD) |
| 9 | `fix_og_image` | Добавляет og:image, og:title, og:url, og:type, og:site_name, og:locale |
| 10 | `fix_internal_links` | Добавляет внутренние ссылки между страницами |
| 11 | `fix_nofollow` | Удаляет внешние `<a href="https://...">` ссылки (анкор остаётся текстом) |
| 12 | `fix_robots_txt` | Генерирует/обновляет robots.txt с актуальным Sitemap URL |

### Шаги перевода

| # | Шаг | Что делает |
|---|-----|------------|
| 1 | `fix_translations` | Переводит на выбранные языки (с кэшем nav/footer) |
| 2 | `fix_hreflang_translated` | Добавляет hreflang на переведённые страницы |
| 3 | `fix_internal_links` | Обновляет внутренние ссылки в переведённых версиях |
| 4 | `fix_lang_switcher` | Добавляет/обновляет floating dropdown переключения языков |

---

## Аудит

После pipeline выводится таблица изменений по типам проблем:

```
┌─────────────────────┬────┬────┐
│ Проблема            │ До │ П/с│
├─────────────────────┼────┼────┤
│ Нет description     │  5 │  0 ✅│
│ Нет H1              │  3 │  0 ✅│
│ Тонкий контент      │  5 │  5 ⚠│
└─────────────────────┴────┴────┘
Страниц с проблемами: 5 → 2 / 5
```

Проверяемые параметры: `no_title`, `no_desc`, `no_h1`, `no_h2`, `no_canonical`, `no_og_image`, `no_schema`, `thin_content`, `few_links`.

---

## Скачивание из archive.org (pull.py)

1. **CDX API** — оценка числа страниц и времени скачивания
2. **wget** — `--wait=1 --random-wait` (0.5–1.5 сек между запросами)
3. **Rate limit** — при 429 / Connection refused: пауза 60–120 сек, до 3 попыток. Если не помогло — чёткое сообщение "подожди 20-30 минут"
4. **Recover** — повторное скачивание пропущенных ассетов после паузы
5. **PHP → HTML** — переименование и перезапись ссылок
6. **Query-string URLs** — `?p=123` → `_qs__p=123.html`
7. **Очистка артефактов** archive.org

> ⚠️ archive.org может заблокировать IP при прогоне нескольких сайтов подряд (~20-30 мин ожидания).

---

## Перевод

1. Язык источника определяется из `<html lang="">` и исключается из целевых
2. Кэш nav/footer — UI-строки переводятся один раз (~30% экономии API)
3. Батч-перевод через WowAI API (50 сегментов на запрос)
4. Для хинди (hi) — Groq fallback (WowAI не поддерживает)
5. Антифейк-фильтр: перевод отклоняется если ≥70% символов совпадает с оригиналом
6. RTL: арабский получает `dir="rtl"`

> ⚠️ WowAI API иногда перегружается — перевод всех 21 языков может идти медленно.

### Повторный запуск (incremental)

Уже переведённые страницы пропускаются. Новые языки переводятся с нуля. hreflang и lang-switcher обновляются с полным списком.

---

## Поддерживаемые языки (21)

| Код | Язык | Код | Язык | Код | Язык |
|-----|------|-----|------|-----|------|
| `ru` | 🇷🇺 Русский | `nl` | 🇳🇱 Нидерландский | `ko` | 🇰🇷 Корейский |
| `de` | 🇩🇪 Немецкий | `cs` | 🇨🇿 Чешский | `zh` | 🇨🇳 Китайский |
| `fr` | 🇫🇷 Французский | `ro` | 🇷🇴 Румынский | `ja` | 🇯🇵 Японский |
| `es` | 🇪🇸 Испанский | `sv` | 🇸🇪 Шведский | `sk` | 🇸🇰 Словацкий |
| `it` | 🇮🇹 Итальянский | `tr` | 🇹🇷 Турецкий | `fi` | 🇫🇮 Финский |
| `pt` | 🇵🇹 Португальский | `el` | 🇬🇷 Греческий | `ar` | 🇸🇦 Арабский |
| `pl` | 🇵🇱 Польский | `uk` | 🇺🇦 Украинский | `hi` | 🇮🇳 Хинди |

---

## Локальный запуск (run_local.py)

```bash
# Один сайт
python run_local.py --site d:/loricarson --domain loricarson.com

# Только SEO, без перевода
python run_local.py --site d:/mysite --domain mysite.com --mode seo_only

# Только перевод
python run_local.py --site d:/mysite --domain mysite.com --mode translate --langs ru,de,fr

# Все сайты из списка SITES
python run_local.py --all
```

---

## Инфраструктура сервера

```
/opt/seobot/              — код бота
/opt/seobot/venv/         — Python virtualenv
/opt/seobot/.env          — переменные окружения
/var/www/<domain>/        — задеплоенные сайты
/etc/nginx/sites-available/<domain>  — nginx конфиг на отдельном порту
```

**systemd:** `systemctl status seobot`  
**nginx:** reverse proxy 80 → 8080 (admin UI) + отдельный порт для каждого сайта  
**Auto-deploy:** `POST /webhook` (GitHub) → `git pull` + `systemctl restart seobot`
