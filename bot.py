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
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
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


# ── FSM States ────────────────────────────────────────────────────────────────

class SEOFlow(StatesGroup):
    waiting_repo         = State()
    waiting_langs        = State()
    waiting_confirm      = State()
    waiting_github_token = State()
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


def confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='✅ Да, исправить и создать PR', callback_data='confirm:yes'),
        InlineKeyboardButton(text='❌ Нет', callback_data='confirm:no'),
    ]])


# ── Handlers ──────────────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        '👋 Привет! Я SEO-бот.\n\n'
        'Я анализирую статические сайты, исправляю SEO-проблемы '
        'и создаю Pull Request с готовыми изменениями.\n\n'
        '📎 Отправь ссылку на GitHub репозиторий:\n'
        '`https://github.com/user/repo`',
        parse_mode='Markdown'
    )
    await state.set_state(SEOFlow.waiting_repo)


@dp.message(Command('cancel'))
async def cmd_cancel(message: Message, state: FSMContext):
    data = await state.get_data()
    tmp = data.get('tmp_dir')
    if tmp and os.path.exists(tmp):
        shutil.rmtree(tmp, ignore_errors=True)
    await state.clear()
    await message.answer('❌ Отменено.')


@dp.message(F.text.regexp(r'https?://github\.com/'))
async def got_repo(message: Message, state: FSMContext):
    """Accept GitHub URL in any state — auto-reset if needed."""
    url = message.text.strip().rstrip('/')
    log.info(f'got_repo: url={url!r}')
    # Validate GitHub URL
    m = re.match(r'https?://github\.com/([^/]+/[^/]+?)(?:\.git)?$', url)
    if not m:
        await message.answer('⚠️ Не похоже на GitHub ссылку. Пример:\n`https://github.com/user/repo`',
                             parse_mode='Markdown')
        return

    repo_slug = m.group(1)
    await state.update_data(repo_url=url, repo_slug=repo_slug, selected_langs={'ru', 'de', 'fr', 'es'})
    await message.answer(
        f'✅ Репозиторий: `{repo_slug}`\n\n'
        '🌍 Выбери языки для перевода:',
        parse_mode='Markdown',
        reply_markup=langs_keyboard({'ru', 'de', 'fr', 'es'})
    )
    await state.set_state(SEOFlow.waiting_langs)


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


async def download_repo_zip(repo_slug: str, dest_dir: str):
    """Download repo as ZIP from GitHub (async, long timeout) and extract."""
    import zipfile, io, aiohttp

    timeout = aiohttp.ClientTimeout(total=300, connect=30)

    # Use GitHub API endpoint — works even when codeload.github.com is blocked by VPN
    zip_url = f'https://api.github.com/repos/{repo_slug}/zipball'
    log.info(f'Downloading ZIP via API: {zip_url}')
    headers = {'Accept': 'application/vnd.github+json'}
    if GITHUB_TOKEN:
        headers['Authorization'] = f'Bearer {GITHUB_TOKEN}'

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(zip_url, headers=headers) as resp:
            if resp.status != 200:
                raise RuntimeError(f'GitHub API returned {resp.status}')
            data = await resp.read()
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
    data = await state.get_data()
    repo_url   = data['repo_url']
    repo_slug  = data['repo_slug']
    langs      = sorted(data['selected_langs'])

    status_msg = await message.answer('⏳ Скачиваю репозиторий...')

    # Download ZIP via GitHub API (avoids Windows colon-in-path issue with git clone)
    tmp_dir = tempfile.mkdtemp(prefix='seobot_')
    await state.update_data(tmp_dir=tmp_dir)

    try:
        await download_repo_zip(repo_slug, tmp_dir)
    except Exception as e:
        await bot.edit_message_text(
            text=f'❌ Не удалось скачать репозиторий:\n`{e}`',
            chat_id=message.chat.id, message_id=status_msg.message_id,
            parse_mode='Markdown')
        shutil.rmtree(tmp_dir, ignore_errors=True)
        await state.clear()
        return

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

    # Format report
    report = format_audit_report(results, repo_slug, langs)
    await bot.edit_message_text(
        text=report, chat_id=message.chat.id, message_id=status_msg.message_id,
        parse_mode='Markdown')

    await message.answer(
        '🔧 Запустить автоисправление и создать Pull Request?',
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

    # If no server-side token, ask the user for their GitHub token
    if not GITHUB_TOKEN:
        await callback.message.answer(
            '🔑 Для создания Pull Request нужен GitHub токен.\n\n'
            'Создай на [github.com/settings/tokens](https://github.com/settings/tokens) '
            'токен с правами `repo` и отправь его сюда.\n\n'
            '_Сообщение с токеном будет удалено сразу после получения._',
            parse_mode='Markdown'
        )
        await state.set_state(SEOFlow.waiting_github_token)
        return

    await state.set_state(SEOFlow.processing)
    await run_fixes(callback.message, state)


@dp.message(SEOFlow.waiting_github_token)
async def got_github_token(message: Message, state: FSMContext):
    token = message.text.strip() if message.text else ''

    if not token.startswith('ghp_') and not token.startswith('github_pat_'):
        await message.answer(
            '❌ Не похоже на GitHub токен (должен начинаться с `ghp_` или `github_pat_`).\n'
            'Попробуй ещё раз.',
            parse_mode='Markdown'
        )
        return

    # Try to delete the message with the token for security
    try:
        await message.delete()
    except Exception:
        await message.answer(
            '⚠️ Не могу удалить сообщение — удали его вручную для безопасности.',
        )

    await state.update_data(user_github_token=token)
    await state.set_state(SEOFlow.processing)
    await run_fixes(message, state)


def _snapshot_files(site_dir: str) -> dict:
    """Return {rel_path: bytes} for all files in site_dir."""
    snap = {}
    for root, dirs, files in os.walk(site_dir):
        dirs[:] = [d for d in dirs if d not in ('.git', 'node_modules')]
        for fname in files:
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, site_dir).replace(os.sep, '/')
            try:
                snap[rel] = Path(fpath).read_bytes()
            except Exception:
                pass
    return snap


async def run_fixes(message: Message, state: FSMContext):
    """Run all SEO fixes and create PR."""
    data = await state.get_data()
    tmp_dir   = data['tmp_dir']
    site_dir  = data.get('site_dir', tmp_dir)
    repo_slug = data['repo_slug']
    langs     = sorted(data['selected_langs'])

    status = await message.answer('⚙️ Начинаю исправления...')

    # Snapshot files BEFORE fixes to detect changes later
    loop = asyncio.get_event_loop()
    files_before = await loop.run_in_executor(None, _snapshot_files, site_dir)

    steps = [
        ('📝 Исправляю title и description...', 'fix_descriptions'),
        ('🗂️ Добавляю Schema.org (BreadcrumbList, FAQ)...', 'fix_schema'),
        ('🌍 Запускаю переводы...', 'fix_translations'),
        ('🔗 Исправляю lang switcher...', 'fix_lang_switcher'),
    ]

    from fixes import run_all_fixes
    for step_text, step_key in steps:
        await bot.edit_message_text(text=step_text, chat_id=message.chat.id, message_id=status.message_id)
        try:
            result = await loop.run_in_executor(
                None, run_all_fixes, site_dir, step_key, langs, GROQ_API_KEY
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

    pr_url = await create_pull_request(tmp_dir, site_dir, repo_slug, langs, files_before, effective_token)

    shutil.rmtree(tmp_dir, ignore_errors=True)
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
    """Create PR using GitHub Git Data API (no local git required)."""
    token = token or GITHUB_TOKEN
    if not token:
        return None

    try:
        import base64
        from datetime import datetime
        from github import Github, InputGitTreeElement

        g = Github(token)
        gh_repo = g.get_repo(repo_slug)
        base_branch = gh_repo.default_branch
        base_commit = gh_repo.get_branch(base_branch).commit

        # Find changed/new files by comparing with pre-fix snapshot
        tree_elements = []
        for root, dirs, files in os.walk(site_dir):
            dirs[:] = [d for d in dirs if d not in ('.git', 'node_modules')]
            for fname in files:
                fpath = os.path.join(root, fname)
                # Path relative to site_dir (used for snapshot comparison)
                rel_in_site = os.path.relpath(fpath, site_dir).replace(os.sep, '/')
                # Path relative to repo root (used in GitHub)
                rel_in_repo = os.path.relpath(fpath, tmp_dir).replace(os.sep, '/')
                try:
                    new_content = Path(fpath).read_bytes()
                    if files_before.get(rel_in_site) == new_content:
                        continue  # unchanged
                    blob = gh_repo.create_git_blob(
                        base64.b64encode(new_content).decode(), 'base64'
                    )
                    tree_elements.append(InputGitTreeElement(
                        path=rel_in_repo, mode='100644', type='blob', sha=blob.sha
                    ))
                except Exception as e:
                    log.warning(f'Skipping {rel_in_repo}: {e}')

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
