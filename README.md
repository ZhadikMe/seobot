# SEO Bot

Скачивает статические сайты с archive.org, применяет SEO-правки, переводит на 21 язык и разворачивает на сервере.

## Архитектура

| Файл | Роль |
|------|------|
| `admin.py` | Web-интерфейс (Flask) + SSE-логи, оркестрация pipeline |
| `pull.py` | Загрузка сайтов с archive.org через wget |
| `fixes.py` | SEO-pipeline: descriptions, H1/H2, canonical, schema, og, hreflang, lang switcher |
| `translate.py` | Перевод HTML-страниц на 21 язык (WowAI + Groq как fallback) |
| `audit.py` | SEO-аудит: сканирует HTML, формирует список проблем по типам |
| `deploy.py` | Деплой сайта на сервер по SSH, nginx-конфиг, port-based preview |
| `run_local.py` | Локальный запуск pipeline без Admin UI |
| `detector.py` | Определение языка и кодировки |

## Pipeline

1. **Pull** — `pull.py` скачивает сайт из Wayback Machine
2. **Fix** — `fixes.py`: `fix_archive_scripts → fix_canonical → fix_title_refresh → fix_descriptions → fix_h1 → fix_h2 → fix_thin_content → fix_schema → fix_og_image → fix_internal_links → fix_nofollow → fix_robots_txt`
3. **Translate** — `translate.py` создаёт поддиректории `/lang/` для выбранных языков
4. **Deploy** — `deploy.py` разворачивает сайт на сервере, nginx слушает на своём порту

## Переменные окружения

```
WOWAI_API_KEY=...
GROQ_API_KEY=...
GITHUB_TOKEN=...          # опционально, для PR
SSH_DEPLOY_HOST=207.154.195.96
SSH_DEPLOY_USER=root
SSH_DEPLOY_KEY=...        # PEM-ключ; если не задан — использует ~/.ssh/
```

## Запуск

```bash
python -m venv venv && venv/bin/pip install -r requirements.txt
python admin.py   # → http://localhost:8080
```

## Деплой (сервер)

Бот работает как systemd-сервис на `207.154.195.96`.  
Auto-deploy: push в `master` → GitHub webhook → `git pull` + `systemctl restart seobot`.

Каждый задеплоенный сайт доступен по `http://207.154.195.96:<port>/` (порты начиная с 8081).
