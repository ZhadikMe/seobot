"""
deploy.py — деплой сайта на сервер после завершения пайплайна.

Каждый сайт получает свой порт начиная с 8081:
  http://207.154.195.96:8081/ — первый сайт
  http://207.154.195.96:8082/ — второй сайт
  ...

Порт сохраняется в nginx-конфиге и переиспользуется при повторном деплое.

Env vars:
  SSH_DEPLOY_HOST  — хост (по умолч. 207.154.195.96)
  SSH_DEPLOY_USER  — пользователь (по умолч. root)
  SSH_DEPLOY_KEY   — содержимое PEM-ключа; если не задан — использует ~/.ssh/
"""
import io
import os
import re
import shutil
import socket
import subprocess
import tarfile
import tempfile

SSH_HOST = os.getenv('SSH_DEPLOY_HOST', '207.154.195.96')
SSH_USER = os.getenv('SSH_DEPLOY_USER', 'root')
SSH_KEY  = os.getenv('SSH_DEPLOY_KEY', '')

PORT_START = 8081

NGINX_TEMPLATE = """\
server {{
    listen {port};
    # domain: {domain}
    root /var/www/{domain};
    index index.html;
    location / {{
        try_files $uri $uri/ $uri.html =404;
    }}
}}
"""


def _next_port() -> int:
    """Find the next free port by scanning existing nginx site configs."""
    used = set()
    sites_dir = '/etc/nginx/sites-available'
    if os.path.isdir(sites_dir):
        for fname in os.listdir(sites_dir):
            try:
                text = open(os.path.join(sites_dir, fname)).read()
                for m in re.finditer(r'listen\s+(\d+)', text):
                    p = int(m.group(1))
                    if p >= PORT_START:
                        used.add(p)
            except Exception:
                pass
    port = PORT_START
    while port in used:
        port += 1
    return port


def _port_for_domain(domain: str) -> int | None:
    """Return existing port for domain if already deployed, else None."""
    cfg = f'/etc/nginx/sites-available/{domain}'
    if not os.path.exists(cfg):
        return None
    try:
        text = open(cfg).read()
        m = re.search(r'listen\s+(\d+)', text)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def _is_local_host(host: str) -> bool:
    """Return True if host resolves to an IP of this machine."""
    if host in ('localhost', '127.0.0.1', '::1'):
        return True
    try:
        target_ips = {info[4][0] for info in socket.getaddrinfo(host, None)}
        local_ips  = {info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None)}
        local_ips.update({'127.0.0.1', '::1'})
        if target_ips & local_ips:
            return True
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

    nginx_avail   = f'/etc/nginx/sites-available/{domain}'
    nginx_enabled = f'/etc/nginx/sites-enabled/{domain}'

    # Reuse existing port or allocate a new one
    port = _port_for_domain(domain) or _next_port()

    if not os.path.exists(nginx_avail):
        log_fn(f'[deploy] Создаю nginx-конфиг для {domain} на порту {port}...')
        with open(nginx_avail, 'w') as f:
            f.write(NGINX_TEMPLATE.format(domain=domain, port=port))
        if not os.path.exists(nginx_enabled):
            os.symlink(nginx_avail, nginx_enabled)

    log_fn('[deploy] Перезагружаю nginx...')
    r = subprocess.run('nginx -t 2>&1 && systemctl reload nginx',
                       shell=True, capture_output=True, text=True)
    if r.returncode != 0:
        log_fn(f'[deploy] nginx ошибка: {r.stdout or r.stderr}')
        return False

    preview = f'http://{SSH_HOST}:{port}/'
    log_fn(f'[deploy] ✅ Превью: {preview}')
    return preview


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
    Возвращает URL превью при успехе, False при ошибке.
    """
    def log(msg):
        if log_fn:
            log_fn(msg)

    if _is_local_host(SSH_HOST):
        return _deploy_local(site_dir, domain, log)

    try:
        import paramiko  # noqa
    except ImportError:
        log('[deploy] paramiko не установлен — пропускаю деплой')
        return False

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

        log('[deploy] Загружаю архив на сервер...')
        sftp = client.open_sftp()
        sftp.put(tmp_tar, remote_tar)
        sftp.close()
        os.unlink(tmp_tar)

        log(f'[deploy] Распаковываю в {web_root}...')
        code, out, err = _run(client, f'mkdir -p {web_root} && tar xzf {remote_tar} -C {web_root} && rm {remote_tar}')
        if code != 0:
            log(f'[deploy] Ошибка распаковки: {err}')
            return False

        nginx_avail   = f'/etc/nginx/sites-available/{domain}'
        nginx_enabled = f'/etc/nginx/sites-enabled/{domain}'

        code, out, _ = _run(client, f'test -f {nginx_avail} && echo exists || echo missing')
        if out == 'missing':
            # Allocate port remotely
            code, ports_out, _ = _run(client, f"grep -rh 'listen' /etc/nginx/sites-available/ | grep -oP '\\d+' | sort -n | tail -1")
            try:
                port = max(int(ports_out.strip()), PORT_START - 1) + 1
            except Exception:
                port = PORT_START
            log(f'[deploy] Создаю nginx-конфиг для {domain} на порту {port}...')
            conf = NGINX_TEMPLATE.format(domain=domain, port=port)
            sftp = client.open_sftp()
            with sftp.open(nginx_avail, 'w') as f:
                f.write(conf)
            sftp.close()
            _run(client, f'ln -sf {nginx_avail} {nginx_enabled}')
        else:
            # Read existing port
            code, conf_out, _ = _run(client, f"grep -oP 'listen\\s+\\K\\d+' {nginx_avail}")
            try:
                port = int(conf_out.strip())
            except Exception:
                port = PORT_START

        log('[deploy] Проверяю и перезагружаю nginx...')
        code, out, err = _run(client, 'nginx -t 2>&1 && systemctl reload nginx')
        if code != 0:
            log(f'[deploy] nginx ошибка: {err or out}')
            return False

        preview = f'http://{SSH_HOST}:{port}/'
        log(f'[deploy] ✅ Превью: {preview}')
        return preview

    except Exception as e:
        log(f'[deploy] Неожиданная ошибка: {e}')
        return False
    finally:
        client.close()
