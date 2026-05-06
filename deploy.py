"""
deploy.py — SSH-деплой сайта на сервер после завершения пайплайна.

Env vars:
  SSH_DEPLOY_HOST  — хост (по умолч. 207.154.195.96)
  SSH_DEPLOY_USER  — пользователь (по умолч. root)
  SSH_DEPLOY_KEY   — содержимое PEM-ключа; если не задан — использует ~/.ssh/

Процесс:
  1. Упаковывает site_dir в tar.gz
  2. Загружает архив на сервер по SFTP
  3. Распаковывает в /var/www/<domain>/
  4. Создаёт nginx-конфиг если его нет, включает сайт
  5. nginx -t && systemctl reload nginx

Если бот запущен на том же сервере — SSH не нужен, файлы копируются напрямую.
"""
import io
import os
import shutil
import socket
import subprocess
import tarfile
import tempfile

SSH_HOST = os.getenv('SSH_DEPLOY_HOST', '207.154.195.96')
SSH_USER = os.getenv('SSH_DEPLOY_USER', 'root')
SSH_KEY  = os.getenv('SSH_DEPLOY_KEY', '')

NGINX_TEMPLATE = """\
server {{
    listen 80;
    server_name {domain} www.{domain};
    root /var/www/{domain};
    index index.html;
    location / {{
        try_files $uri $uri/ $uri.html =404;
    }}
}}
"""


def _is_local_host(host: str) -> bool:
    """Return True if host resolves to an IP of this machine."""
    if host in ('localhost', '127.0.0.1', '::1'):
        return True
    try:
        target_ips = {info[4][0] for info in socket.getaddrinfo(host, None)}
        # Check hostname resolution
        local_ips = {info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None)}
        local_ips.update({'127.0.0.1', '::1'})
        if target_ips & local_ips:
            return True
        # Also check all IPs assigned to network interfaces (catches public IPs on VPS)
        r = subprocess.run(['hostname', '-I'], capture_output=True, text=True)
        local_ips.update(r.stdout.split())
        return bool(target_ips & local_ips)
    except Exception:
        return False


def _deploy_local(site_dir: str, domain: str, log_fn):
    """Copy files directly when the bot runs on the target server."""
    web_root = f'/var/www/{domain}'
    log_fn(f'[deploy] Локальный деплой → {web_root}')
    os.makedirs(web_root, exist_ok=True)
    for item in os.listdir(site_dir):
        src = os.path.join(site_dir, item)
        dst = os.path.join(web_root, item)
        if os.path.isdir(src):
            if os.path.exists(dst):
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)

    nginx_avail  = f'/etc/nginx/sites-available/{domain}'
    nginx_enabled = f'/etc/nginx/sites-enabled/{domain}'
    if not os.path.exists(nginx_avail):
        log_fn(f'[deploy] Создаю nginx-конфиг для {domain}...')
        with open(nginx_avail, 'w') as f:
            f.write(NGINX_TEMPLATE.format(domain=domain))
        if not os.path.exists(nginx_enabled):
            os.symlink(nginx_avail, nginx_enabled)

    log_fn('[deploy] Перезагружаю nginx...')
    r = subprocess.run('nginx -t 2>&1 && systemctl reload nginx',
                       shell=True, capture_output=True, text=True)
    if r.returncode != 0:
        log_fn(f'[deploy] nginx ошибка: {r.stdout or r.stderr}')
        return False
    log_fn(f'[deploy] ✅ Сайт доступен: http://{domain}/')
    return True


def _connect():
    import paramiko
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    if SSH_KEY:
        pkey = paramiko.RSAKey.from_private_key(io.StringIO(SSH_KEY))
        client.connect(SSH_HOST, username=SSH_USER, pkey=pkey, timeout=15)
    else:
        client.connect(SSH_HOST, username=SSH_USER, timeout=15)
    return client


def _run(client, cmd):
    _, stdout, stderr = client.exec_command(cmd)
    exit_code = stdout.channel.recv_exit_status()
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    return exit_code, out, err


def deploy_to_server(site_dir: str, domain: str, log_fn=None):
    """
    Деплоит site_dir на сервер как /var/www/<domain>/.
    log_fn(msg) — колбэк для логирования (опционально).
    Возвращает True при успехе.
    """
    def log(msg):
        if log_fn:
            log_fn(msg)

    if _is_local_host(SSH_HOST):
        return _deploy_local(site_dir, domain, log)

    try:
        import paramiko  # noqa — проверка наличия
    except ImportError:
        log('[deploy] paramiko не установлен — пропускаю деплой')
        return False

    # ── Упаковка ──────────────────────────────────────────────────────────────
    log(f'[deploy] Упаковываю {site_dir}...')
    tmp_tar = tempfile.mktemp(suffix='.tar.gz')
    try:
        with tarfile.open(tmp_tar, 'w:gz') as tar:
            tar.add(site_dir, arcname='.')
        size_mb = os.path.getsize(tmp_tar) / 1024 / 1024
        log(f'[deploy] Архив готов: {size_mb:.1f} MB')
    except Exception as e:
        log(f'[deploy] Ошибка архивации: {e}')
        return False

    # ── Соединение ────────────────────────────────────────────────────────────
    log(f'[deploy] Подключаюсь к {SSH_HOST}...')
    try:
        client = _connect()
    except Exception as e:
        log(f'[deploy] SSH ошибка: {e}')
        os.unlink(tmp_tar)
        return False

    try:
        remote_tar = f'/tmp/{domain}.tar.gz'
        web_root   = f'/var/www/{domain}'

        # ── Загрузка архива ───────────────────────────────────────────────────
        log(f'[deploy] Загружаю архив на сервер...')
        sftp = client.open_sftp()
        sftp.put(tmp_tar, remote_tar)
        sftp.close()
        os.unlink(tmp_tar)

        # ── Распаковка ────────────────────────────────────────────────────────
        log(f'[deploy] Распаковываю в {web_root}...')
        code, out, err = _run(client, f'mkdir -p {web_root} && tar xzf {remote_tar} -C {web_root} && rm {remote_tar}')
        if code != 0:
            log(f'[deploy] Ошибка распаковки: {err}')
            return False

        # ── Nginx конфиг ──────────────────────────────────────────────────────
        nginx_avail  = f'/etc/nginx/sites-available/{domain}'
        nginx_enabled = f'/etc/nginx/sites-enabled/{domain}'

        code, out, _ = _run(client, f'test -f {nginx_avail} && echo exists || echo missing')
        if out == 'missing':
            log(f'[deploy] Создаю nginx-конфиг для {domain}...')
            conf = NGINX_TEMPLATE.format(domain=domain)
            sftp = client.open_sftp()
            with sftp.open(nginx_avail, 'w') as f:
                f.write(conf)
            sftp.close()
            _run(client, f'ln -sf {nginx_avail} {nginx_enabled}')

        # ── Перезагрузка nginx ────────────────────────────────────────────────
        log('[deploy] Проверяю и перезагружаю nginx...')
        code, out, err = _run(client, 'nginx -t 2>&1 && systemctl reload nginx')
        if code != 0:
            log(f'[deploy] nginx ошибка: {err or out}')
            return False

        log(f'[deploy] ✅ Сайт доступен: http://{domain}/')
        return True

    except Exception as e:
        log(f'[deploy] Неожиданная ошибка: {e}')
        return False
    finally:
        client.close()
