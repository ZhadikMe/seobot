# SEO Bot

Скачивает статические сайты с archive.org, применяет SEO-правки и переводит их на 21 язык через WowAI / Groq.

## Архитектура

| Файл | Роль |
|------|------|
| `bot.py` | Веб-интерфейс + SSE-логи, оркестрация пайплайна |
| `pull.py` | Загрузка сайтов с archive.org через wget (Railway, с rate-limit) |
| `pull_local.py` | То же, что pull.py, но с уменьшенными задержками для локального запуска |
| `translate.py` | Перевод HTML-страниц на 21 язык (WowAI + Groq как fallback) |
| `fixes.py` | SEO-пайплайн: descriptions, H1/H2, thin content, hreflang, lang switcher |
| `audit.py` | Аудит страниц на SEO-проблемы |
| `detector.py` | Определение языка и кодировки |
| `admin.py` | Административные эндпоинты |

## Пайплайн

1. **Pull** — `pull.py` скачивает сайт из Wayback Machine
2. **Fix** — `fixes.py` запускает: `fix_descriptions → fix_h1 → fix_h2 → fix_thin_content → fix_hreflang → fix_lang_switcher`
3. **Translate** — `translate.py` создаёт поддиректории `/lang/` для 21 языка

## Переменные окружения

```
WOWAI_API_KEY=...
GROQ_API_KEY=...
```

## Локальный запуск

```bash
pip install -r requirements.txt
python bot.py
```

Открыть `http://localhost:8883`.

## Деплой

Настроен для Railway через `Procfile` + `railway.toml` + `nixpacks.toml`.
