"""Проверка загружаемых файлов: расширение, содержимое и размер после распаковки.

Расширение и MIME присылает клиент, им доверять нельзя. Настоящий признак
формата — первые байты файла, их и сверяем. Плюс ограничения на то, сколько
файл распаковывает: zip-бомба весит мало, а распухает на гигабайты.
"""

import io
import zipfile

# Единственные форматы, которые умеет читать импорт.
ALLOWED_SUFFIXES = {'.xlsx', '.xlsm', '.xls', '.csv'}

# Сколько в сумме может распаковать xlsx/xlsm. Файл на диске и так ограничен
# 10 МБ, но zip жмёт повторяющиеся данные: те же нули в XML дают
# сжатие в тысячу раз, и на месте проверки оказываются гигабайты.
MAX_UNZIPPED_BYTES = 200 * 1024 * 1024
MAX_ZIP_ENTRIES = 2000

# Признаки форматов
_ZIP_MAGIC = b'PK'
_XLS_MAGIC = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
_XLSX_SUFFIXES = {'.xlsx', '.xlsm'}


def allowed_suffix(filename):
    """Расширение из белого списка в нижнем регистре, иначе пустая строка."""
    name = (filename or '').strip().lower()
    dot = name.rfind('.')
    if dot < 0:
        return ''
    suffix = name[dot:]
    return suffix if suffix in ALLOWED_SUFFIXES else ''


def looks_like(suffix, data):
    """Совпадает ли содержимое файла с заявленным расширением."""
    if not data:
        return False

    if suffix in _XLSX_SUFFIXES:
        # xlsx/xlsm — это zip-контейнер, они начинаются с PK
        return data[:2] == _ZIP_MAGIC

    if suffix == '.xls':
        # старый формат BIFF, у него своя сигнатура
        return data[:8] == _XLS_MAGIC

    if suffix == '.csv':
        # CSV обязан быть текстом в одной из кодировок импорта
        return _is_text(data)

    return False


def _is_text(data, limit=8192):
    for encoding in ('utf-8-sig', 'utf-8', 'cp1251'):
        try:
            data[:limit].decode(encoding)
        except UnicodeDecodeError:
            continue
        return True
    return False


def guard_zip(data, max_unzipped=MAX_UNZIPPED_BYTES, max_entries=MAX_ZIP_ENTRIES):
    """Проверить zip до распаковки: сколько он разжимается и сколько в нём файлов.

    openpyxl распаковывает книгу целиком, поэтому проверять надо до него.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            total = sum(i.file_size for i in infos)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError('Файл повреждён и не открывается как таблица.') from exc

    if len(infos) > max_entries:
        raise ValueError(
            f'В архиве слишком много файлов ({len(infos)}). '
            'Это не похоже на таблицу Excel.'
        )
    if total > max_unzipped:
        raise ValueError(
            f'После распаковки файл занимает {total // (1024 * 1024)} МБ — '
            f'больше, чем допустимые {max_unzipped // (1024 * 1024)} МБ.'
        )
    return True