#!/usr/bin/env python
"""Показ расписания коллегам в локальной сети: `python tools/show_lan.py`

Зачем отдельный скрипт. `manage.py runserver` для показа не годится:

* **Однопоточный.** Браузер открывает страницу и параллельно тянет стили,
  картинки и шрифты. Пока не отдаст всё это, следующий запрос не начнёт
  разбираться — при первом открытии таблица может подвиснуть на несколько
  секунд. На своём компе это почти незаметно, у начальника на экране —
  выглядит как «сайт сдох».
* **При DEBUG=0 не отдаёт статику.** Стоит выключить DEBUG для настоящей
  работы — и таблица остаётся без оформления.
* **Перезапускается на каждом изменении файла.** Если во время показа
  случайно откроется и сохранится любой файл проекта, сервер перезапустится
  и страница отвалится посреди демонстрации.

Скрипт поднимает waitress — он многопоточный, отдаёт статику через
whitenoise сам и не следит за файлами. Настройки для показа подставляются
здесь, а не в окружении: иначе пришлось бы каждый раз вводить четыре
переменные вручную, и одна забытая переменная снова дала бы 400 или
редирект на https.

Запуск с ПК, с которого показывают:

    python tools/show_lan.py

С других компьютеров в этой же сети:

    http://<адрес>:8000
"""

import argparse
import os
import socket
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

DEFAULT_PORT = 8000


def lan_address():
    """IP-адрес машины в локальной сети, по которому её видят коллеги.

    Вариант «открыть сокет и посмотреть, что уйдёт наружу» — единственный,
    который работает без специальных прав и без правки настроек сети.
    Реального запроса он не делает: соединение рвётся сразу на
    connect_ex, без отправки данных.

    Перебираются адреса всех интерфейсов, кроме явно ненадёжных. Tailscale
    и VPN-адаптеры пропускаются: они дают адреса вида 100.x, по которым
    коллега из того же здания сервер не найдёт.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # UDP-«соединение» ничего не отправляет, но ядро выбирает исходящий
        # интерфейс так же, как для настоящего запроса.
        probe.connect(('10.255.255.255', 1))
        candidate = probe.getsockname()[0]
    except OSError:
        candidate = None
    finally:
        probe.close()

    if candidate and not candidate.startswith(('127.', '169.254.', '100.')):
        return candidate

    # Запасной путь: перебрать интерфейсы. Пригодится, если маршрут по
    # умолчанию уводит в VPN, а локальная сеть без него не видна.
    import ctypes
    import struct

    hostname = socket.gethostname()
    try:
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            addr = info[4][0]
            if not addr.startswith(('127.', '169.254.', '100.')):
                return addr
    except socket.gaierror:
        pass

    # Последний вариант — разобрать вывод ipconfig. Медленно, зато работает
    # там, где перечисление имён с адресами не сработало.
    try:
        raw = subprocess.run(
            ['ipconfig'], capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        raw = ''
    best = None
    for block in raw.split('\r\n\r\n'):
        if 'IPv4' not in block:
            continue
        for line in block.splitlines():
            if 'IPv4' not in line:
                continue
            addr = line.split(':')[-1].strip()
            if not addr or addr.startswith(('127.', '169.254.', '100.')):
                continue
            if '10.10.' in addr:
                return addr
            best = best or addr
    return best or '127.0.0.1'


def port_available(port, host='0.0.0.0'):
    """Занят ли порт кем-то ещё.

    Проверка обязательная: без неё скрипт падает с трейсбеком внутри
    waitress, и сотрудник не понимает, что произошло. Частая ситуация —
    сервер с прошлого показа ещё жив.
    """
    for family, addr in ((socket.AF_INET, host),):
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((addr, port))
        except OSError:
            return False
        finally:
            sock.close()
    return True


def static_is_fresh():
    """Актуальна ли собранная копия статики.

    Whitenoise отдаёт файлы только из ``staticfiles/``, и копия там может
    отставать от исходников: правку стилей сделали, показали коллегам,
    а ``collectstatic`` не запускали. Тогда расписание выводится с
    инлайновыми фонами, но без оформления — шрифты крупные, отступы
    чужие. Выглядит как «всё сломалось», хотя сломалось только копия.

    Поэтому сверяется не наличие папки, а время: брать самый старый файл
    из исходников и самый новый из копии.
    """
    static_root = BASE_DIR / 'staticfiles'
    if not static_root.exists():
        return False

    sources = [
        p for p in (BASE_DIR / 'schedule' / 'static').rglob('*')
        if p.is_file() and '__pycache__' not in p.parts
    ]
    if not sources:
        # Исходников нет — значит, собирать нечего.
        return True

    newest_source = max(p.stat().st_mtime for p in sources)
    copied = [
        static_root / p.relative_to(BASE_DIR / 'schedule' / 'static')
        for p in sources
    ]
    if not all(p.exists() for p in copied):
        return False

    return min(p.stat().st_mtime for p in copied) >= newest_source


def existing_server_here(port):
    """Отвечает ли на порту уже запущенное приложение.

    Нужно, чтобы не мешать работающему серверу и сказать понятное слово
    вместо отказа. Иначе получится «порт занят» — и сотрудник решит, что
    сломалось расписание, а не просто запустил скрипт дважды.
    """
    import urllib.error
    import urllib.request

    for host in ('127.0.0.1', lan_address()):
        try:
            with urllib.request.urlopen(
                f'http://{host}:{port}/healthz/', timeout=3,
            ) as resp:
                if resp.status == 200:
                    return host
        except (urllib.error.URLError, OSError, ValueError):
            continue
    return None


def apply_settings(address, port):
    """Прописывает настройки показа в окружение этого процесса.

    Отдельная функция не для красоты: настройки Django читаются при
    ``django.setup()``, то есть уже в этом процессе. Если задать их только
    в ``os.environ`` копии для подпроцессов, то collectstatic и migrate
    пройдут с правильными настройками, а сам сервер стартует со старыми и
    на первый же запрос коллеги ответит 400 «Invalid HTTP_HOST header».
    Именно так и вышло: подпроцессы видели верный ALLOWED_HOSTS, waitress —
    нет.

    Поэтому значения ставятся в ``os.environ`` напрямую, и из этого же
    окружения потом берётся копия для подпроцессов.
    """
    os.environ['DJANGO_DEBUG'] = '0'
    os.environ['DJANGO_ALLOWED_HOSTS'] = f'localhost,127.0.0.1,[::1],{address}'
    # Показ идёт по http в общей сети: редирект на https и secure-куки
    # здесь только всё ломают. На боевом сервере переменная не задаётся.
    os.environ['DJANGO_SECURE_SSL'] = '0'
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')


def build_env():
    """Копия окружения для подпроцессов.

    Берётся уже после ``apply_settings``, поэтому collectstatic и migrate
    видят ровно те же настройки, что и работающий сервер.
    """
    return dict(os.environ)


def main():
    parser = argparse.ArgumentParser(
        description='Показать расписание коллегам в локальной сети',
    )
    parser.add_argument(
        '--port', type=int, default=DEFAULT_PORT,
        help=f'порт (по умолчанию {DEFAULT_PORT})',
    )
    parser.add_argument(
        '--address', default=None,
        help='адрес в сети, если определить автоматически не вышло',
    )
    args = parser.parse_args()

    address = args.address or lan_address()
    port = args.port

    # До всего остального: и проверка занятости порта, и collectstatic
    # должны видеть те же настройки, что и сервер.
    apply_settings(address, port)

    print('=' * 62)
    print('  Показ расписания по локальной сети')
    print('=' * 62)

    # Собрать статику. Whitenoise отдаёт файлы только из staticfiles, поэтому
    # пока там нет свежей версии style.css, таблица покажется без оформления:
    # фоны приедут инлайном из HTML, а шрифты и отступы — из CSS, и блоки
    # «поедут».
    #
    # Проверяется не наличие папки, а время файлов. Раньше проверялось
    # существование каталога — и этого хватало: папка от прошлого показа
    # оставалась, повреждённый CSS считался годным, и демонстрация
    # начиналась сразу с «сломавшейся» вёрстки.
    if not static_is_fresh():
        print('\n  Собираю статику — папка со старой версией файлов...')
        result = subprocess.run(
            [sys.executable, 'manage.py', 'collectstatic', '--noinput',
             '--clear'],
            cwd=BASE_DIR, env=build_env(),
        )
        if result.returncode != 0:
            print('\n  Не удалось собрать статику. Показ не запущен.')
            return 1

    running = existing_server_here(port)
    if running:
        print(f'\n  На порту {port} уже отвечает наше приложение ({running}).')
        print('  Второй раз запускать не нужно — все подключатся к нему:')
        print(f'      http://{address}:{port}')
        return 0

    if not port_available(port):
        print(f'\n  Порт {port} занят другой программой.')
        print('  Освободите его либо запустите показ на другом порту:')
        print(f'      python tools/show_lan.py --port {port + 1}')
        return 1

    # Миграции перед стартом: забытая миграция даёт ошибку на ровном
    # месте — когда начальник уже смотрит на экран.
    check = subprocess.run(
        [sys.executable, 'manage.py', 'migrate', '--noinput'],
        cwd=BASE_DIR, env=build_env(),
        capture_output=True, text=True,
    )
    if check.returncode != 0:
        print('\n  Миграции не применились:')
        print(check.stderr[-600:] or check.stdout[-600:])
        return 1

    print(f'\n  Адрес для коллег:   http://{address}:{port}')
    print(f'  Только с этого ПК:   http://127.0.0.1:{port}')
    print('\n  Остановить: Ctrl+C')
    print('=' * 62)
    print('\n  Проверьте ДО показа, что с другого компьютера открывается.')
    print('  Если «не удаётся подключиться» — см. раздел в README про')
    print('  правило брандмауэра Windows.\n')

    from waitress import serve

    # Приложение импортируется заранее, а не передаётся строкой
    # 'config.wsgi:application'. Со строкой waitress включает
    # clear_untrusted_proxy_headers, и обёртка пытается вызвать строку
    # как функцию: «TypeError: 'str' object is not callable». Явный импорт
    # убирает этот класс ошибок целиком — и заодно позволяет проверить,
    # что объект действительно приложение WSGI, а не строка.
    import django

    django.setup()
    from config.wsgi import application

    if not callable(application):
        print('\n  config.wsgi.application не является приложением WSGI — '
              'показ не запущен.')
        return 1

    serve(
        application,
        host='0.0.0.0',
        port=port,
        # Многопоточность — ровно то, чего не хватало runserver. Двадцать
        # соединений заведомо хватает: браузер одновременно держит единицы.
        threads=20,
        # Доверять заголовкам прокси нельзя: за nginx их пишет сервер, но
        # при прямом обращении из сети их может прислать кто угодно.
        clear_untrusted_proxy_headers=True,
        ident='timetable',
    )
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\n  Остановлено.')
        sys.exit(0)