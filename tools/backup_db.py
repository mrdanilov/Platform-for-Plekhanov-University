#!/usr/bin/env python
"""Бэкап базы расписания на рабочей машине: `python tools/backup_db.py`

Почему не `copy db.sqlite3 backups\\...`: SQLite пишет в файл частями, и
копия, снятая во время записи, достаётся наполовину такой — файл
откроется, а расписание в нём сломается уже на конкретной странице.
Поэтому берётся встроенный механизм SQLite: `Connection.backup()`
собирает цельный снимок, даже пока сервер работает и сотрудница правит
расписание.

То же самое делает `tools/backup.sh` — на сервере, где есть bash и
планировщик. Этот файл его двойник для Windows, где нет ни того, ни
другого: запускается руками или кнопкой 4 из `Platform.bat`.

Копии складываются в `backups/` рядом с проектом. Папка в `.gitignore`
с намерением пошире: наружу не должен уезжать ни сам файл базы, ни
одна из его копий — в имени копии дата, а дата вкупе с содержимым базы
рассказывает о расписании больше, чем хотелось бы.

Запуск:  python tools/backup_db.py
"""

import datetime
import os
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
KEEP_DAYS = 30


def backup_dir():
    """Куда складывать копии.

    Переменные окружения читаются здесь, а не на уровне модуля: иначе
    тест пришлось бы перезапускать ради смены папки.
    """
    return Path(os.environ.get('TIMETABLE_BACKUP_DIR', BASE_DIR / 'backups'))


def database_path():
    return Path(os.environ.get('TIMETABLE_DB', BASE_DIR / 'db.sqlite3'))


def unique_target(folder):
    """Путь, который ещё не занят.

    Две копии в одну секунду — не выдумка: кнопку можно нажать дважды,
    а вторая копия молча перезаписала бы первую.
    """
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    target = folder / f'db_{stamp}.sqlite3'
    counter = 1
    while target.exists():
        counter += 1
        target = folder / f'db_{stamp}_{counter}.sqlite3'
    return target


def make_snapshot(source_path, target_path):
    """Цельный снимок средствами SQLite, а не копированием файла."""
    source = sqlite3.connect(source_path)
    try:
        target = sqlite3.connect(target_path)
        try:
            with target:
                source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def inspect(target_path):
    """Что внутри копии: целостность и число занятий.

    Целостность проверяется сразу, потому что бэкап, выяснивший свою
    порчу в день восстановления, бэкапом не является. Таблицы может не
    быть вовсю — свежий проект без миграций, — это не ошибка.
    """
    connection = sqlite3.connect(target_path)
    try:
        integrity = connection.execute('PRAGMA integrity_check').fetchone()[0]
        try:
            lessons = connection.execute(
                'SELECT COUNT(*) FROM schedule_lesson').fetchone()[0]
        except sqlite3.OperationalError:
            lessons = None
    finally:
        connection.close()
    return integrity, lessons


def drop_expired(folder):
    """Убрать копии старше срока хранения.

    Как и на сервере, одна предыдущая копия остаётся всегда: если сбой
    заметили только сейчас, вчерашняя ещё может пригодиться.
    """
    cutoff = (
        datetime.datetime.now() - datetime.timedelta(days=KEEP_DAYS)
    ).timestamp()
    removed = 0
    kept_previous = False
    for path in sorted(folder.glob('db_*.sqlite3'), reverse=True):
        expired = path.stat().st_mtime < cutoff
        if not expired:
            continue
        if not kept_previous:
            # Самая новая из просроченных: пусть полежит ещё.
            kept_previous = True
            continue
        path.unlink()
        removed += 1
    return removed


def main():
    source = database_path()
    if not source.exists():
        print(f'  Нет файла базы: {source}', file=sys.stderr)
        return 1

    folder = backup_dir()
    folder.mkdir(parents=True, exist_ok=True)

    target = unique_target(folder)
    try:
        make_snapshot(source, target)
        integrity, lessons = inspect(target)
    except sqlite3.Error as error:
        print(f'  Копия не получилась: {error}', file=sys.stderr)
        target.unlink(missing_ok=True)
        return 1

    if integrity != 'ok':
        print(f'  Копия повреждена: {integrity}', file=sys.stderr)
        target.unlink(missing_ok=True)
        return 1

    size_kb = round(target.stat().st_size / 1024)
    relative = target.relative_to(BASE_DIR) if target.is_relative_to(
        BASE_DIR) else target
    print(f'  Бэкап: {relative} ({size_kb} КБ)')
    if lessons is None:
        print('  Таблиц занятий в копии нет — база ещё не создана.')
    else:
        print(f'  Занятий в копии: {lessons}')

    removed = drop_expired(folder)
    if removed:
        print(f'  Старых копий убрано: {removed} (храню {KEEP_DAYS} дней)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
