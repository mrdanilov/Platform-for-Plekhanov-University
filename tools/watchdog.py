#!/usr/bin/env python
r"""Фоновый сторож платформы: `python tools\watchdog.py --daemon`

Отдельный от `show_lan.py` процесс, который поднимает показ и следит за ним.
Запускается без привязки к окну, поэтому переживает закрытие сессии, из
которой стартовал, — в отличие от `python ...` в консоли.

    Старт:      python tools\watchdog.py --start
    Остановка:  python tools\watchdog.py --stop
    Статус:     python tools\watchdog.py --status

Зачем он здесь. Показ коллегам идёт по адресу в сети, и если процесс
завершился, адрес перестаёт отвечать — коллега видит «не удаётся
подключиться». Три предыдущих попытки сделать это «просто» провалились по
одной причине: процесс, запущенный из окна PowerShell, умирал вместе с
этим окном. Поэтому здесь окно не нужно вовсе — процесс отвязывается и
работает сам.

Сторож держит платформу, а не спасает её: он проверяет `/healthz/` и
поднимает показ заново, если тот не отвечает. Проверяется ответ, а не
«жив ли процесс»: процесс может висеть в памяти и не отвечать, и такой
сторож принял бы сломанное приложение за работающее.
"""

import argparse
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Ответ спрашиваем с запасом: один пропуск — не поломка, а момент, когда
# система отдавала ресурсы другому процессу. Перезапуск на ровном месте
# рвёт у коллег открытые вкладки, что хуже самой просадки.
CHECK_INTERVAL = 15
FAILURES_BEFORE_RESTART = 3

# Пауза после перезапуска. Нужна, чтобы новый процесс успел подняться, и
# чтобы при падении «на старте» не получился плотный цикл попыток.
RESTART_COOLDOWN = 20

# Время на старт платформы внутри попытки подъёма. collectstatic при
# первом запуске занимает несколько секунд.
STARTUP_GRACE = 15

LOG_DIR = BASE_DIR / 'tmp_import'
LOG_PATH = LOG_DIR / 'watchdog.log'
SERVER_LOG = LOG_DIR / 'server.log'
PID_PATH = LOG_DIR / 'watchdog.pid'

HEALTH_PATH = '/healthz/'


def log(message):
    """Журнал и консоль одновременно.

    Консоль нужна при ручном запуске, журнал — когда процесс отвязан от
    окна и писать больше некуда. Журнал переживает перезагрузку, поэтому
    после обеда можно посмотреть, был ли обрыв.
    """
    stamp = time.strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{stamp}] {message}'
    print(line, flush=True)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open('a', encoding='utf-8') as fh:
            fh.write(line + '\n')
    except OSError:
        # Журнал вспомогательный: его недоступность не должна ронять
        # сторож, иначе потеряем и надзор, и запись о поломке.
        pass


def health_ok(port, host='127.0.0.1'):
    """Отвечает ли платформа.

    Хост 127.0.0.1, а не адрес в сети: проверка идёт с той же машины, и
    сетевой путь здесь только лишняя причина для ложного «упало».
    """
    try:
        with urllib.request.urlopen(
            f'http://{host}:{port}{HEALTH_PATH}', timeout=8,
        ) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def listening_pid(port):
    """PID процесса на порту, если он слушает."""
    try:
        probe = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             f'(Get-NetTCPConnection -LocalPort {port} -State Listen '
             '-ErrorAction SilentlyContinue) | Select-Object -First 1 '
             '-ExpandProperty OwningProcess'],
            capture_output=True, text=True, timeout=20,
        )
        pid = probe.stdout.strip()
        return int(pid) if pid.isdigit() else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def kill_pid(pid):
    try:
        subprocess.run(
            ['taskkill', '/F', '/PID', str(pid), '/T'],
            capture_output=True, timeout=30,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def start_server(port):
    """Поднимает показ. Отдельным процессом, не в потоке.

    В потоке нельзя: waitress при ошибке не всегда возвращает управление,
    и тогда сторож упал бы вместе с платформой — то есть ровно тогда,
    когда он нужен.

    Вывод сервера пишется в отдельный журнал: он нужен, чтобы после
    сбоя понять, чем именно он был вызван.
    """
    log(f'Поднимаю платформу на порту {port}...')
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handle = SERVER_LOG.open('ab')
    except OSError:
        handle = subprocess.DEVNULL

    try:
        subprocess.Popen(
            [sys.executable, 'tools/show_lan.py', '--port', str(port)],
            cwd=str(BASE_DIR),
            stdout=handle,
            stderr=subprocess.STDOUT,
            # Новая группа и без окна: остановка сторожа не должна уносить
            # платформу, и наоборот. Окна при этом не создаётся вовсе —
            # иначе на экране появляется пустая консоль без вывода.
            creationflags=detached_flags(),
        )
    except OSError as exc:
        log(f'Не удалось запустить: {exc}')
        return False

    time.sleep(STARTUP_GRACE)
    if health_ok(port):
        log('Платформа отвечает.')
        return True

    log('Платформа не отвечает после запуска. Журнал сервера:')
    try:
        tail = SERVER_LOG.read_text(
            encoding='utf-8', errors='replace',
        ).splitlines()[-10:]
        for line in tail:
            log(f'    {line}')
    except OSError:
        log('    (журнал сервера недоступен)')
    return False


def daemon_loop(port):
    """Основной цикл сторожа."""
    log('=' * 58)
    log(f'Сторож работает. Контроль: http://127.0.0.1:{port}{HEALTH_PATH}')
    log(f'Пауз между проверками: {CHECK_INTERVAL} с, порог: '
        f'{FAILURES_BEFORE_RESTART}')
    log('=' * 58)

    failures = 0
    while True:
        if health_ok(port):
            if failures:
                log('Платформа снова отвечает.')
            failures = 0
        else:
            failures += 1
            log(f'Нет ответа ({failures}/{FAILURES_BEFORE_RESTART})')

            if failures >= FAILURES_BEFORE_RESTART:
                pid = listening_pid(port)
                if pid:
                    log(f'Порт {port} держит процесс {pid} — снимаю.')
                    kill_pid(pid)
                    time.sleep(3)

                start_server(port)
                failures = 0
                time.sleep(RESTART_COOLDOWN)

        time.sleep(CHECK_INTERVAL)


# --------------------------------------------------------------- управление


def detached_flags():
    """Флаги для фонового процесса без окна на экране.

    Процесс должен переживать окно, из которого запущен, но не должен
    оставлять после себя чёрный квадрат на рабочем столе.

    Раньше здесь стоял ``DETACHED_PROCESS``, и он решал первую задачу, но
    не вторую: на Windows консоль всё равно создаётся — просто отдельная и
    пустая. Сотрудница, увидев такое окно, решила бы, что программа
    зависла.

    ``CREATE_NO_WINDOW`` делает ровно то, что нужно: окна нет вообще,
    процесс живёт сам по себе.
    """
    if os.name != 'nt':
        return getattr(subprocess, 'DETACHED_PROCESS', 8)
    return (
        getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
        | getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)
    )


def cmd_start(port):
    if read_pid() and health_ok(port):
        log(f'Платформа уже работает на порту {port}.')
        return 0

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    flags = detached_flags()

    handle = LOG_DIR / 'watchdog_out.log'
    try:
        out = handle.open('ab')
    except OSError:
        out = subprocess.DEVNULL

    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), '--daemon',
         '--port', str(port)],
        cwd=str(BASE_DIR), stdout=out, stderr=subprocess.STDOUT,
        creationflags=flags,
    )
    PID_PATH.write_text(str(proc.pid), encoding='utf-8')

    log(f'Сторож запущен, PID {proc.pid}.')
    log(f'Журнал: {LOG_PATH}')
    print(f'  Открыть расписание с другого компьютера:  http://<этот адрес>:{port}')
    return 0


def read_pid():
    """PID запущенного сторожа, если он жив.

    Файл с PID может протухнуть: сторож убили, а файл остался. Поэтому
    PID проверяется через список процессов, и протухший файл попутно
    убирается. Иначе «статус» вечно показывал бы «сторож работает» на PID
    из прошлого запуска, и никто бы не понял, что надзора нет.

    Заодно проверяется, что процесс действительно наш сторож, а не
    какое-нибудь другое приложение, которому система случайно выдала тот
    же номер.
    """
    try:
        pid = int(PID_PATH.read_text(encoding='utf-8').strip())
    except (OSError, ValueError):
        return None

    # Командная строка проверяется через PowerShell, а не через wmic:
    # wmic убран из Windows 11 и на этой машине его нет, из-за чего
    # проверка падала с FileNotFoundError. PowerShell есть везде, где
    # работает скрипт, и этот же способ уже используется выше для порта.
    try:
        probe = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             f'(Get-CimInstance Win32_Process -Filter '
             f'"ProcessId={pid}" -ErrorAction SilentlyContinue)'
             '.CommandLine'],
            capture_output=True, text=True, timeout=25,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if 'watchdog.py' not in probe.stdout:
        # Номер занят кем-то другим либо процесс уже ушёл.
        clear_pid()
        return None
    return pid


def clear_pid():
    try:
        PID_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def cmd_stop(port):
    pid = read_pid()
    if not pid:
        log('Сторож не найден — считаем, что остановлен.')
        server_pid = listening_pid(port)
        if server_pid:
            log(f'Останавливаю платформу на порту {port} (PID {server_pid}).')
            kill_pid(server_pid)
        try:
            PID_PATH.unlink(missing_ok=True)
        except OSError:
            pass
        return 0

    log(f'Останавливаю сторож PID {pid}.')
    kill_pid(pid)
    time.sleep(2)

    server_pid = listening_pid(port)
    if server_pid:
        log(f'Останавливаю платформу на порту {port} (PID {server_pid}).')
        kill_pid(server_pid)

    try:
        PID_PATH.unlink(missing_ok=True)
    except OSError:
        pass
    log('Остановлено.')
    return 0


def cmd_status(port):
    pid = read_pid()
    ok = health_ok(port)
    server = listening_pid(port)
    print(f'  сторож:    {"работает, PID " + str(pid) if pid else "НЕ РАБОТАЕТ"}')
    print(f'  платформа: {"отвечает" if ok else "НЕ ОТВЕЧАЕТ"}'
          f'{" (PID " + str(server) + ")" if server else ""}')
    print(f'  журнал:    {LOG_PATH}')
    if not pid and ok:
        # Польезное предупреждение, а не формальность: платформа работает,
        # но если она упадёт, никто её не поднимет — и это выглядит как
        # «само работает», пока не станет хуже.
        print('  ВНИМАНИЕ: сторож не запущен. Если платформа упадёт,')
        print('  поднимать придётся вручную: python tools\\watchdog.py --start')
    return 0 if (pid and ok) else 1


def main():
    parser = argparse.ArgumentParser(
        description='Сторож платформы: держит показ расписания живым',
    )
    parser.add_argument(
        '--daemon', action='store_true',
        help='внутренний режим самого сторожа',
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--start', action='store_true', help='запустить в фоне')
    group.add_argument('--stop', action='store_true', help='остановить')
    group.add_argument('--status', action='store_true', help='показать статус')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()

    if args.stop:
        return cmd_stop(args.port)
    if args.status:
        return cmd_status(args.port)
    if args.start:
        return cmd_start(args.port)
    return daemon_loop(args.port)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log('Сторож прерван вручную.')
        sys.exit(0)