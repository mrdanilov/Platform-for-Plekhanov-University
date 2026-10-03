"""Импорт расписания из Excel/CSV.

Формат таблицы (одна строка = одно занятие):

| Группа | День | Пара | Предмет | Тип      | Преподаватель | Аудитория | Недели |
|--------|------|------|---------|----------|---------------|-----------|--------|
| ЭК-01  | пн   | 1    | Финансы | Лекция   | Иванов И.И.   | 201       | каждую |
| ЭК-01  | вт   | 2    | Статистика | Практика | Петров П.П. | 305     | нечёт  |

Допускаются варианты записи дня: 1..5, "пн".."пят", "понедельник".."пятница".
Недели: "каждую"/"кажд"/"0"/"" → каждую; "нечёт"/"неч"/"1" → нечётные; "чёт"/"2" → чётные.

Если группа, предмет, преподаватель или аудитория отсутствуют в базе —
создаются автоматически. Занятия, уже существующие в том же слоте, обновляются
(новый преподаватель/аудитория/тип перезаписывают старые).
"""

import csv
import io
import re
from dataclasses import dataclass, field

from django.db import transaction

from .models import Group, Lesson, LessonTime, LessonType, Room, Subject, Teacher

COLUMNS = {
    'group': {'группа', 'гр', 'group'},
    'day': {'день', 'деньнедели', 'day'},
    'lesson_time': {'пара', 'пары', 'время', 'времяпары', 'lesson', 'number'},
    'subject': {'предмет', 'дисциплина', 'subject'},
    'lesson_type': {'тип', 'типзанятия', 'lessontype', 'type'},
    'teacher': {'преподаватель', 'препод', 'teacher', 'teachername', 'фио'},
    'room': {'аудитория', 'ауд', 'кабинет', 'room'},
    'week_parity': {'недели', 'неделя', 'week', 'parity'},
}

REQUIRED = ['group', 'day', 'lesson_time', 'subject']

DAY_WORDS = {
    'пн': 1, 'пон': 1, 'понедельник': 1, 'понедельника': 1, '1': 1,
    'вт': 2, 'вторник': 2, 'вторника': 2, '2': 2,
    'ср': 3, 'среда': 3, 'среды': 3, 'с': 3, '3': 3,
    'чт': 4, 'четверг': 4, 'четверга': 4, 'ч': 4, '4': 4,
    'пт': 5, 'пятница': 5, 'пятницу': 5, 'пят': 5, '5': 5,
}

PARITY_WORDS = {
    'каждую': 0, 'кажд': 0, 'каждуюнеделю': 0, 'еженедельно': 0,
    'все': 0, '0': 0, '': 0,
    'нечёт': 1, 'нечет': 1, 'нечётная': 1, 'нечётные': 1,
    'нечетная': 1, 'нечетные': 1, '1': 1,
    'чёт': 2, 'чет': 2, 'чётная': 2, 'чётные': 2,
    'четная': 2, 'четные': 2, '2': 2,
}

MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# Больше такого количества строк не разбираем: файл либо сломан, либо это
# не расписание. Страхует от книги, где один лист тянет на миллион ячеек.
MAX_ROWS = 20000

SUBJECT_COLORS = [
    '#e3f2fd', '#fff3e0', '#e8f5e9', '#fce4ec', '#ede7f6',
    '#e0f7fa', '#f1f8e9', '#ede0f5', '#fff8e1', '#fbe9e7',
]


@dataclass
class ImportResult:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    new_groups: list = field(default_factory=list)
    new_subjects: list = field(default_factory=list)
    new_teachers: list = field(default_factory=list)
    new_rooms: list = field(default_factory=list)
    new_types: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    sheet_name: str = ''
    # какой разбор сработал: 'grid' (сетка), 'flat' (плоская таблица)
    # или '' — не сработал ни один
    source: str = ''

    @property
    def ok(self):
        return not self.errors

    @property
    def source_label(self):
        """Человекочитаемое имя сработавшего разбора для UI."""
        return {
            'grid': 'сетка (дни в строках, пары в колонках)',
            'flat': 'плоская таблица (одна строка — одно занятие)',
        }.get(self.source, '')

    def summary(self):
        parts = [
            f'создано {self.created}',
            f'обновлено {self.updated}',
        ]
        if self.skipped:
            parts.append(f'пропущено {self.skipped}')
        new = []
        if self.new_groups:
            new.append(f'{len(self.new_groups)} группа')
        if self.new_subjects:
            new.append(f'{len(self.new_subjects)} предмет')
        if self.new_teachers:
            new.append(f'{len(self.new_teachers)} преподаватель')
        if self.new_rooms:
            new.append(f'{len(self.new_rooms)} аудитория')
        if self.new_types:
            new.append(f'{len(self.new_types)} тип')
        if new:
            parts.append('новых: ' + ', '.join(new))
        return '; '.join(parts)


def _norm(value):
    if value is None:
        return ''
    return re.sub(r'\s+', ' ', str(value).replace('\xa0', ' ')).strip()


def _norm_key(value):
    return _norm(value).lower().replace('ё', 'е')


def _pick(row, keys):
    """Достаёт значение колонки по списку возможных заголовков."""
    for key in keys:
        value = row.get(key)
        if value not in (None, ''):
            return value
    return ''


def _normalize_day(value, row_number, result):
    key = _norm_key(value)
    day = DAY_WORDS.get(key)
    if day is None:
        result.errors.append(f'строка {row_number}: не понял день «{_norm(value)}»')
    return day


def _normalize_parity(value):
    return PARITY_WORDS.get(_norm_key(value).replace(' ', ''), 0)


def _pick_color(subject):
    digest = sum(ord(c) for c in subject)
    return SUBJECT_COLORS[digest % len(SUBJECT_COLORS)]


def _get_or_create(result, *, groups, subjects, teachers, rooms, types):
    """Словари name -> object: берём из базы, иначе создаём."""
    def ensure(store, model, key, **kwargs):
        obj = store.get(key)
        if obj is not None:
            return obj
        obj = model.objects.filter(**kwargs).first()
        if obj is None:
            obj = model.objects.create(**kwargs)
            bucket = {
                'group': 'new_groups', 'subject': 'new_subjects',
                'teacher': 'new_teachers', 'room': 'new_rooms',
                'lessontype': 'new_types',
            }[model._meta.model_name]
            getattr(result, bucket).append(str(obj))
        store[key] = obj
        return obj

    return ensure


def _group_name_and_course(text):
    """'ЭК-01 2' или 'ЭК-01 (2 курс)' -> ('ЭК-01', 2).

    Курс распознаётся только если цифра отделена пробелом или скобкой —
    иначе «ЭК-11» превратился бы в группу «ЭК-1» первого курса.
    """
    text = _norm(text)
    match = re.match(r'^(.*\S)\s*[(\[]\s*(\d)\s*(?:курс|к\.|course)?\s*[)\]]$', text, re.I)
    if match:
        return match.group(1).strip(), int(match.group(2))
    match = re.match(r'^(.*\S)\s+(\d)\s*(?:курс|к\.|course)?$', text, re.I)
    if match:
        return match.group(1).strip(), int(match.group(2))
    return text, 1


def _split_teachers(value):
    """'Иванов И.И., Петров П.П.' -> ['Иванов И.И.', 'Петров П.П.']"""
    value = _norm(value)
    if not value:
        return []
    parts = re.split(r'[;,/]| и |\sи\s', value)
    return [_norm(p) for p in parts if _norm(p)]


def read_rows(data, filename=''):
    """Читает .xlsx или .csv и отдаёт список словарей с исходными заголовками.

    Ограничения на размер нужны и здесь, а не только при загрузке: функцию
    зовёт и импорт из сетки Excel, где файл тоже приходит снаружи.
    """
    name = _norm_key(filename)
    if name.endswith(('.xlsx', '.xlsm')):
        from openpyxl import load_workbook

        from .upload_guard import guard_zip

        # распаковку проверяем до load_workbook: книгу он читает целиком
        guard_zip(data)
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        sheet = workbook.active
        rows = []
        for index, row in enumerate(sheet.iter_rows(values_only=True)):
            if index >= MAX_ROWS:
                raise ValueError(
                    f'В файле больше {MAX_ROWS} строк. '
                    'Разбейте расписание на части.'
                )
            rows.append(row)
    else:
        text = None
        for encoding in ('utf-8-sig', 'utf-8', 'cp1251'):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError('Не удалось определить кодировку файла. Сохраните как UTF-8.')
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=',;\t')
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.reader(io.StringIO(text), dialect))

    rows = [r for r in rows if any(_norm(c) for c in r)]
    if not rows:
        raise ValueError('Файл пуст.')

    header = [_norm_key(c) for c in rows[0]]
    out = []
    for raw in rows[1:]:
        row = {}
        for i, cell in enumerate(raw):
            if i < len(header) and header[i]:
                row[header[i]] = cell
        out.append(row)
    return header, out


def import_schedule(data, filename='', *, dry_run=False):
    """Импортирует расписание. dry_run=True — только проверка, без записи."""
    result = ImportResult()

    try:
        _, rows = read_rows(data, filename)
    except Exception as exc:
        result.errors.append(str(exc))
        return result

    if not rows:
        result.errors.append('В файле нет строк с данными.')
        return result

    if dry_run:
        _check_lessons_available(rows, result)
        return result

    with transaction.atomic():
        cache = {}
        ensure = _get_or_create(result, groups=cache.setdefault('groups', {}),
                                subjects=cache.setdefault('subjects', {}),
                                teachers=cache.setdefault('teachers', {}),
                                rooms=cache.setdefault('rooms', {}),
                                types=cache.setdefault('types', {}))

        lesson_times = {str(lt.number): lt for lt in LessonTime.objects.all()}

        for row_number, row in enumerate(rows, start=2):
            group_raw = _pick(row, COLUMNS['group'])
            subject_raw = _pick(row, COLUMNS['subject'])
            day_raw = _pick(row, COLUMNS['day'])
            lt_raw = _pick(row, COLUMNS['lesson_time'])

            if not all(_norm(v) for v in (group_raw, subject_raw, day_raw, lt_raw)):
                result.skipped += 1
                continue

            day = _normalize_day(day_raw, row_number, result)
            if day is None:
                continue

            lt_number = re.sub(r'\D', '', _norm(lt_raw)) or _norm(lt_raw)
            lesson_time = lesson_times.get(str(lt_number))
            if lesson_time is None:
                result.errors.append(
                    f'строка {row_number}: пара «{_norm(lt_raw)}» не найдена в «Время пар»'
                )
                continue

            group_name, course = _group_name_and_course(group_raw)
            subject_name = _norm(subject_raw)
            teacher_name = _norm(_pick(row, COLUMNS['teacher']))
            room_name = _norm(_pick(row, COLUMNS['room']))
            type_name = _norm(_pick(row, COLUMNS['lesson_type']))
            parity = _normalize_parity(_pick(row, COLUMNS['week_parity']))

            group = ensure(cache['groups'], Group, f'{course}:{_norm_key(group_name)}',
                           name=group_name, course=course)
            subject = ensure(cache['subjects'], Subject, _norm_key(subject_name),
                             name=subject_name)
            if not subject.short_name:
                subject.short_name = subject_name[:30]
                subject.color = _pick_color(subject_name)
                subject.save()
            lesson_type = None
            if type_name:
                lesson_type = ensure(cache['types'], LessonType, _norm_key(type_name),
                                     name=type_name)
            teacher = None
            if teacher_name:
                teacher = ensure(cache['teachers'], Teacher, _norm_key(teacher_name),
                                 full_name=teacher_name)
            room = None
            if room_name:
                building, number = _split_room(room_name)
                room = ensure(cache['rooms'], Room, _norm_key(room_name),
                              name=number, building=building)

            existing = Lesson.objects.filter(
                group=group, day=day, lesson_time=lesson_time, week_parity=parity,
            ).select_related('teacher', 'room').first()

            extra_teachers = _split_teachers(teacher_name)
            if existing:
                changed = False
                if teacher and existing.teacher_id != teacher.pk:
                    existing.teacher = teacher
                    changed = True
                if room and existing.room_id != room.pk:
                    existing.room = room
                    changed = True
                if lesson_type and existing.lesson_type_id != lesson_type.pk:
                    existing.lesson_type = lesson_type
                    changed = True
                if changed:
                    existing.save()
                    result.updated += 1
                else:
                    result.skipped += 1
            else:
                Lesson.objects.create(
                    group=group, subject=subject, lesson_type=lesson_type,
                    teacher=teacher, room=room, day=day,
                    lesson_time=lesson_time, week_parity=parity,
                )
                result.created += 1

            if extra_teachers:
                result.warnings.append(
                    f'строка {row_number}: в поле преподавателя указано несколько человек '
                    f'({", ".join(extra_teachers)}), импортирован первый'
                )

    return result


def _split_room(value):
    """'Главный - 201' -> ('Главный', '201'), '201' -> ('', '201')"""
    value = _norm(value)
    match = re.match(r'^(.*?)\s*[-–—]\s*(\S+)$', value)
    if match:
        return _norm(match.group(1)), _norm(match.group(2))
    return '', value


def _check_lessons_available(rows, result):
    """Режим проверки: показываем проблемы, ничего не создавая."""
    lesson_times = {str(lt.number) for lt in LessonTime.objects.all()}
    if not lesson_times:
        result.errors.append('Не задано «Время пар» — сначала заполните его в админке.')
        return
    for row_number, row in enumerate(rows, start=2):
        group_raw = _pick(row, COLUMNS['group'])
        subject_raw = _pick(row, COLUMNS['subject'])
        day_raw = _pick(row, COLUMNS['day'])
        lt_raw = _pick(row, COLUMNS['lesson_time'])
        if not all(_norm(v) for v in (group_raw, subject_raw, day_raw, lt_raw)):
            result.skipped += 1
            continue
        day = _normalize_day(day_raw, row_number, result)
        if day is None:
            continue
        lt_number = re.sub(r'\D', '', _norm(lt_raw)) or _norm(lt_raw)
        if str(lt_number) not in lesson_times:
            result.errors.append(
                f'строка {row_number}: пара «{_norm(lt_raw)}» не найдена в «Время пар»'
            )
    result.warnings.append(
        f'Проверено строк: {len(rows)}. Режим проверки — данные не изменены.'
    )


def export_csv(queryset=None):
    """Выгрузка текущего расписания в CSV (для бэкапа и проверки импорта)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=';')
    writer.writerow([
        'Группа', 'Курс', 'День', 'Пара', 'Время', 'Предмет', 'Тип',
        'Преподаватель', 'Аудитория', 'Корпус', 'Недели',
    ])
    lessons = queryset if queryset is not None else Lesson.objects.all()
    lessons = lessons.select_related(
        'group', 'subject', 'lesson_type', 'teacher', 'room', 'lesson_time',
    ).order_by('group__name', 'day', 'lesson_time__number')

    parity_labels = {0: 'каждую', 1: 'нечёт', 2: 'чёт'}
    for lesson in lessons:
        writer.writerow([
            lesson.group.name,
            lesson.group.course,
            lesson.get_day_display(),
            lesson.lesson_time.number,
            f'{lesson.lesson_time.start:%H:%M}-{lesson.lesson_time.end:%H:%M}',
            lesson.subject.name,
            lesson.lesson_type.name if lesson.lesson_type else '',
            lesson.teacher.full_name if lesson.teacher else '',
            lesson.room.name if lesson.room else '',
            lesson.room.building if lesson.room else '',
            parity_labels.get(lesson.week_parity, 'каждую'),
        ])
    return buffer.getvalue()


def export_template_csv():
    """Пустой шаблон с заголовками и двумя строками-примерами."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=';')
    writer.writerow([
        'Группа', 'Курс', 'День', 'Пара', 'Предмет', 'Тип',
        'Преподаватель', 'Аудитория', 'Недели',
    ])
    writer.writerow([
        'ЭК-01', 1, 'пн', 1, 'Финансы', 'Лекция',
        'Иванов И.И.', 'Главный - 201', 'каждую',
    ])
    writer.writerow([
        'ЭК-01', 1, 'ср', 3, 'Статистика', 'Практика',
        'Петров П.П.', '305', 'нечёт',
    ])
    return buffer.getvalue()