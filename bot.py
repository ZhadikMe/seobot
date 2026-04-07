#!/usr/bin/env python3
"""
SEO Bot — анализирует GitHub репозиторий со статическим сайтом,
исправляет SEO-проблемы и открывает Pull Request с изменениями.
"""
import asyncio
import logging
import os
import sys
import tempfile
import shutil
import re
from pathlib import Path
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.types import (Message, InlineKeyboardMarkup, InlineKeyboardButton,
                            CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

load_dotenv()

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
log = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
GROQ_API_KEY   = os.getenv('GROQ_API_KEY')
GITHUB_TOKEN   = os.getenv('GITHUB_TOKEN')

bot = Bot(token=TELEGRAM_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())

# Global processing lock — only one site at a time
_processing_user_id: int | None = None


# ── FSM States ────────────────────────────────────────────────────────────────

class SEOFlow(StatesGroup):
    waiting_repo         = State()
    waiting_mode         = State()
    waiting_domain       = State()
    waiting_github_token = State()
    waiting_langs        = State()
    waiting_confirm      = State()
    processing           = State()


# ── Keyboards ─────────────────────────────────────────────────────────────────

def langs_keyboard(selected: set) -> InlineKeyboardMarkup:
    options = ['ru', 'de', 'fr', 'es', 'it', 'pt']
    labels  = {'ru': '🇷🇺 RU', 'de': '🇩🇪 DE', 'fr': '🇫🇷 FR',
               'es': '🇪🇸 ES', 'it': '🇮🇹 IT', 'pt': '🇵🇹 PT'}
    buttons = []
    for lang in options:
        label = ('✅ ' if lang in selected else '') + labels[lang]
        buttons.append(InlineKeyboardButton(text=label, callback_data=f'lang:{lang}'))

    rows = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton(text='▶️ Запустить анализ', callback_data='lang:start')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='🔍 Только аудит', callback_data='mode:audit'),
        InlineKeyboardButton(text='🔧 Аудит + исправления + PR', callback_data='mode:full'),
    ]])


def confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='✅ Да, исправить и создать PR', callback_data='confirm:yes'),
        InlineKeyboardButton(text='❌ Нет', callback_data='confirm:no'),
    ]])


# ── Handlers ──────────────────────────────────────────────────────────────────

INFO_TEXT = (
    '*SEO-бот — что умеет и как работает*\n\n'
    '*Что делает бот:*\n'
    '1. Скачивает сайт с GitHub\n'
    '2. Анализирует SEO: title, description, H1, canonical, og:image, schema\n'
    '3. В режиме «полный»: исправляет мета-теги, добавляет Schema.org, '
    'переводит на выбранные языки, создаёт Pull Request\n\n'
    '*Режимы:*\n'
    '🔍 *Только аудит* — смотришь список проблем, ничего не меняется\n'
    '🔧 *Аудит + PR* — бот исправляет и создаёт PR в твой репо\n\n'
    '*Поддерживаемые структуры репо:*\n'
    '• `web.archive.org/` дамп — бот сам распакует CSS, картинки, JS\n'
    '• Папка `site/` с HTML файлами\n'
    '• HTML файлы в корне репо\n\n'
    '*Переводы:*\n'
    'Каждый язык добавляется инкрементально — повторный запуск с другим языком '
    'не удалит предыдущие переводы.\n\n'
    '*Для PR нужен GitHub токен:*\n'
    '1. Открой [github.com/settings/tokens](https://github.com/settings/tokens)\n'
    '2. Нажми *Generate new token* → *Generate new token (classic)*\n'
    '3. Дай любое название, например `seobot`\n'
    '4. Поставь галочку на `repo` (первый пункт в списке)\n'
    '5. Нажми *Generate token* и скопируй — он начинается с `ghp_`\n\n'
    '*Команды:*\n'
    '/start — начать\n'
    '/info — эта справка\n'
    '_← Назад — вернуться к предыдущему шагу_'
)


@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        '👋 Привет! Я SEO-бот.\n\n'
        'Анализирую сайты на GitHub, нахожу SEO-проблемы '
        'и создаю Pull Request с исправлениями.\n\n'
        '📎 Отправь ссылку на GitHub репозиторий:\n'
        '`https://github.com/user/repo`',
        parse_mode='Markdown',
        reply_markup=main_keyboard()
    )
    await state.set_state(SEOFlow.waiting_repo)


@dp.message(Command('info'))
@dp.message(F.text == 'ℹ️ Info')
async def cmd_info(message: Message):
    await message.answer(INFO_TEXT, parse_mode='Markdown')


@dp.message(Command('cancel'))
@dp.message(F.text == '← Назад')
async def cmd_cancel(message: Message, state: FSMContext):
    global _processing_user_id
    current_state = await state.get_state()
    data = await state.get_data()

    # If processing — fully cancel and clean up
    if current_state == SEOFlow.processing:
        tmp = data.get('tmp_dir')
        if tmp and os.path.exists(tmp):
            shutil.rmtree(tmp, ignore_errors=True)
        if _processing_user_id == message.from_user.id:
            _processing_user_id = None
        await state.clear()
        await message.answer('❌ Обработка отменена.', reply_markup=main_keyboard())
        return

    # Back navigation based on current state
    if current_state == SEOFlow.waiting_mode:
        # Back to: enter repo URL
        await state.clear()
        await message.answer(
            '📎 Отправь ссылку на GitHub репозиторий:\n`https://github.com/user/repo`',
            parse_mode='Markdown', reply_markup=main_keyboard()
        )
        await state.set_state(SEOFlow.waiting_repo)

    elif current_state == SEOFlow.waiting_domain:
        # Back to: mode selection
        await message.answer('Что хочешь сделать?', reply_markup=mode_keyboard())
        await state.set_state(SEOFlow.waiting_mode)

    elif current_state == SEOFlow.waiting_github_token:
        # Back to: domain input
        await message.answer(
            '🌐 *Укажи домен сайта* (или Пропустить):',
            parse_mode='Markdown', reply_markup=domain_keyboard()
        )
        await state.set_state(SEOFlow.waiting_domain)

    elif current_state == SEOFlow.waiting_langs:
        # Back to: token input (full) or domain (audit)
        mode = data.get('mode', 'full')
        if mode == 'full':
            await message.answer(
                '🔑 Введи GitHub токен (начинается с `ghp_`):',
                parse_mode='Markdown', reply_markup=token_keyboard()
            )
            await state.set_state(SEOFlow.waiting_github_token)
        else:
            await message.answer(
                '🌐 *Укажи домен сайта* (или Пропустить):',
                parse_mode='Markdown', reply_markup=domain_keyboard()
            )
            await state.set_state(SEOFlow.waiting_domain)

    elif current_state == SEOFlow.waiting_confirm:
        # Back to: language selection
        await message.answer(
            '🌍 Выбери языки для перевода:',
            reply_markup=langs_keyboard(data.get('selected_langs', {'ru', 'de', 'fr', 'es'}))
        )
        await state.set_state(SEOFlow.waiting_langs)

    else:
        # No active flow — clean up and restart
        tmp = data.get('tmp_dir')
        if tmp and os.path.exists(tmp):
            shutil.rmtree(tmp, ignore_errors=True)
        if _processing_user_id == message.from_user.id:
            _processing_user_id = None
        await state.clear()
        await message.answer(
            '📎 Отправь ссылку на GitHub репозиторий:\n`https://github.com/user/repo`',
            parse_mode='Markdown', reply_markup=main_keyboard()
        )


def token_keyboard() -> InlineKeyboardMarkup:
    # No skip button — token is required for full mode
    return InlineKeyboardMarkup(inline_keyboard=[])


def domain_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='⏭️ Пропустить', callback_data='domain:skip'),
    ]])


def main_keyboard() -> ReplyKeyboardMarkup:
    """Persistent bottom keyboard shown after /start."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text='ℹ️ Info'), KeyboardButton(text='← Назад')],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


@dp.message(F.text.regexp(r'https?://github\.com/'))
async def got_repo(message: Message, state: FSMContext):
    """Accept GitHub URL in any state — auto-reset if needed."""
    global _processing_user_id

    # Check if another user is currently processing
    if _processing_user_id is not None and _processing_user_id != message.from_user.id:
        await message.answer(
            '⏳ *Бот сейчас занят* — обрабатывается другой сайт.\n\n'
            'Попробуй через несколько минут.',
            parse_mode='Markdown'
        )
        return

    url = message.text.strip().rstrip('/')
    log.info(f'got_repo: url={url!r}')
    m = re.match(r'https?://github\.com/([^/]+/[^/]+?)(?:\.git)?$', url)
    if not m:
        await message.answer('⚠️ Не похоже на GitHub ссылку. Пример:\n`https://github.com/user/repo`',
                             parse_mode='Markdown')
        return

    # Clear previous session state before starting fresh
    old_data = await state.get_data()
    if old_data.get('tmp_dir'):
        shutil.rmtree(old_data['tmp_dir'], ignore_errors=True)
    await state.clear()

    repo_slug = m.group(1)
    await state.update_data(repo_url=url, repo_slug=repo_slug, selected_langs={'ru', 'de', 'fr', 'es'},
                            user_github_token=GITHUB_TOKEN)
    await message.answer(
        f'✅ Репозиторий: `{repo_slug}`\n\n'
        'Что хочешь сделать?',
        parse_mode='Markdown',
        reply_markup=mode_keyboard()
    )
    await state.set_state(SEOFlow.waiting_mode)


@dp.callback_query(SEOFlow.waiting_mode, F.data.startswith('mode:'))
async def chose_mode(callback: CallbackQuery, state: FSMContext):
    mode = callback.data.split(':')[1]  # 'audit' or 'full'
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.update_data(mode=mode)

    await callback.message.answer(
        '🌐 *Укажи домен сайта* — он нужен для canonical, hreflang, sitemap и og:image.\n\n'
        'Формат: `https://example.com` (без слеша в конце)\n\n'
        '_Если домена ещё нет — нажми Пропустить, подставится заглушка._',
        parse_mode='Markdown',
        reply_markup=domain_keyboard()
    )
    await state.set_state(SEOFlow.waiting_domain)
    await callback.answer()


async def _after_domain(message_or_callback, state: FSMContext):
    """Continue flow after domain step."""
    data = await state.get_data()
    mode = data.get('mode', 'audit')

    if mode == 'audit':
        await message_or_callback.answer(
            '🔍 Режим: только аудит.\n\n'
            '🌍 Выбери языки (для отчёта):',
            reply_markup=langs_keyboard(data.get('selected_langs', {'ru', 'de', 'fr', 'es'}))
        )
        await state.set_state(SEOFlow.waiting_langs)
    else:
        await message_or_callback.answer(
            '🔧 Режим: аудит + исправления + PR.\n\n'
            '🔑 *Нужен GitHub токен* — для скачивания репо и создания PR.\n\n'
            'Как получить:\n'
            '1. Открой [github.com/settings/tokens](https://github.com/settings/tokens)\n'
            '2. Нажми *Generate new token* → *Generate new token (classic)*\n'
            '3. Дай любое название, например `seobot`\n'
            '4. Поставь галочку на `repo` (первый пункт в списке)\n'
            '5. Нажми *Generate token* → скопируй токен (начинается с `ghp_`)\n\n'
            '_Сообщение с токеном будет удалено сразу после получения._',
            parse_mode='Markdown',
            reply_markup=token_keyboard()
        )
        await state.set_state(SEOFlow.waiting_github_token)


@dp.message(SEOFlow.waiting_domain)
async def got_domain(message: Message, state: FSMContext):
    domain = message.text.strip().rstrip('/')
    if not domain.startswith('http'):
        domain = 'https://' + domain
    await state.update_data(site_domain=domain)
    await _after_domain(message, state)


@dp.callback_query(SEOFlow.waiting_domain, F.data == 'domain:skip')
async def skip_domain(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_reply_markup(reply_markup=None)
    await _after_domain(callback.message, state)
    await callback.answer()


@dp.message(SEOFlow.waiting_github_token)
async def got_github_token(message: Message, state: FSMContext):
    token = message.text.strip() if message.text else ''

    if not token.startswith('ghp_') and not token.startswith('github_pat_'):
        await message.answer(
            '❌ Не похоже на GitHub токен (должен начинаться с `ghp_` или `github_pat_`).\n'
            'Попробуй ещё раз или нажми «Пропустить».',
            parse_mode='Markdown',
            reply_markup=token_keyboard()
        )
        return

    try:
        await message.delete()
    except Exception:
        await message.answer('⚠️ Не могу удалить сообщение — удали его вручную.')

    await state.update_data(user_github_token=token)
    data = await state.get_data()
    await message.answer(
        '✅ Токен принят.\n\n🌍 Выбери языки для перевода:',
        reply_markup=langs_keyboard(data.get('selected_langs', {'ru', 'de', 'fr', 'es'}))
    )
    await state.set_state(SEOFlow.waiting_langs)


@dp.callback_query(SEOFlow.waiting_github_token, F.data == 'token:skip')
async def skip_github_token(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_reply_markup(reply_markup=None)
    data = await state.get_data()
    await callback.message.answer(
        '⏭️ Без токена — только аудит, PR не создастся.\n\n'
        '🌍 Выбери языки:',
        reply_markup=langs_keyboard(data.get('selected_langs', {'ru', 'de', 'fr', 'es'}))
    )
    await state.set_state(SEOFlow.waiting_langs)
    await callback.answer()


@dp.callback_query(SEOFlow.waiting_langs, F.data.startswith('lang:'))
async def toggle_lang(callback: CallbackQuery, state: FSMContext):
    lang = callback.data.split(':')[1]
    data = await state.get_data()
    selected = data.get('selected_langs', set())

    if lang == 'start':
        if not selected:
            await callback.answer('Выбери хотя бы один язык!', show_alert=True)
            return
        await callback.message.edit_reply_markup(reply_markup=None)
        await run_audit(callback.message, state)
        return

    if lang in selected:
        selected.discard(lang)
    else:
        selected.add(lang)

    await state.update_data(selected_langs=selected)
    await callback.message.edit_reply_markup(reply_markup=langs_keyboard(selected))
    await callback.answer()


async def _fetch_zip(session, repo_slug: str, headers: dict) -> bytes:
    """
    Try multiple download strategies:
    1. Direct archive URL (no API quota, works for public repos without token)
    2. GitHub API zipball (requires token on shared IPs due to rate limits)
    """
    import aiohttp

    # Strategy 1: direct archive download — no rate limit, no auth needed for public repos
    for branch in ('main', 'master'):
        url = f'https://github.com/{repo_slug}/archive/refs/heads/{branch}.zip'
        log.info(f'Trying direct download: {url}')
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                return await resp.read()
            log.info(f'Direct {branch}: HTTP {resp.status}')

    # Strategy 2: API endpoint (needs token on Railway due to shared IP rate limits)
    api_url = f'https://api.github.com/repos/{repo_slug}/zipball'
    log.info(f'Trying API download: {api_url}')
    api_headers = {**headers, 'Accept': 'application/vnd.github+json'}
    async with session.get(api_url, headers=api_headers) as resp:
        if resp.status == 200:
            return await resp.read()
        if resp.status == 403:
            raise RuntimeError(
                'GitHub вернул 403 — превышен rate limit для публичных запросов.\n'
                'Отправь GitHub токен (ghp_...) для авторизации.'
            )
        raise RuntimeError(f'GitHub вернул {resp.status}')


async def download_repo_zip(repo_slug: str, dest_dir: str, token: str | None = None):
    """Download repo as ZIP from GitHub (async, long timeout) and extract."""
    import zipfile, io, aiohttp

    timeout = aiohttp.ClientTimeout(total=300, connect=30)
    headers = {}
    if token:
        headers['Authorization'] = f'Bearer {token}'

    async with aiohttp.ClientSession(timeout=timeout) as session:
        data = await _fetch_zip(session, repo_slug, headers)

    log.info(f'Downloaded {len(data)//1024} KB')

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        members = zf.namelist()
        top = members[0].split('/')[0] + '/'
        for member in members:
            target = os.path.join(dest_dir, member[len(top):])
            if member.endswith('/'):
                os.makedirs(target, exist_ok=True)
            else:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(member) as src, open(target, 'wb') as dst:
                    dst.write(src.read())

    log.info(f'Extracted to {dest_dir}')


async def run_audit(message: Message, state: FSMContext):
    """Clone repo, detect structure, normalize, run SEO audit."""
    global _processing_user_id
    _processing_user_id = message.from_user.id

    data = await state.get_data()
    repo_url   = data['repo_url']
    repo_slug  = data['repo_slug']
    langs      = sorted(data['selected_langs'])
    token      = data.get('user_github_token')

    status_msg = await message.answer('⏳ Скачиваю репозиторий...')

    # Download ZIP via GitHub API (avoids Windows colon-in-path issue with git clone)
    tmp_dir = tempfile.mkdtemp(prefix='seobot_')
    await state.update_data(tmp_dir=tmp_dir)

    try:
        await download_repo_zip(repo_slug, tmp_dir, token=token)
    except Exception as e:
        await bot.edit_message_text(
            text=f'❌ Не удалось скачать репозиторий:\n`{e}`',
            chat_id=message.chat.id, message_id=status_msg.message_id,
            parse_mode='Markdown')
        shutil.rmtree(tmp_dir, ignore_errors=True)
        _processing_user_id = None
        await state.clear()
        return

    # If repo has a web.archive.org dump, always re-extract site/ from archive
    # so we audit the original site state, not a potentially stale previous run.
    archive_in_repo = os.path.join(tmp_dir, 'web.archive.org')
    if os.path.isdir(archive_in_repo):
        stale_site = os.path.join(tmp_dir, 'site')
        if os.path.isdir(stale_site):
            shutil.rmtree(stale_site)
            log.info('Removed stale site/ — will re-extract from archive')

    # Detect and normalize structure
    await bot.edit_message_text(
        text='🔎 Определяю структуру репозитория...',
        chat_id=message.chat.id, message_id=status_msg.message_id)
    try:
        from detector import detect_and_normalize
        site_dir, structure_desc = await asyncio.get_event_loop().run_in_executor(
            None, detect_and_normalize, tmp_dir
        )
        await state.update_data(site_dir=site_dir)
    except Exception as e:
        await bot.edit_message_text(text=f'❌ {e}', chat_id=message.chat.id, message_id=status_msg.message_id)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        _processing_user_id = None
        await state.clear()
        return

    await bot.edit_message_text(
        text=f'✅ Структура: {structure_desc}\n\n🔍 Запускаю SEO аудит...',
        chat_id=message.chat.id, message_id=status_msg.message_id
    )

    # Run audit
    from audit import run_audit_on_dir
    results = run_audit_on_dir(site_dir)

    await state.update_data(audit_results=results, tmp_dir=tmp_dir)

    mode = data.get('mode', 'full')
    report = format_audit_report(results, repo_slug, langs)
    await bot.edit_message_text(
        text=report, chat_id=message.chat.id, message_id=status_msg.message_id,
        parse_mode='Markdown')

    if mode == 'audit':
        # Audit-only mode — clean up and finish
        shutil.rmtree(tmp_dir, ignore_errors=True)
        _processing_user_id = None
        await state.clear()
        await message.answer('✅ Аудит завершён. Отправь новую ссылку для следующего сайта.')
    else:
        await message.answer(
            '📋 _Аудит показывает состояние сайта ДО исправлений — это список задач для бота._\n\n'
            '🔧 Запустить автоисправление и создать Pull Request?',
            parse_mode='Markdown',
            reply_markup=confirm_keyboard()
        )
        await state.set_state(SEOFlow.waiting_confirm)


@dp.callback_query(SEOFlow.waiting_confirm, F.data.startswith('confirm:'))
async def handle_confirm(callback: CallbackQuery, state: FSMContext):
    answer = callback.data.split(':')[1]
    await callback.message.edit_reply_markup(reply_markup=None)

    if answer == 'no':
        data = await state.get_data()
        shutil.rmtree(data.get('tmp_dir', ''), ignore_errors=True)
        await state.clear()
        await callback.message.answer('👌 Окей, ничего не изменено.')
        return

    await state.set_state(SEOFlow.processing)
    await run_fixes(callback.message, state)



async def run_fixes(message: Message, state: FSMContext):
    """Run all SEO fixes and create PR."""
    global _processing_user_id
    data = await state.get_data()
    tmp_dir   = data['tmp_dir']
    site_dir  = data.get('site_dir', tmp_dir)
    repo_slug = data['repo_slug']
    langs     = sorted(data['selected_langs'])

    status = await message.answer('⚙️ Начинаю исправления...')

    loop = asyncio.get_event_loop()

    steps = [
        ('🧹 Очищаю archive.org скрипты...', 'fix_archive_scripts'),
        ('🔗 Добавляю canonical URLs...', 'fix_canonical'),
        ('📝 Генерирую уникальные descriptions...', 'fix_descriptions'),
        ('🗂️ Добавляю Schema.org (BreadcrumbList)...', 'fix_schema'),
        ('🖼️ Добавляю OG images...', 'fix_og_image'),
        ('🚫 Добавляю nofollow на внешние ссылки...', 'fix_nofollow'),
        ('🤖 Генерирую robots.txt...', 'fix_robots_txt'),
        ('🌍 Запускаю переводы...', 'fix_translations'),
        ('🌐 Добавляю hreflang на переведённые страницы...', 'fix_hreflang_translated'),
        ('🔗 Исправляю lang switcher...', 'fix_lang_switcher'),
    ]

    from fixes import run_all_fixes
    site_domain = data.get('site_domain')
    for step_text, step_key in steps:
        await bot.edit_message_text(text=step_text, chat_id=message.chat.id, message_id=status.message_id)
        try:
            result = await loop.run_in_executor(
                None, run_all_fixes, site_dir, step_key, langs, GROQ_API_KEY, site_domain
            )
            if not result['ok']:
                await bot.edit_message_text(
                    text=f'⚠️ {step_text[2:]}\n`{result["error"]}`',
                    chat_id=message.chat.id, message_id=status.message_id, parse_mode='Markdown'
                )
        except Exception as e:
            log.error(f'Fix step {step_key} failed: {e}')

    # Create PR — use env token or user-provided token
    effective_token = GITHUB_TOKEN or data.get('user_github_token')
    await bot.edit_message_text(text='🚀 Создаю Pull Request...', chat_id=message.chat.id, message_id=status.message_id)

    pr_url = await create_pull_request(tmp_dir, site_dir, repo_slug, langs, {}, effective_token)

    shutil.rmtree(tmp_dir, ignore_errors=True)
    _processing_user_id = None
    await state.clear()

    if pr_url:
        await bot.edit_message_text(
            text=(f'✅ *Готово!*\n\n'
                  f'Pull Request создан:\n{pr_url}\n\n'
                  f'Проверь изменения и нажми Merge.'),
            chat_id=message.chat.id, message_id=status.message_id, parse_mode='Markdown'
        )
    else:
        await bot.edit_message_text(
            text=('✅ *Исправления применены локально.*\n\n'
                  '⚠️ PR не создан — нужен GITHUB\\_TOKEN в настройках бота.'),
            chat_id=message.chat.id, message_id=status.message_id, parse_mode='Markdown'
        )


async def create_pull_request(
    tmp_dir: str, site_dir: str, repo_slug: str, langs: list, files_before: dict,
    token: str | None = None,
) -> str | None:
    """Create PR using GitHub Git Data API — uploads all site files every time."""
    token = token or GITHUB_TOKEN
    if not token:
        return None

    # Skip extensions that are too large or irrelevant for the site
    SKIP_EXTENSIONS = {'.zip', '.tar', '.gz', '.rar', '.7z', '.mp4', '.mp3', '.mov', '.avi'}

    def _git_blob_sha(data: bytes) -> str:
        import hashlib
        header = f'blob {len(data)}\0'.encode()
        return hashlib.sha1(header + data).hexdigest()

    try:
        import base64, hashlib
        from datetime import datetime
        from github import Github, InputGitTreeElement

        g = Github(token)
        gh_repo = g.get_repo(repo_slug)
        base_branch = gh_repo.default_branch
        base_commit = gh_repo.get_branch(base_branch).commit

        # Fetch existing tree SHAs — skip files whose content is already identical
        try:
            full_tree = gh_repo.get_git_tree(base_commit.commit.tree.sha, recursive=True)
            existing_shas = {item.path: item.sha for item in full_tree.tree if item.type == 'blob'}
            log.info(f'Repo has {len(existing_shas)} existing files')
        except Exception as e:
            log.warning(f'Could not fetch repo tree: {e}')
            existing_shas = {}

        tree_elements = []
        skipped = 0
        for root, dirs, files in os.walk(site_dir):
            dirs[:] = [d for d in dirs if d not in ('.git', 'node_modules')]
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext in SKIP_EXTENSIONS:
                    skipped += 1
                    continue
                fpath = os.path.join(root, fname)
                rel_in_repo = os.path.relpath(fpath, tmp_dir).replace(os.sep, '/')
                try:
                    content = Path(fpath).read_bytes()
                    if len(content) > 5 * 1024 * 1024:
                        log.warning(f'Skipping large file: {rel_in_repo} ({len(content)//1024}KB)')
                        skipped += 1
                        continue
                    # Skip if GitHub already has identical content (saves API calls + time)
                    if existing_shas.get(rel_in_repo) == _git_blob_sha(content):
                        skipped += 1
                        continue
                    blob = gh_repo.create_git_blob(
                        base64.b64encode(content).decode(), 'base64'
                    )
                    tree_elements.append(InputGitTreeElement(
                        path=rel_in_repo, mode='100644', type='blob', sha=blob.sha
                    ))
                except Exception as e:
                    log.warning(f'Skipping {rel_in_repo}: {e}')
                    skipped += 1

        log.info(f'PR: {len(tree_elements)} changed files, {skipped} unchanged/skipped')

        if not tree_elements:
            log.info('No changed files — skipping PR')
            return None

        log.info(f'Creating PR with {len(tree_elements)} changed files')

        # Create tree → commit → branch → PR
        new_tree = gh_repo.create_git_tree(
            tree_elements, base_tree=base_commit.commit.tree
        )
        new_commit = gh_repo.create_git_commit(
            message=(
                f'SEO fixes: translations ({", ".join(langs)}), schema, meta\n\n'
                f'Auto-generated by SEO Bot\n'
                f'Files changed: {len(tree_elements)}'
            ),
            tree=new_tree,
            parents=[base_commit.commit],
        )

        branch_name = 'seo-fixes'
        try:
            gh_repo.get_branch(branch_name)
            branch_name = f'seo-fixes-{datetime.now().strftime("%Y%m%d-%H%M")}'
        except Exception:
            pass

        gh_repo.create_git_ref(f'refs/heads/{branch_name}', new_commit.sha)

        pr = gh_repo.create_pull(
            title='SEO improvements: translations, schema, meta',
            body=(
                '## SEO Bot автоматические улучшения\n\n'
                '### Что сделано:\n'
                f'- 🌍 Переводы: {", ".join(langs).upper()}\n'
                '- 📝 Исправлены title/description\n'
                '- 🗂️ Добавлены Schema.org (BreadcrumbList, FAQPage)\n'
                '- 🔗 Добавлен lang switcher\n\n'
                f'Изменено файлов: {len(tree_elements)}\n\n'
                '_Создано автоматически SEO Bot_'
            ),
            head=branch_name,
            base=base_branch,
        )
        return pr.html_url

    except Exception as e:
        log.error(f'PR creation failed: {e}', exc_info=True)
        return None


# ── Audit report formatter ────────────────────────────────────────────────────

def format_audit_report(results: dict, repo_slug: str, langs: list) -> str:
    total  = results.get('total', 0)
    passed = results.get('passed', 0)
    failed = results.get('failed', 0)
    issues = results.get('issues', [])

    lines = [
        f'📊 *Аудит: {repo_slug}*\n',
        f'Страниц: {total} | ✅ {passed} | ❌ {failed}\n',
    ]

    if issues:
        lines.append('*Проблемы:*')
        for issue in issues[:10]:
            lines.append(f'  • {issue}')
        if len(issues) > 10:
            lines.append(f'  _...и ещё {len(issues) - 10}_')
    else:
        lines.append('✅ Критических проблем не найдено')

    lines.append(f'\n🌍 Переводы: {", ".join(l.upper() for l in langs)}')
    return '\n'.join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    log.info('Bot started')
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
