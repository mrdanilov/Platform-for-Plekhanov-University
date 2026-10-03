"""Монопольная блокировка импорта: один процесс за раз.

SQLite блокирует базу на запись целиком, поэтому два одновременных импорта
либо падают с «database table is locked», либо пишут частично. Здесь мы
сериализуем именно импорт: второй запрос получает внятный отказ, а не 500.

Блокировка — файл в каталоге временных загрузок. Создаём его атомарно
(`O_CREAT | O_EXCL`), поэтому способ работает и на Linux, и на Windows, где
другого общего механизма под рукой нет. Реплика при этом должна быть одна:
файл лежит на локальном диске и не виден другим хостам — это ограничение
описано в README.
"""

import os
import time
from contextlib import contextmanager
from pathlib import Path

# Столько секунд блокировка считается «живой». Если процесс упал, не сняв
# файл, по истечении срока замок перехватывается — иначе импорт отключился бы
# навсегда.
DEFAULT_STALE_SECONDS = 600

LOCK_NAME = 'import.lock'


class ImportLocked(Exception):
    """Импорт уже выполняется другим процессом."""


def _try_create(path):
    """Атомарно создать файл блокировки. None, если он уже есть."""
    try:
        return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None


def _remove(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _is_stale(path, stale_seconds):
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    return age > stale_seconds


@contextmanager
def exclusive_import_lock(tmp_dir, stale_seconds=DEFAULT_STALE_SECONDS):
    """Держит монопольный замок импорта на время блока ``with``.

    Бросает :class:`ImportLocked`, если импорт уже идёт. По выходу замок
    снимается, в том числе при исключении внутри блока.
    """
    tmp_dir = Path(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    path = tmp_dir / LOCK_NAME

    fd = _try_create(path)
    if fd is None:
        # возможно, остался от упавшего процесса — тогда перехватываем
        if _is_stale(path, stale_seconds):
            _remove(path)
            fd = _try_create(path)
        if fd is None:
            raise ImportLocked(
                'Импорт уже выполняется в другом процессе. '
                'Дождитесь его завершения и повторите.'
            )

    try:
        os.write(fd, f'{os.getpid()} {int(time.time())}'.encode())
        os.close(fd)
        yield
    finally:
        _remove(path)
