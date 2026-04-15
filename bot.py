#!/usr/bin/env python3
"""
SEO Bot — анализирует GitHub репозиторий со статическим сайтом,
исправляет SEO-проблемы и открывает Pull Request с изменениями.
"""
import asyncio
import logging
import os
import sys
import subprocess
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
WOWAI_KEY      = os.getenv('WOWAI_API_KEY', 'sk_trans_o5Un1stZ7eEG5uXovdDK_XlwzGHnqHd5lPJl9RxmA5U')

bot = Bot(token=TELEGRAM_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())

# ── Queue system — max 1 active + MAX_QUEUE waiting ──────────────────────────
from collections import deque

MAX_QUEUE = 2  # max 2 waiting jobs (3 total including active)

_active_job: dict | None = None           # currently running job
_job_queue: deque[dict] = deque()         # pending jobs

def _queue_size() -> int:
    return len(_job_queue) + (1 if _active_job else 0)

def _user_in_system(user_id: int) -> bool:
    if _active_job and _active_job['user_id'] == user_id:
        return True
    return any(j['user_id'] == user_id for j in _job_queue)

def _queue_position(user_id: int) -> int:
    """1-based position in queue (1 = next after active). 0 = is active."""
    if _active_job and _active_job['user_id'] == user_id:
        return 0
    for i, j in enumerate(_job_queue):
        if j['user_id'] == user_id:
            return i + 1
    return -1


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

ALL_TARGET_LANGS = [
    'ru', 'de', 'fr', 'es', 'it', 'pt', 'pl', 'nl', 'cs', 'ro', 'sv', 'tr',
    'el', 'uk', 'ko', 'zh', 'ja', 'sk', 'fi', 'ar', 'hi',
]

LANG_LABELS = {
    'ru': '🇷🇺 RU', 'de': '🇩🇪 DE', 'fr': '🇫🇷 FR', 'es': '🇪🇸 ES',
    'it': '🇮🇹 IT', 'pt': '🇵🇹 PT', 'pl': '🇵🇱 PL', 'nl': '🇳🇱 NL',
    'cs': '🇨🇿 CS', 'ro': '🇷🇴 RO', 'sv': '🇸🇪 SV', 'tr': '🇹🇷 TR',
    'el': '🇬🇷 EL', 'uk': '🇺🇦 UK', 'ko': '🇰🇷 KO', 'zh': '🇨🇳 ZH',
    'ja': '🇯🇵 JA', 'sk': '🇸🇰 SK', 'fi': '🇫🇮 FI', 'ar': '🇸🇦 AR',
    'hi': '🇮🇳 HI',
}


def langs_choice_keyboard() -> InlineKeyboardMarkup:
    """First-step language choice: all at once or pick manually."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f'🌍 Все языки ({len(ALL_TARGET_LANGS)})', callback_data='langchoice:all'),
            InlineKeyboardButton(text='✏️ Выбрать вручную', callback_data='langchoice:custom'),
        ]
    ])


def langs_keyboard(selected: set, exclude: set | None = None) -> InlineKeyboardMarkup:
    """Language selection keyboard. exclude: skip languages (e.g. source_lang)."""
    exclude = exclude or set()
    options = [l for l in ALL_TARGET_LANGS if l not in exclude]
    buttons = []
    for lang in options:
        label = ('✅ ' if lang in selected else '') + LANG_LABELS[lang]
        buttons.append(InlineKeyboardButton(text=label, callback_data=f'lang:{lang}'))

    rows = [buttons[i:i+4] for i in range(0, len(buttons), 4)]
    rows.append([
        InlineKeyboardButton(text='✅ Выбрать все', callback_data='lang:all'),
        InlineKeyboardButton(text='☑️ Снять все', callback_data='lang:none'),
    ])
    rows.append([InlineKeyboardButton(text='▶️ Запустить', callback_data='lang:start')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text='🔍 Только аудит', callback_data='mode:audit'),
            InlineKeyboardButton(text='🔧 Полный (SEO + перевод + PR)', callback_data='mode:full'),
        ],
        [
            InlineKeyboardButton(text='🛠️ SEO без перевода + PR', callback_data='mode:seo_only'),
            InlineKeyboardButton(text='🌍 Только перевод + PR', callback_data='mode:translate_only'),
        ],
    ])


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
    global _active_job, _job_queue
    current_state = await state.get_state()
    data = await state.get_data()

    # If processing — fully cancel and clean up
    if current_state == SEOFlow.processing:
        tmp = data.get('tmp_dir')
        if tmp and os.path.exists(tmp):
            shutil.rmtree(tmp, ignore_errors=True)
        if _active_job and _active_job['user_id'] == message.from_user.id:
            _active_job = None
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
        # Back to: token input (full/translate_only) or domain (audit)
        mode = data.get('mode', 'full')
        if mode in ('full', 'translate_only'):
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
        mode = data.get('mode', 'full')
        if mode == 'seo_only':
            # seo_only has no langs step — go back to domain
            await message.answer(
                '🌐 *Укажи домен сайта* (или Пропустить):',
                parse_mode='Markdown', reply_markup=domain_keyboard()
            )
            await state.set_state(SEOFlow.waiting_domain)
        else:
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
    uid = message.from_user.id

    # If another user has filled the queue — reject
    if not _user_in_system(uid) and _queue_size() >= MAX_QUEUE + 1:
        await message.answer(
            f'⏳ *Очередь заполнена* ({MAX_QUEUE + 1} задания).\n\n'
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


_TOKEN_PROMPT = (
    '🔑 *Нужен GitHub токен* — для скачивания репо и создания PR.\n\n'
    'Как получить:\n'
    '1. Открой [github.com/settings/tokens](https://github.com/settings/tokens)\n'
    '2. Нажми *Generate new token* → *Generate new token (classic)*\n'
    '3. Дай любое название, например `seobot`\n'
    '4. Поставь галочку на `repo` (первый пункт в списке)\n'
    '5. Нажми *Generate token* → скопируй токен (начинается с `ghp_`)\n\n'
    '_Сообщение с токеном будет удалено сразу после получения._'
)

async def _after_domain(message_or_callback, state: FSMContext):
    """Continue flow after domain step."""
    data = await state.get_data()
    mode = data.get('mode', 'audit')

    if mode == 'audit':
        await message_or_callback.answer(
            '🔍 Режим: только аудит.\n\n'
            '🌍 Выбери языки (для отчёта):',
            reply_markup=langs_choice_keyboard()
        )
        await state.set_state(SEOFlow.waiting_langs)
    elif mode == 'seo_only':
        await message_or_callback.answer(
            '🛠️ Режим: SEO без перевода + PR.\n\n' + _TOKEN_PROMPT,
            parse_mode='Markdown',
            reply_markup=token_keyboard()
        )
        await state.set_state(SEOFlow.waiting_github_token)
    else:
        mode_label = '🌍 Только перевод' if mode == 'translate_only' else '🔧 Аудит + исправления'
        await message_or_callback.answer(
            f'{mode_label} + PR.\n\n' + _TOKEN_PROMPT,
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
    mode = data.get('mode', 'full')

    if mode == 'seo_only':
        # No language selection needed — run audit + confirm directly
        await state.update_data(selected_langs=set())
        await run_audit(message, state)
    else:
        await message.answer(
            '✅ Токен принят.\n\n🌍 Выбери языки для перевода:',
            reply_markup=langs_choice_keyboard()
        )
        await state.set_state(SEOFlow.waiting_langs)


@dp.callback_query(SEOFlow.waiting_github_token, F.data == 'token:skip')
async def skip_github_token(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        '⏭️ Без токена — только аудит, PR не создастся.\n\n'
        '🌍 Выбери языки:',
        reply_markup=langs_choice_keyboard()
    )
    await state.set_state(SEOFlow.waiting_langs)
    await callback.answer()


@dp.callback_query(SEOFlow.waiting_langs, F.data.startswith('langchoice:'))
async def chose_lang_mode(callback: CallbackQuery, state: FSMContext):
    choice = callback.data.split(':')[1]
    await callback.message.edit_reply_markup(reply_markup=None)

    if choice == 'all':
        await state.update_data(selected_langs=set(ALL_TARGET_LANGS))
        await run_audit(callback.message, state)
    else:
        # Show manual selection grid
        data = await state.get_data()
        selected = data.get('selected_langs', {'ru', 'de', 'fr', 'es'})
        await callback.message.answer(
            '✏️ Выбери языки (нажми ▶️ Запустить когда готово):',
            reply_markup=langs_keyboard(selected)
        )
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

    if lang == 'all':
        selected = set(ALL_TARGET_LANGS)
    elif lang == 'none':
        selected = set()
    elif lang in selected:
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
    global _active_job

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
        await state.clear()
        return

    # If repo has a web.archive.org dump and site/ is present:
    # - First run (no lang dirs at root): delete site/ → re-extract from archive
    # - Re-run (lang dirs exist at root from merged PR): preserve site/, copy lang
    #   dirs into it so skip_existing in translate.py finds existing translations
    _LANG_LIST = ALL_TARGET_LANGS
    archive_in_repo = os.path.join(tmp_dir, 'web.archive.org')
    if os.path.isdir(archive_in_repo):
        stale_site = os.path.join(tmp_dir, 'site')
        root_langs = [l for l in _LANG_LIST if os.path.isdir(os.path.join(tmp_dir, l))]
        if os.path.isdir(stale_site) and root_langs:
            # Previously processed repo — copy root lang dirs into site/ for skip_existing
            for lang in root_langs:
                src = os.path.join(tmp_dir, lang)
                dst = os.path.join(stale_site, lang)
                if not os.path.isdir(dst):
                    shutil.copytree(src, dst)
            log.info(f'Re-run detected: copied lang dirs into site/ {root_langs}')
        elif os.path.isdir(stale_site):
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
        if _active_job and _active_job.get('user_id') == state.key.user_id:
            _active_job = None
        await state.clear()
        await message.answer('✅ Аудит завершён. Отправь новую ссылку для следующего сайта.')
    elif mode == 'seo_only':
        time_est = estimate_processing_time(site_dir, [])
        await message.answer(
            f'⏱ *Примерное время:* `{time_est}`\n\n'
            '🛠️ Запустить SEO-исправления без перевода и создать Pull Request?',
            parse_mode='Markdown',
            reply_markup=confirm_keyboard()
        )
        await state.set_state(SEOFlow.waiting_confirm)
    elif mode == 'translate_only':
        time_est = estimate_processing_time(site_dir, langs)
        await message.answer(
            f'⏱ *Примерное время:* `{time_est}`\n\n'
            '🌍 Запустить перевод + создать Pull Request?\n'
            '_SEO-исправления будут пропущены._',
            parse_mode='Markdown',
            reply_markup=confirm_keyboard()
        )
        await state.set_state(SEOFlow.waiting_confirm)
    else:
        time_est = estimate_processing_time(site_dir, langs)
        await message.answer(
            f'⏱ *Примерное время обработки:* `{time_est}`\n\n'
            '🔧 Запустить автоисправление и создать Pull Request?',
            parse_mode='Markdown',
            reply_markup=confirm_keyboard()
        )
        await state.set_state(SEOFlow.waiting_confirm)


@dp.callback_query(SEOFlow.waiting_confirm, F.data.startswith('confirm:'))
async def handle_confirm(callback: CallbackQuery, state: FSMContext):
    global _active_job, _job_queue

    answer = callback.data.split(':')[1]
    await callback.message.edit_reply_markup(reply_markup=None)

    if answer == 'no':
        data = await state.get_data()
        shutil.rmtree(data.get('tmp_dir', ''), ignore_errors=True)
        await state.clear()
        await callback.message.answer('👌 Окей, ничего не изменено.')
        return

    await state.set_state(SEOFlow.processing)

    # Check if we can start immediately or need to queue
    if _active_job is None:
        await run_fixes(callback.message, state)
    else:
        data = await state.get_data()
        pos = len(_job_queue) + 1
        _job_queue.append({
            'user_id': callback.from_user.id,
            'chat_id': callback.message.chat.id,
            'data': data,
            'mode': data.get('mode', 'full'),
        })
        await state.clear()
        await callback.message.answer(
            f'⏳ *Твой сайт поставлен в очередь* (позиция {pos}).\n\n'
            f'Бот сейчас обрабатывает другой сайт. Уведомлю когда придёт твоя очередь.',
            parse_mode='Markdown'
        )



async def run_fixes(message: Message, state: FSMContext):
    """Run all SEO fixes and create PR."""
    global _active_job, _job_queue

    data = await state.get_data()
    tmp_dir        = data['tmp_dir']
    site_dir       = data.get('site_dir', tmp_dir)
    repo_slug      = data['repo_slug']
    langs          = sorted(data['selected_langs'])
    mode           = data.get('mode', 'full')
    site_domain    = data.get('site_domain')
    translate_only = (mode == 'translate_only')
    seo_only = (mode == 'seo_only')

    # Register as active job (state.key.user_id is safe even when message is bot's own)
    _active_job = {'user_id': state.key.user_id, 'tmp_dir': tmp_dir}

    # Detect source language from the site
    from fixes import run_all_fixes
    from translate import detect_source_lang
    import functools
    source_lang = await asyncio.get_event_loop().run_in_executor(
        None, detect_source_lang, site_dir
    )
    log.info(f'Source language detected: {source_lang}')

    # Audit BEFORE fixes (to show before/after comparison)
    from audit import run_audit_on_dir
    audit_before = run_audit_on_dir(site_dir)

    if translate_only:
        start_label = 'перевод'
    elif seo_only:
        start_label = 'SEO-исправления'
    else:
        start_label = 'исправления'

    status = await message.answer(
        f'⚙️ Начинаю {start_label}...'
        + (f'\n🌐 Исходный язык сайта: `{source_lang}`' if source_lang != 'en' else ''),
        parse_mode='Markdown'
    )

    loop = asyncio.get_event_loop()

    SEO_STEPS = [
        ('🛡️ Удаляю Cloudflare заглушки...', 'fix_cloudflare_stubs'),
        ('🧹 Очищаю archive.org скрипты...', 'fix_archive_scripts'),
        ('⏳ Отключаю preloader...', 'fix_preloader'),
        ('🔗 Добавляю canonical URLs...', 'fix_canonical'),
        ('📅 Обновляю год в заголовках...', 'fix_title_refresh'),
        ('📝 Генерирую уникальные descriptions...', 'fix_descriptions'),
        ('🏷️ Добавляю H1 заголовки...', 'fix_h1'),
        ('🏷️ Добавляю H2 заголовки...', 'fix_h2'),
        ('📄 Расширяю тонкие страницы...', 'fix_thin_content'),
        ('🗂️ Добавляю Schema.org (BreadcrumbList)...', 'fix_schema'),
        ('🖼️ Добавляю OG images...', 'fix_og_image'),
        ('🐦 Добавляю Twitter Card теги...', 'fix_twitter_card'),
        ('🔗 Удаляю внешние ссылки...', 'fix_external_links'),
        ('🤖 Генерирую robots.txt...', 'fix_robots_txt'),
    ]
    TRANSLATE_STEPS = [
        ('🌍 Запускаю переводы...', 'fix_translations'),
        ('🌐 Добавляю hreflang на переведённые страницы...', 'fix_hreflang_translated'),
        ('🔗 Добавляю внутренние ссылки...', 'fix_internal_links'),
        ('🔗 Обновляю lang switcher...', 'fix_lang_switcher'),
    ]

    if translate_only:
        steps = [
            ('🌍 Запускаю переводы...', 'fix_translations'),
            ('🌐 Добавляю hreflang на переведённые страницы...', 'fix_hreflang_translated'),
            ('🔗 Обновляю lang switcher...', 'fix_lang_switcher'),
        ]
    elif seo_only:
        steps = SEO_STEPS
    else:
        steps = SEO_STEPS + TRANSLATE_STEPS

    for step_text, step_key in steps:
        await bot.edit_message_text(text=step_text, chat_id=message.chat.id, message_id=status.message_id)
        try:
            progress_cb = None
            if step_key == 'fix_translations':
                _chat_id = message.chat.id
                _msg_id = status.message_id
                def progress_cb(done, total, chat_id=_chat_id, msg_id=_msg_id):
                    asyncio.run_coroutine_threadsafe(
                        bot.edit_message_text(
                            text=f'🌍 Перевод: {done}/{total} страниц...',
                            chat_id=chat_id,
                            message_id=msg_id,
                        ),
                        loop,
                    )
            result = await loop.run_in_executor(
                None, functools.partial(
                    run_all_fixes, site_dir, step_key, langs, GROQ_API_KEY,
                    site_domain, WOWAI_KEY, progress_cb, source_lang, translate_only
                )
            )
            if not result['ok']:
                await bot.edit_message_text(
                    text=f'⚠️ {step_text[2:]}\n`{result["error"]}`',
                    chat_id=message.chat.id, message_id=status.message_id, parse_mode='Markdown'
                )
        except Exception as e:
            log.error(f'Fix step {step_key} failed: {e}')

    # Audit AFTER fixes — compare with before
    audit_after = run_audit_on_dir(site_dir)
    delta_text = _build_delta_text(audit_before, audit_after)

    # Create PR — user-provided token takes priority over env var
    effective_token = data.get('user_github_token') or GITHUB_TOKEN
    await bot.edit_message_text(text='🚀 Создаю Pull Request...', chat_id=message.chat.id, message_id=status.message_id)

    pr_url = await create_pull_request(tmp_dir, site_dir, repo_slug, langs, {}, effective_token)

    shutil.rmtree(tmp_dir, ignore_errors=True)
    _active_job = None
    await state.clear()

    # Start next queued job if any
    if _job_queue:
        next_job = _job_queue.popleft()
        await bot.send_message(
            next_job['chat_id'],
            '▶️ *Твой сайт начинает обрабатываться!*',
            parse_mode='Markdown'
        )
        asyncio.create_task(_process_queued_job(next_job))

    if pr_url:
        await bot.edit_message_text(
            text=(f'✅ *Готово!*\n\n'
                  f'Pull Request создан:\n{pr_url}\n\n'
                  f'Проверь изменения и нажми Merge.'
                  + delta_text),
            chat_id=message.chat.id, message_id=status.message_id, parse_mode='Markdown'
        )
    elif not effective_token:
        await bot.edit_message_text(
            text=('✅ *Исправления применены локально.*\n\n'
                  '⚠️ PR не создан — токен не введён.' + delta_text),
            chat_id=message.chat.id, message_id=status.message_id, parse_mode='Markdown'
        )
    else:
        await bot.edit_message_text(
            text=('✅ *Исправления применены локально.*\n\n'
                  '❌ PR не создан — ошибка GitHub API. '
                  'Проверь что токен действителен и имеет права `repo`. '
                  'Подробности в логах Railway.' + delta_text),
            chat_id=message.chat.id, message_id=status.message_id, parse_mode='Markdown'
        )


async def _process_queued_job(job: dict):
    """Start processing a job that was waiting in queue."""
    global _active_job
    _active_job = job
    # Reconstruct a minimal context to call run_fixes equivalent
    # The job dict contains all needed data — call run_fixes_from_data directly
    await run_fixes_from_data(job['chat_id'], job['data'], job.get('mode', 'full'))


async def run_fixes_from_data(chat_id: int, data: dict, mode: str):
    """
    Process a queued job: run fixes and create PR using stored data dict.
    Used when starting a queued job after the previous one finishes.
    """
    global _active_job, _job_queue

    tmp_dir        = data['tmp_dir']
    site_dir       = data.get('site_dir', tmp_dir)
    repo_slug      = data['repo_slug']
    langs          = sorted(data['selected_langs'])
    site_domain    = data.get('site_domain')
    translate_only = (mode == 'translate_only')

    from fixes import run_all_fixes
    from translate import detect_source_lang
    import functools

    seo_only = (mode == 'seo_only')

    source_lang = await asyncio.get_event_loop().run_in_executor(
        None, detect_source_lang, site_dir
    )

    from audit import run_audit_on_dir
    audit_before = run_audit_on_dir(site_dir)

    status = await bot.send_message(chat_id, '⚙️ Начинаю обработку...')
    loop = asyncio.get_event_loop()

    SEO_STEPS = [
        ('🛡️ Удаляю Cloudflare заглушки...', 'fix_cloudflare_stubs'),
        ('🧹 Очищаю archive.org скрипты...', 'fix_archive_scripts'),
        ('⏳ Отключаю preloader...', 'fix_preloader'),
        ('🔗 Добавляю canonical URLs...', 'fix_canonical'),
        ('📅 Обновляю год в заголовках...', 'fix_title_refresh'),
        ('📝 Генерирую descriptions...', 'fix_descriptions'),
        ('🗂️ Добавляю Schema.org...', 'fix_schema'),
        ('🖼️ Добавляю OG images...', 'fix_og_image'),
        ('🐦 Добавляю Twitter Card теги...', 'fix_twitter_card'),
        ('🔗 Удаляю внешние ссылки...', 'fix_external_links'),
        ('🤖 Генерирую robots.txt...', 'fix_robots_txt'),
    ]
    TRANSLATE_STEPS = [
        ('🌍 Запускаю переводы...', 'fix_translations'),
        ('🌐 Добавляю hreflang...', 'fix_hreflang_translated'),
        ('🔗 Добавляю внутренние ссылки...', 'fix_internal_links'),
        ('🔗 Обновляю lang switcher...', 'fix_lang_switcher'),
    ]

    if translate_only:
        steps = [
            ('🌍 Запускаю переводы...', 'fix_translations'),
            ('🌐 Добавляю hreflang...', 'fix_hreflang_translated'),
            ('🔗 Обновляю lang switcher...', 'fix_lang_switcher'),
        ]
    elif seo_only:
        steps = SEO_STEPS
    else:
        steps = SEO_STEPS + TRANSLATE_STEPS

    for step_text, step_key in steps:
        await bot.edit_message_text(text=step_text, chat_id=chat_id, message_id=status.message_id)
        try:
            progress_cb = None
            if step_key == 'fix_translations':
                _cid = chat_id
                _mid = status.message_id
                def progress_cb(done, total, cid=_cid, mid=_mid):
                    asyncio.run_coroutine_threadsafe(
                        bot.edit_message_text(
                            text=f'🌍 Перевод: {done}/{total} страниц...',
                            chat_id=cid, message_id=mid,
                        ), loop,
                    )
            result = await loop.run_in_executor(
                None, functools.partial(
                    run_all_fixes, site_dir, step_key, langs, GROQ_API_KEY,
                    site_domain, WOWAI_KEY, progress_cb, source_lang, translate_only
                )
            )
            if not result['ok']:
                log.warning(f'Step {step_key}: {result["error"]}')
        except Exception as e:
            log.error(f'Fix step {step_key} failed: {e}')

    audit_after = run_audit_on_dir(site_dir)
    delta_text = _build_delta_text(audit_before, audit_after)

    effective_token = data.get('user_github_token') or GITHUB_TOKEN
    await bot.edit_message_text(text='🚀 Создаю Pull Request...', chat_id=chat_id, message_id=status.message_id)
    pr_url = await create_pull_request(tmp_dir, site_dir, repo_slug, langs, {}, effective_token)

    shutil.rmtree(tmp_dir, ignore_errors=True)
    _active_job = None

    if _job_queue:
        next_job = _job_queue.popleft()
        await bot.send_message(next_job['chat_id'], '▶️ *Твой сайт начинает обрабатываться!*', parse_mode='Markdown')
        asyncio.create_task(_process_queued_job(next_job))

    result_text = (
        f'✅ *Готово!*\n\nPull Request создан:\n{pr_url}\n\nПроверь изменения и нажми Merge.' + delta_text
        if pr_url else
        f'✅ *Готово!*\n\n⚠️ PR не создан — проверь токен.' + delta_text
    )
    await bot.edit_message_text(text=result_text, chat_id=chat_id, message_id=status.message_id, parse_mode='Markdown')


async def create_pull_request(
    tmp_dir: str, site_dir: str, repo_slug: str, langs: list, files_before: dict,
    token: str | None = None,
) -> str | None:
    """
    Create PR via git clone → commit → push → GitHub API PR creation.
    Works for any repo size — no GitHub API tree size limits.
    """
    token = token or GITHUB_TOKEN
    if not token:
        return None

    try:
        import shutil
        from datetime import datetime
        from github import Github, Auth

        # ── Determine branch name ──────────────────────────────────────────────
        g = Github(auth=Auth.Token(token))
        gh_repo = g.get_repo(repo_slug)
        base_branch = gh_repo.default_branch

        branch_name = 'seo-fixes'
        try:
            gh_repo.get_branch(branch_name)
            branch_name = f'seo-fixes-{datetime.now().strftime("%Y%m%d-%H%M")}'
        except Exception:
            pass

        # ── Clone repo into a fresh directory ─────────────────────────────────
        clone_dir = os.path.join(tmp_dir, '_git_clone')
        # Remove if already exists (leftover from previous run committed to repo)
        if os.path.exists(clone_dir):
            shutil.rmtree(clone_dir, ignore_errors=True)
        clone_url = f'https://x-access-token:{token}@github.com/{repo_slug}.git'
        log.info(f'Cloning {repo_slug} → {clone_dir}')

        result = subprocess.run(
            ['git', 'clone', '--depth=1', clone_url, clone_dir],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            log.error(f'git clone failed: {result.stderr}')
            return None

        # ── Copy translated site files into clone ──────────────────────────────
        SKIP_EXTENSIONS = {'.zip', '.tar', '.gz', '.rar', '.7z', '.mp4', '.mp3', '.mov', '.avi'}
        copied = 0
        for root, dirs, files in os.walk(site_dir):
            dirs[:] = [d for d in dirs if d not in ('.git', 'node_modules', '_git_clone')]
            for fname in files:
                if os.path.splitext(fname)[1].lower() in SKIP_EXTENSIONS:
                    continue
                src = os.path.join(root, fname)
                rel = os.path.relpath(src, site_dir)
                dst = os.path.join(clone_dir, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                copied += 1

        log.info(f'Copied {copied} files into clone')

        # ── Configure git identity ─────────────────────────────────────────────
        subprocess.run(['git', 'config', 'user.email', 'seobot@noreply.github.com'],
                       cwd=clone_dir, capture_output=True)
        subprocess.run(['git', 'config', 'user.name', 'SEO Bot'],
                       cwd=clone_dir, capture_output=True)

        # ── Commit on new branch ───────────────────────────────────────────────
        subprocess.run(['git', 'checkout', '-b', branch_name],
                       cwd=clone_dir, capture_output=True)
        subprocess.run(['git', 'add', '-A'],
                       cwd=clone_dir, capture_output=True)

        commit_msg = (
            f'SEO fixes: translations ({", ".join(langs)}), schema, meta\n\n'
            f'Auto-generated by SEO Bot\n'
            f'Files changed: {copied}'
        )
        result = subprocess.run(
            ['git', 'commit', '-m', commit_msg],
            cwd=clone_dir, capture_output=True, text=True
        )
        if result.returncode != 0:
            log.info(f'git commit: {result.stdout.strip()} {result.stderr.strip()}')
            if 'nothing to commit' in result.stdout + result.stderr:
                log.info('No changes to commit')
                return None

        # ── Push ───────────────────────────────────────────────────────────────
        log.info(f'Pushing branch {branch_name}...')
        result = subprocess.run(
            ['git', 'push', 'origin', branch_name],
            cwd=clone_dir, capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            log.error(f'git push failed: {result.stderr}')
            return None

        log.info(f'Push OK — creating PR...')

        # ── Create PR via API ──────────────────────────────────────────────────
        pr = gh_repo.create_pull(
            title='SEO improvements: translations, schema, meta',
            body=(
                '## SEO Bot автоматические улучшения\n\n'
                '### Что сделано:\n'
                f'- 🌍 Переводы: {", ".join(langs).upper()}\n'
                '- 📝 Исправлены title/description\n'
                '- 🗂️ Добавлены Schema.org (BreadcrumbList, FAQPage)\n'
                '- 🔗 Добавлен lang switcher\n\n'
                f'Изменено файлов: {copied}\n\n'
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

def _build_delta_text(audit_before: dict, audit_after: dict) -> str:
    """Build a human-readable before/after audit summary."""
    cb = audit_before.get('counts', {})
    ca = audit_after.get('counts', {})
    LABELS = [
        ('no_canonical', 'canonical',    True),
        ('no_desc',      'description',  True),
        ('no_og_image',  'OG image',     True),
        ('no_schema',    'Schema.org',   True),
        ('no_h1',        'H1',           False),
        ('no_h2',        'H2',           False),
        ('thin_content', 'мало текста',  False),
        ('few_links',    'мало ссылок',  False),
    ]
    fixed_lines = []
    remaining_lines = []
    for key, label, auto_fixable in LABELS:
        before = cb.get(key, 0)
        after = ca.get(key, 0)
        if before > 0 and after == 0:
            fixed_lines.append(f'  ✅ {label}: исправлено ({before} стр.)')
        elif before > 0 and after < before:
            fixed_lines.append(f'  ✅ {label}: частично ({before}→{after} стр.)')
        elif after > 0:
            note = '' if auto_fixable else ' _(ручная правка)_'
            remaining_lines.append(f'  ⚠️ {label}: {after} стр.{note}')

    text = '\n\n📊 *Аудит до/после:*'
    if fixed_lines:
        text += '\n*Исправлено:*\n' + '\n'.join(fixed_lines)
    if remaining_lines:
        text += '\n*Остались нерешёнными:*\n' + '\n'.join(remaining_lines)
    if not fixed_lines and not remaining_lines:
        text += (f'\n  Страниц с проблемами: '
                 f'{audit_before.get("failed", 0)} → {audit_after.get("failed", 0)}')
    return text


def estimate_processing_time(site_dir: str, langs: list) -> str:
    """
    Estimate translation time based on page word counts and language count.
    Calibrated from real speedcarrace run: ~4 min per real page × 6 langs.
    """
    LANGS_REF = 6
    MIN_PER_REAL_PAGE_6L = 4.0   # empirical: 4 min per ~50-seg page × 6 langs
    MIN_PER_STUB_6L = 0.2        # stubs are near-instant

    _SKIP = set(ALL_TARGET_LANGS) | {'.git', 'node_modules', 'scripts', 'images', 'css',
                                      'web.archive.org', 'web-static.archive.org', '_git_clone'}
    real = 0
    stubs = 0
    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP]
        for fname in files:
            if not fname.endswith('.html'):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, encoding='utf-8', errors='ignore') as f:
                    html = f.read()
                body_m = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL | re.IGNORECASE)
                if body_m:
                    body = re.sub(r'<(script|style)[^>]*>.*?</(script|style)>', '', body_m.group(1),
                                  flags=re.DOTALL | re.IGNORECASE)
                    text = re.sub(r'<[^>]+>', ' ', body)
                    words = len(text.split())
                    if words >= 50:
                        real += 1
                    else:
                        stubs += 1
                else:
                    stubs += 1
            except Exception:
                stubs += 1

    n = len(langs)
    total = real + stubs
    if n == 0:
        # seo_only mode — no translation, estimate ~30s per page
        minutes = total * 0.05
        lo = max(1, int(minutes * 0.85))
        hi = max(2, int(minutes * 1.2))
        return f'~{lo}–{hi} мин ({total} стр., только SEO)'
    scale = n / LANGS_REF
    minutes = (real * MIN_PER_REAL_PAGE_6L + stubs * MIN_PER_STUB_6L) * scale
    lo = max(1, int(minutes * 0.85))
    hi = int(minutes * 1.2)
    return f'~{lo}–{hi} мин ({total} стр. × {n} яз., {real} с контентом)'


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
            lines.append(f'  • {issue.replace("_", "\\_")}')
        if len(issues) > 10:
            lines.append(f'  _...и ещё {len(issues) - 10}_')
    else:
        lines.append('✅ Критических проблем не найдено')

    if langs:
        lines.append(f'\n🌍 Переводы: {", ".join(l.upper() for l in langs)}')
    return '\n'.join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    log.info('Bot started')
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
