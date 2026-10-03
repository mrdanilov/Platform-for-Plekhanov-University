"""Разбор Excel-файлов с расписанием.

Поддерживаются два формата:

1. Сетка «дни в строках, пары в колонках» — как в
   ``Расписание_как_на_фото.xlsx``: в первой строке ``1 / 8:00 - 9:20``,
   ``2 / 9:30 - 10:50``…, в первом столбце ``Mn``…``Sat``, ниже ячейки с
   занятиями. Занятие в ячейке может занимать одну или несколько строк:
   ``English / EnG4 / F. Ruzmetova / 1/13``.

2. Плоская таблица — одна строка = одно занятие (см. ``importer.py``:
   колонки Группа/День/Пара/Предмет/...).
"""

import io
import re
from contextlib import nullcontext

from django.db import transaction

from .models import Group, Lesson, LessonTime, Room, Subject, Teacher

DAY_WORDS = {
    'mn': 1, 'mo': 1, 'mon': 1, 'monday': 1, 'пн': 1, 'понедельник': 1, 'понедельника': 1,
    'tu': 2, 'tue': 2, 'tues': 2, 'tuesday': 2, 'вт': 2, 'вторник': 2, 'вторника': 2,
    'we': 3, 'wed': 3, 'weds': 3, 'wednesday': 3, 'ср': 3, 'среда': 3, 'среды': 3,
    'th': 4, 'thu': 4, 'thur': 4, 'thurs': 4, 'thursday': 4,
    'чт': 4, 'четверг': 4, 'четверга': 4,
    'fr': 5, 'fri': 5, 'friday': 5, 'пт': 5, 'пят': 5, 'пятница': 5, 'пятницу': 5,
    'sa': 6, 'sat': 6, 'saturday': 6, 'сб': 6, 'суб': 6, 'суббота': 6, 'субботу': 6,
}

DAY_NAMES = {
    1: 'Понедельник', 2: 'Вторник', 3: 'Среда',
    4: 'Четверг', 5: 'Пятница', 6: 'Суббота',
}

# «1 / 8:00 - 9:20», «2 пара 10:10-11:40», «3 Class 12:00-13:30»
PAIR_HEADER_RE = re.compile(
    r'^\s*(?P<number>\d{1,2})\s*(?:пара|пары|class|classes)?\s*'
    r'[-–—:.]?\s*'
    r'(?P<start>\d{1,2}[:.]\d{2})\s*[-–—]\s*(?P<end>\d{1,2}[:.]\d{2})',
    re.I,
)
# Время без номера пары: «8:00 - 9:20»
TIME_RE = re.compile(r'^\s*(\d{1,2}[:.]\d{2})\s*[-–—]\s*(\d{1,2}[:.]\d{2})\s*$')
# Счётчик «1/13»
COUNTER_RE = re.compile(r'^\s*\d{1,3}\s*/\s*\d{1,3}\s*$')
# Группа: G1, EnG10, SE G1, SEC G2, ЭК-01, 6203
GROUP_RE = re.compile(
    r'^\s*(?:'
    r'[A-Za-zА-Яа-я]{1,5}[\s\-]?[A-Za-zА-Яа-я]{1,5}\s*G\s*\d{1,3}'  # SE G1 / SEC G2
    r'|[A-Za-zА-Яа-я]{0,5}\s*G\s*\d{1,3}'                           # G1 / EnG10
    r'|[A-Za-zА-Яа-я]{1,6}[\s\-]?\d{2,5}'                           # ЭК-01 / 6203
    r'|[A-Za-zА-Яа-я]{1,8}[\s\-]?\d{2,5}\s+\d{1,2}'                 # ЭК-01 2
    r')\s*$'
)
# Аудитория в любом месте строки: «ауд. 201», «каб 12», «Ауд. 305»
ROOM_RE = re.compile(
    r'(?:^|[\s/])(?:ауд|ауд\.|аудитория|каб|кабинет|зал|room|classroom)\.?\s*'
    r'[-–—:№]?\s*(?P<room>[A-Za-zА-Яа-я0-9][\w\-/]*)',
    re.I,
)
# Похоже на ФИО: «F. Ruzmetova», «Иванов И.И.», «M. Petrychenko»
INITIALS_LAST_RE = re.compile(
    r'^(?:[A-ZА-ЯЁ][a-zа-яё\-]{1,}\s+){1,3}(?:[A-ZА-ЯЁ]\.\s*){1,3}$'
)
INITIALS_FIRST_RE = re.compile(
    r'^(?:[A-ZА-ЯЁ]\.\s*){1,3}(?:[A-ZА-ЯЁ][a-zа-яё\-]+)(?:\s+[A-ZА-ЯЁ][a-zа-яё\-]+){0,2}\.?$'
)


def _norm(value):
    if value is None:
        return ''
    return re.sub(r'[ \t]+', ' ', str(value).replace('\xa0', ' ')).strip()


def _lines(value):
    return [_norm(line) for line in str(value).splitlines() if _norm(line)]


def _parse_time(text):
    match = re.match(r'^(\d{1,2})[:.](\d{2})$', _norm(text))
    if not match:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return None
    return f'{hours:02d}:{minutes:02d}'


def norm_day(value):
    """'Mn', 'вт', 'Понедельник', '2' -> номер дня 1..6 или None."""
    text = _norm(value).lower().replace('ё', 'е')
    if not text:
        return None
    head = re.split(r'[\s,/\\-]', text)[0]
    if head in DAY_WORDS:
        return DAY_WORDS[head]
    if text.isdigit() and 1 <= int(text) <= 6:
        return int(text)
    return None


def is_sure_teacher(line):
    """Строка с инициалами — почти наверняка ФИО: «F. Ruzmetova», «Иванов И.И.»."""
    text = _norm(line)
    if not text or norm_day(text) or GROUP_RE.match(text) or COUNTER_RE.match(text):
        return False
    if ROOM_RE.search(text) or PAIR_HEADER_RE.match(text):
        return False
    return bool(INITIALS_FIRST_RE.match(text) or INITIALS_LAST_RE.match(text))


def maybe_teacher(line):
    """Два-три слова с заглавной — может быть ФИО: «Иванов Сергей Петрович»."""
    text = _norm(line)
    if not text or norm_day(text) or GROUP_RE.match(text) or COUNTER_RE.match(text):
        return False
    if ROOM_RE.search(text) or PAIR_HEADER_RE.match(text):
        return False
    if "'" in text or '’' in text or '/' in text:
        return False
    words = re.findall(r'(?<![.])\b[A-ZА-ЯЁ][a-zа-яё]{2,}\b', text)
    return 2 <= len(words) <= 3 and len(words) == len(text.split())


def parse_cell(text):
    """Разбирает ячейку на предмет, группу, преподавателя и аудиторию.

    Порядок строк в файле значения не имеет. Преподавателем считаются строки
    с инициалами; если таких нет — строки вида «Фамилия Имя Отчество».
    Остальное — предмет.
    """
    lines = _lines(text)
    result = {
        'subject': '', 'group': '', 'teacher': '', 'room': '',
        'raw': ' / '.join(lines),
    }
    if not lines:
        return result

    subjects, teachers = [], []
    for line in lines:
        if COUNTER_RE.match(line):
            continue
        room = ROOM_RE.search(line)
        if room:
            result['room'] = room.group('room')
            head = _norm(line[:room.start()].strip(' /,;-'))
            if head:
                subjects.append(head)
            continue
        if not result['group'] and GROUP_RE.match(line):
            result['group'] = line
            continue
        subjects.append(line)

    # преподавателя ищем только после того, как разобрали остальное
    sure = [line for line in subjects if is_sure_teacher(line)]
    if sure:
        teachers = sure
        subjects = [line for line in subjects if line not in sure]
    else:
        maybe = [line for line in subjects if maybe_teacher(line)]
        if maybe and len(subjects) > 1:
            teachers = maybe
            subjects = [line for line in subjects if line not in maybe]

    result['subject'] = ' / '.join(subjects).strip(' /,;-')
    result['teacher'] = ' / '.join(teachers).strip(' /,;-')
    if not result['subject'] and not result['teacher']:
        result['subject'] = lines[0]
    return result


class Sheet:
    """Единый доступ к листу независимо от .xlsx / .xls."""

    def __init__(self, title, rows):
        self.title = title
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)

    def __len__(self):
        return len(self.rows)


MAX_SHEETS = 20
MAX_SHEET_ROWS = 5000


def _trim_sheet(rows):
    """Обрезать лист по числу строк: книга может оказаться не той."""
    return rows[:MAX_SHEET_ROWS]


def open_workbook(data, filename=''):
    """Открывает книгу как список Sheet. None, если формат не поддержан."""
    name = _norm(filename).lower()
    try:
        if name.endswith(('.xlsx', '.xlsm')):
            from openpyxl import load_workbook

            from .upload_guard import guard_zip

            # до распаковки: zip-бомба разжимается именно здесь
            guard_zip(data)
            book = load_workbook(io.BytesIO(data), data_only=True)
            return [
                Sheet(
                    sheet.title,
                    _trim_sheet(
                        [tuple(row) for row in sheet.iter_rows(values_only=True)]
                    ),
                )
                for sheet in book.worksheets[:MAX_SHEETS]
            ]
        if name.endswith('.xls'):
            import xlrd

            book = xlrd.open_workbook(file_contents=data)
            return [
                Sheet(
                    sheet.name,
                    _trim_sheet([
                        tuple(sheet.cell_value(r, c) for c in range(sheet.ncols))
                        for r in range(sheet.nrows)
                    ]),
                )
                for sheet in book.sheets()[:MAX_SHEETS]
            ]
    except Exception:
        return None
    return None


def detect_grid(sheet):
    """Ищет сетку «дни в строках, пары в колонках». None, если не нашёл.

    Шапка с парами и колонка с днями могут находиться в разных строках —
    как в «Расписание_как_на_фото.xlsx», где пары в строке 1, а дни в
    столбце A строк со 2-й по 7-ю.
    """
    header_row = None
    pairs_by_row = {}

    for index, row in enumerate(sheet.rows):
        pairs = []
        for column, value in enumerate(row):
            text = _norm(value)
            match = PAIR_HEADER_RE.match(text)
            if not match:
                continue
            start, end = _parse_time(match.group('start')), _parse_time(match.group('end'))
            if start is None or end is None or start >= end:
                continue
            pairs.append({'column': column, 'number': int(match.group('number')),
                          'start': start, 'end': end, 'label': text})
        if len(pairs) >= 2:
            header_row = index
            pairs_by_row = pairs
            break

    if header_row is None:
        return None

    # колонка с днями: та, где больше всего ячеек-дней
    columns = range(max(len(row) for row in sheet.rows) if sheet.rows else 0)
    day_column, day_count = None, 0
    for column in columns:
        count = 0
        for row in sheet.rows:
            if column < len(row) and norm_day(row[column]) is not None:
                count += 1
        if count > day_count:
            day_column, day_count = column, count

    if day_count == 0:
        return None

    # пары не должны совпадать с колонкой дней
    pairs = [p for p in pairs_by_row if p['column'] != day_column]
    if not pairs:
        return None

    days = []
    for index in range(header_row + 1, len(sheet.rows)):
        row = sheet.rows[index]
        day = norm_day(row[day_column]) if day_column < len(row) else None
        if day is None:
            continue
        days.append({'row': index, 'day': day})

    if not days:
        return None

    return {
        'header_row': header_row,
        'day_column': day_column,
        'pairs': pairs,
        'days': days,
        'rows': sheet.rows,
    }


def _ensure_lesson_times(pairs, result, dry_run=False):
    existing = {lt.number: lt for lt in LessonTime.objects.all()}
    for pair in pairs:
        number, start, end = pair['number'], pair['start'], pair['end']
        if start >= end:
            continue
        lesson_time = existing.get(number)
        if lesson_time is None:
            if dry_run:
                # проверка: не создаём, но помним пару, чтобы посчитать занятия
                lesson_time = LessonTime(number=number, start=start, end=end)
            else:
                lesson_time = LessonTime.objects.create(number=number, start=start, end=end)
            existing[number] = lesson_time
            result.notes.append(f'добавлено время пары {number}: {start}–{end}')
        elif (str(lesson_time.start)[:5], str(lesson_time.end)[:5]) != (start, end):
            result.warnings.append(
                f'Время пары {number} в файле ({start}–{end}) отличается от '
                f'настроенного ({lesson_time.start:%H:%M}–{lesson_time.end:%H:%M}); '
                'оставлено настроенное.'
            )
    return existing


def _get_or_create(store, result, model, lookup, defaults=None, bucket=None, dry_run=False):
    key = _norm(lookup).lower()
    obj = store.get(key)
    if obj is not None:
        return obj
    field = 'full_name' if model is Teacher else 'name'
    obj = model.objects.filter(**{field: lookup}).first()
    if obj is None:
        if dry_run:
            # проверка: не создаём, но запоминаем как новый объект
            obj = model(**{field: lookup, **(defaults or {})})
        else:
            obj = model.objects.create(**{field: lookup, **(defaults or {})})
        if bucket is not None:
            getattr(result, bucket).append(str(obj))
    store[key] = obj
    return obj


def import_grid(data, filename='', dry_run=False):
    """Импорт сетки вида «Расписание_как_на_фото.xlsx».

    ``dry_run=True`` — только проверка: считаем, что будет создано,
    обновлено или пропущено, ничего не записывая в базу.
    """
    from .importer import ImportResult

    result = ImportResult()
    result.notes = []
    result.sheet_name = ''

    sheets = open_workbook(data, filename)
    if sheets is None:
        result.errors.append(
            'Не удалось прочитать файл. Поддерживаются .xlsx, .xlsm и .xls.'
        )
        return result

    grid = None
    sheet = None
    for candidate in sheets:
        found = detect_grid(candidate)
        if found:
            grid, sheet = found, candidate
            break

    if grid is None:
        result.errors.append(
            'В файле не найдена сетка расписания. Ожидается строка заголовков пар '
            '(например «1 / 8:00 - 9:20») и колонка дней (Mn, Вт, Ср…).'
        )
        return result

    result.sheet_name = sheet.title

    # в режиме проверки транзакция не нужна: записей не будет
    atomic = nullcontext() if dry_run else transaction.atomic()
    with atomic:
        lesson_times = _ensure_lesson_times(grid['pairs'], result, dry_run=dry_run)
        stores = {'g': {}, 's': {}, 't': {}, 'r': {}}

        for day_info in grid['days']:
            row = grid['rows'][day_info['row']]
            for pair in grid['pairs']:
                column = pair['column']
                text = _norm(row[column]) if column < len(row) else ''
                if not text:
                    continue
                lesson_time = lesson_times.get(pair['number'])
                if lesson_time is None:
                    continue

                parts = parse_cell(text)
                group = None
                if parts['group']:
                    group = _get_or_create(
                        stores['g'], result, Group, parts['group'],
                        defaults={'course': _guess_course(parts['group'])},
                        bucket='new_groups', dry_run=dry_run,
                    )
                subject = None
                if parts['subject']:
                    subject = _get_or_create(
                        stores['s'], result, Subject, parts['subject'],
                        bucket='new_subjects', dry_run=dry_run,
                    )
                teacher = None
                if parts['teacher']:
                    teacher = _get_or_create(
                        stores['t'], result, Teacher, parts['teacher'],
                        bucket='new_teachers', dry_run=dry_run,
                    )
                room = None
                if parts['room']:
                    room = _get_or_create(
                        stores['r'], result, Room, parts['room'],
                        bucket='new_rooms', dry_run=dry_run,
                    )

                lookup = {'lesson_time': lesson_time, 'day': day_info['day'],
                          'week_parity': 0}
                # в режиме проверки новые справочники ещё без pk: искать
                # существующее занятие по ним нельзя, считаем его новым
                if dry_run and (lesson_time.pk is None
                                or (group is not None and group.pk is None)):
                    existing = None
                elif group is not None:
                    existing = Lesson.objects.filter(group=group, **lookup).first()
                else:
                    existing = Lesson.objects.filter(
                        group__isnull=True, raw_text=text, **lookup,
                    ).first()

                if existing is None:
                    if not dry_run:
                        Lesson.objects.create(
                            group=group, subject=subject, teacher=teacher, room=room,
                            day=day_info['day'], lesson_time=lesson_time,
                            week_parity=0, raw_text=text,
                        )
                    result.created += 1
                    continue

                changed = False
                for field, value in (('subject', subject), ('teacher', teacher),
                                     ('room', room)):
                    if value is not None and getattr(existing, f'{field}_id') != value.pk:
                        setattr(existing, field, value)
                        changed = True
                if changed or existing.raw_text != text:
                    if not dry_run:
                        existing.raw_text = text
                        existing.save()
                    result.updated += 1
                else:
                    result.skipped += 1

    if not result.created and not result.updated and not result.skipped:
        result.warnings.append(
            f'В листе «{sheet.title}» занятий нет — таблица пустая.'
        )
    return result


def _guess_course(group_name):
    match = re.search(r'(\d)', group_name or '')
    return int(match.group(1)) if match else 1


def _both_failed(grid_result, flat_result):
    """Ни сетка, ни плоская таблица не разобрались — сводим отчёт в один.

    Вызывается уже после отката транзакции, поэтому «файл не применён»
    здесь — правда: в базе не осталось ни одной записи ни от одной ветки.
    """
    grid_result.errors = list(grid_result.errors) + list(flat_result.errors)
    partially = flat_result.created or flat_result.updated or flat_result.skipped
    if partially:
        grid_result.warnings.append(
            'Ни один разбор не удался: файл не применён целиком. '
            f'Плоская таблица разобрана частично ({flat_result.summary()}), '
            'но из-за ошибок её записи откачены.'
        )
    else:
        grid_result.warnings.append(
            'Ни один разбор не удался: файл не применён, база не изменена.'
        )
    grid_result.source = ''
    return grid_result


def import_workbook(data, filename='', dry_run=False):
    """Сетка, а если не нашлась — плоская таблица.

    Обе ветки выполняются в одной транзакции, каждая — под своим savepoint.
    Если сетка не разобралась, её (потенциальные) записи откатываются, и
    только затем пробуется плоская таблица. Если не сработал никто — в базе
    не остаётся ни одной записи ни от одной ветки.

    В ``result.source`` пишется, какой разбор в итоге сработал:
    ``'grid'``, ``'flat'`` или ``''`` (ни один).
    """
    from .importer import import_schedule

    # не книга (например, CSV) — разбираем сразу как плоскую таблицу
    if open_workbook(data, filename) is None:
        flat = import_schedule(data, filename, dry_run=dry_run)
        flat.source = 'flat' if flat.ok else ''
        return flat

    if dry_run:
        # записи не будет: считаем план по сетке, при неудаче — по плоской
        grid_result = import_grid(data, filename, dry_run=True)
        if not grid_result.errors:
            grid_result.source = 'grid'
            return grid_result
        flat = import_schedule(data, filename, dry_run=True)
        if flat.ok:
            flat.warnings.insert(0, 'Файл разобран как плоская таблица.')
            flat.source = 'flat'
            return flat
        return _both_failed(grid_result, flat)

    # единая транзакция: либо применяется один разбор, либо ничего
    with transaction.atomic():
        # savepoint сетки: при ошибке откатываем всё, что она успела записать
        with transaction.atomic():
            grid_result = import_grid(data, filename)
            if grid_result.errors:
                transaction.set_rollback(True)
        if not grid_result.errors:
            grid_result.source = 'grid'
            return grid_result

        # .xlsx иногда хранит плоскую таблицу — пробуем второй разбор
        # в отдельном savepoint: частичная запись тоже не должна остаться
        with transaction.atomic():
            flat = import_schedule(data, filename)
            if flat.errors:
                transaction.set_rollback(True)
        if flat.ok:
            flat.warnings.insert(0, 'Файл разобран как плоская таблица.')
            flat.source = 'flat'
            return flat
        return _both_failed(grid_result, flat)


__all__ = [
    'DAY_NAMES',
    'Sheet',
    'detect_grid',
    'import_grid',
    'import_workbook',
    'is_sure_teacher',
    'maybe_teacher',
    'norm_day',
    'open_workbook',
    'parse_cell',
]