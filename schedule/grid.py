from datetime import date, timedelta

from django.conf import settings

from .models import LessonTime, Subject

# Логика недели живёт здесь, в модуле про расписание, и отсюда её берут
# представления. Копии в views.py и в тестах разъезжались: правило недели —
# это одно и то же знание, а писать его в двух местах значит гарантированно
# получить расхождение (задача 13 аудита).
#
# Публичные имена — без подчёркивания, потому что это не внутренние детали
# модуля, а общий контракт приложения.


def monday_of(d):
    """Понедельник недели, которой принадлежит дата ``d``."""
    return d - timedelta(days=d.weekday())


def iso_week(d):
    """Дата -> 'YYYY-Www' в формате параметра ``?week=``."""
    year, week, _ = d.isocalendar()
    return f'{year}-W{week:02d}'


def parse_week(value):
    """'YYYY-Www' или ISO-дата -> понедельник недели. Мусор -> текущая неделя."""
    from django.utils import timezone

    today = timezone.localdate()
    default = monday_of(today)
    if not value:
        return default
    value = str(value).strip()
    try:
        if '-W' in value.upper():
            year, week = value.upper().split('-W')
            return date.fromisocalendar(int(year), int(week), 1)
        return monday_of(date.fromisoformat(value))
    except (ValueError, TypeError):
        return default


def week_param(week_monday):
    """Значение для ``?week=``. Синоним iso_week, назван по месту применения.

    Отдельное имя нужно, чтобы в шаблонах и в коде было видно, где речь
    идёт о параметре ссылки, а не о дате вообще.
    """
    return iso_week(week_monday)


def _parity(week_monday):
    """Нечётная (1) или чётная (2) неделя — так их считает вуз.

    Считается от начала семестра, а не по номеру ISO-недели календаря.
    Номера не совпадают, и из-за этого расписание показывалось не в те
    недели, для которых оно составлено: 12 октября 2026 — это 42-я
    ISO-неделя (чётная), но 7-я неделя семестра, то есть нечётная.

    Дата начала семестра лежит в настройках
    (``SEMESTER_FIRST_MONDAY``) и меняется раз в семестр. Если её нет,
    остаётся прежнее поведение по ISO-неделе — но для расписания вуза оно
    неверно, поэтому значение в настройках задано.
    """
    first = getattr(settings, 'SEMESTER_FIRST_MONDAY', None)
    if first is None:
        return 1 if week_monday.isocalendar()[1] % 2 else 2
    weeks_since = (week_monday - first).days // 7
    return 1 if weeks_since % 2 == 0 else 2


def _shift_day(monday, day_number):
    return monday + timedelta(days=day_number - 1)


def build_days(request=None):
    """Строки таблицы: дни недели в порядке файла (Пн..Сб), локализованные."""
    from .i18n import day_short, translate

    days = []
    for num, _name in settings.WEEKDAYS:
        if request is None:
            name = dict(settings.WEEKDAYS)[num]
            short = day_short(num)
        else:
            name = translate(request, f'day_{num}')
            short = translate(request, f'dow_{num}')
        days.append({
            'num': num,
            'name': name,
            'short': short,
            'color': settings.DAY_COLORS.get(num, '#e7e6e6'),
        })
    return days


def merge_combined(cells):
    """Сливает объединённые группы в одно занятие.

    Преподаватель в один момент ведёт одну пару, и если в файле она стоит у
    нескольких групп, это одна лекция для объединённой группы, а не
    несколько разных занятий. Раньше они рисовались рядом отдельными
    карточками — и на виде «Пара» выглядело так, будто у всех групп в
    девять утра по своему предмету в спортзале.

    Объединёнными считаются занятия, у которых совпадают день, пара,
    предмет, преподаватель и аудитория, а группы разные. Аудитория в ключе
    обязательна: один преподаватель в двух разных залах в один момент —
    это не объединение, а ошибка в расписании, и прятать её слиянием
    нельзя. Отсутствие аудитории у обеих записей считается совпадением.

    Возвращает новый список: первое занятие группы поглощает остальные и
    получает список combined_groups. Порядок карточек сохраняется.
    """
    merged = []
    by_key = {}
    for cell in cells:
        key = (
            cell['subject'].pk if cell.get('subject') else cell.get('raw_text'),
            cell['teacher'].pk if cell.get('teacher') else None,
            cell['room'].pk if cell.get('room') else None,
        )
        head = by_key.get(key)
        if head is None:
            cell['combined_groups'] = []
            by_key[key] = cell
            merged.append(cell)
            continue
        groups = head['combined_groups']
        for group in (head.get('group'), cell.get('group')):
            if group is not None and group not in groups:
                groups.append(group)
    for cell in merged:
        groups = cell['combined_groups']
        if groups:
            groups.sort(key=lambda g: g.name)
            cell['group'] = groups[0]
    return merged


def _run_key(cell):
    """Ключ непрерывного занятия: тот же предмет и тот же преподаватель."""
    subject = cell.get('subject')
    name = subject.pk if subject else ('raw', cell.get('raw_text'))
    teacher = cell.get('teacher')
    return (name, teacher.pk if teacher else None)


def _span_width(root, row_cells, lesson_times, position):
    """Ширина растянутой пары, если соседние слоты заняты её хвостами.

    None, когда растяжку применять нельзя: у соседней пары нашлось чужое
    занятие. Применять нельзя именно тогда — широкая ячейка закрыла бы
    чужую карточку, и она бы просто исчезла из расписания.
    """
    wanted = root.get('spans') or 1
    if wanted <= 1:
        return None
    if position + wanted > len(lesson_times):
        return None
    root_pk = root['lesson'].pk
    for step in range(1, wanted):
        for cell in row_cells.get(lesson_times[position + step].pk, []):
            if cell.get('extend_of_id') != root_pk:
                return None
    return wanted


def _slot_columns(row_cells, lesson_times, merge_runs=True):
    """Собирает колонки строки, считая ширину каждой ячейки.

    Ширина бывает трёх видов, и порядок важен:

    1. **Растянутая пара** — сотрудница нажала «Растянуть» в админке,
       поэтому в ``spans`` стоит 2, 3 или 4. Это её явное намерение,
       и оно уважается во всех видах: в расписании группы пара тоже
       одна лекция, просто занимает три колонки.
    2. **Хвост растянутой пары** — отдельная запись в соседней колонке.
       Она не рисуется: карточку уже нарисовал корень на всю ширину.
    3. **Автосклейка** — одинаковые пары одного преподавателя подряд в
       окне кабинета. Только для занятий с включённой галочкой
       ``auto_join``: снятая галочка означает «покажи раздельно».
    """
    columns = []
    position = 0
    while position < len(lesson_times):
        cells = row_cells.get(lesson_times[position].pk, [])

        # Хвост растянутой пары: слот пропускаем целиком, колонку не выдаём.
        if cells and all(cell.get('extend_of_id') for cell in cells):
            position += 1
            continue

        width = None
        root = cells[0] if cells else None
        stretched = False
        if root is not None:
            width = _span_width(root, row_cells, lesson_times, position)
            stretched = width is not None
            if width is None and merge_runs:
                width = _run_span(cells, row_cells, lesson_times, position)
        width = width or 1

        if width > 1:
            column_cells = []
            for step in range(width):
                column_cells.extend(
                    row_cells.get(lesson_times[position + step].pk, []))
            if stretched:
                # Хвосты растянутой пары — это та же лекция, а не новые
                # занятия. Показывать их второй раз нельзя: карточка уже
                # растянута на всю ширину. Зато чужие занятия, если они
                # попались в эти пары, остаются видимыми.
                column_cells = [c for c in column_cells
                                if not c.get('extend_of_id')]
            else:
                _join_groups(cells[0], column_cells)
        else:
            column_cells = cells

        columns.append({
            'lt': lesson_times[position],
            'colspan': width,
            'cells': column_cells,
            'pairs_label': _pairs_label(lesson_times, position, width),
        })
        position += width
    return columns


def _run_span(cells, row_cells, lesson_times, position):
    """Ширина автосклейки: одинаковые пары одного преподавателя подряд."""
    if len(cells) != 1:
        return 1
    head = cells[0]
    if not head.get('auto_join', True) or head.get('extend_of_id'):
        return 1
    key = _run_key(head)
    span = 1
    while position + span < len(lesson_times):
        following = row_cells.get(lesson_times[position + span].pk, [])
        if not following or following[0].get('extend_of_id'):
            break
        if not following[0].get('auto_join', True):
            break
        if _run_key(following[0]) != key:
            break
        # В паре может стоять второе занятие — например, в спортзале вместе
        # с физкультурой идёт электив другой группы. Серия через него
        # проходит: карточка лекции тянется на ширину всех пар, а второе
        # занятие показывается под ней вторым блоком, а не исчезает.
        span += 1
    return span


def _join_groups(head, column_cells):
    """Собирает коды групп всей серии в одну строку."""
    groups = head['combined_groups']
    for cell in column_cells:
        group = cell.get('group')
        if group is not None and group not in groups:
            groups.append(group)
    groups.sort(key=lambda g: g.name)
    head['combined_groups'] = groups


def _pairs_label(lesson_times, position, span):
    """Подпись «пара 1, 2, 3» для растянутой лекции."""
    if span < 2:
        return ''
    numbers = [lt.number for lt in
               lesson_times[position:position + span]]
    return ' '.join(f'{n}-я' for n in numbers) + ' пары'


def _has_stretched(row_cells):
    """Есть ли в строке растянутая пара.

    Ячейки собираются всегда, когда в строке есть растяжка: сотрудница
    жала кнопку, и это должно увидеть любое окно, а не только кабинет.
    """
    return any(cell and (cell.get('spans') or 1) > 1
               for cells in row_cells.values() for cell in cells)


def build_substitution_grid(*, week_monday, request=None, teacher=None):
    """Сетка замен за неделю — та же таблица, но наполненная заменами.

    Показываются обе стороны: и замены, где выбранный преподаватель ведёт
    занятие вместо коллеги, и те, где его самого заменили. Иначе сотрудник,
    которого подменяют, своей замены не увидит.

    Чётность не проверяется: замена привязана к конкретной дате, а не к
    номеру недели.
    """
    from django.db.models import Q

    from .models import Substitution

    days = build_days(request)
    dates = {d['num']: _shift_day(week_monday, d['num']) for d in days}
    start, end = min(dates.values()), max(dates.values())

    subs = (Substitution.objects
            .filter(date__gte=start, date__lte=end)
            .select_related('lesson', 'lesson__subject', 'lesson__lesson_time',
                            'lesson__group', 'lesson__teacher', 'teacher', 'room')
            .order_by('date', 'lesson__lesson_time__number'))
    if teacher is not None:
        subs = subs.filter(Q(teacher=teacher) | Q(lesson__teacher=teacher))

    cells = {}
    for sub in subs:
        lesson = sub.lesson
        original = lesson.teacher
        cell = {
            'lesson': lesson,
            'subject': lesson.subject,
            'subject_label': (lesson.subject.label if lesson.subject
                              else (lesson.raw_text or '—')),
            'group': lesson.group,
            'combined_groups': [],
            'type': '',
            'teacher': sub.effective_teacher,
            'replaced_teacher': (original if original.pk != sub.teacher_id
                                 else None),
            'room': sub.effective_room,
            'changed': True,
            'note': lesson.note,
            'raw_text': lesson.raw_text,
            'spans': 1,
            'auto_join': True,
            'extend_of_id': None,
        }
        cells.setdefault(lesson.lesson_time_id, {})[sub.date.isoweekday()] = [cell]

    lesson_times = list(LessonTime.objects.order_by('number'))

    rows = []
    for day in days:
        num = day['num']
        row_cells = {lt.pk: cells.get(lt.pk, {}).get(num, [])
                     for lt in lesson_times}
        rows.append({
            'num': num,
            'name': day['name'],
            'short': day['short'],
            'color': day['color'],
            'date': dates[num],
            'date_label': f'{dates[num]:%d.%m.%Y}',
            'is_today': dates[num] == timezone_today(),
            'cells': row_cells,
            'columns': None,
            'total': sum(len(v) for v in row_cells.values()),
        })

    return {
        'rows': rows,
        'lesson_times': lesson_times,
        'filled_times': [lt.pk for lt in lesson_times
                         if any(row['cells'][lt.pk] for row in rows)],
        'subjects': [],
        'has_cards': False,
        'cards_mode': False,
        'total': sum(row['total'] for row in rows),
    }


def build_grid(*, queryset, week_monday, request=None, cards=False,
               merge_runs=False):
    """Собирает сетку «дни в строках, пары в колонках».

    Возвращает dict: rows -> [{day, date, cells: {lesson_time_pk: [...]}}],
    lesson_times -> список пар, subjects -> легенда.

    cards=True просит альтернативную раскладку для фильтра «Пары»: пара там
    выбрана одна, и колонок с занятиями тоже одна, поэтому таблица в семь
    пустых колонок — это просто место впустую. В этом режиме шаблон рисует
    занятия сеткой карточек на всю ширину.
    """
    from .models import Substitution

    parity = _parity(week_monday)
    days = build_days(request)
    dates = {d['num']: _shift_day(week_monday, d['num']) for d in days}

    # Принимается и список, а не только выборка: предпросмотр черновиков
    # отдаёт готовые объекты, включая ещё не сохранённые.
    if hasattr(queryset, 'select_related'):
        lessons = list(
            queryset.select_related(
                'subject', 'lesson_type', 'teacher', 'room', 'lesson_time',
                'group',
            )
        )
    else:
        lessons = list(queryset)

    subs = {}
    if lessons:
        window_start = min(dates.values())
        window_end = max(dates.values())
        for sub in Substitution.objects.filter(
            lesson_id__in=[lesson.pk for lesson in lessons],
            date__gte=window_start, date__lte=window_end,
        ).select_related('teacher', 'room'):
            subs[(sub.lesson_id, sub.date.isoweekday())] = sub

    cells = {}
    for lesson in lessons:
        sub = subs.get((lesson.pk, lesson.day))
        # замена важнее чётности: если на эту дату есть замена — показываем
        if lesson.week_parity not in (0, parity) and sub is None:
            continue
        cell = {
            'lesson': lesson,
            'subject': lesson.subject,
            'subject_label': lesson.subject.label if lesson.subject else (lesson.raw_text or '—'),
            'group': lesson.group,
            'type': lesson.lesson_type.name if lesson.lesson_type else '',
            'teacher': sub.effective_teacher if sub else lesson.teacher,
            'room': sub.effective_room if sub else lesson.room,
            'changed': bool(sub),
            # причину замены в публичный контекст не кладём: это личная
            # информация преподавателя (болезнь, семейные обстоятельства),
            # в расписании она не нужна. В шаблоне остаётся только метка
            # «замена». Сама причина живёт в модели и админке.
            'note': lesson.note,
            'raw_text': lesson.raw_text,
            # Метки предпросмотра черновиков. У настоящих записей их нет,
            # getattr вернёт False, и разметка останется обычной.
            'draft_new': getattr(lesson, '_draft_new', False),
            'draft_changed': getattr(lesson, '_draft_changed', False),
            'spans': lesson.spans or 1,
            'auto_join': lesson.auto_join,
            'extend_of_id': lesson.extend_of_id,
        }
        cells.setdefault(lesson.lesson_time_id, {}).setdefault(lesson.day, []).append(cell)

    for slots in cells.values():
        for day_num, items in slots.items():
            slots[day_num] = merge_combined(items)

    lesson_times = list(LessonTime.objects.order_by('number'))

    rows = []
    for day in days:
        row_cells = {}
        for lesson_time in lesson_times:
            items = cells.get(lesson_time.pk, {}).get(day['num'], [])
            row_cells[lesson_time.pk] = items
        rows.append({
            'num': day['num'],
            'name': day['name'],
            'short': day['short'],
            'color': day['color'],
            'date': dates[day['num']],
            'date_label': f'{dates[day["num"]]:%d.%m.%Y}',
            'is_today': dates[day['num']] == timezone_today(),
            'cells': row_cells,
            # Разбивка по колонкам для отрисовки. Обычная — одна колонка на
            # пару; в окне кабинета соседние пары одной лекции собираются в
            # одну ячейку с colspan.
            'columns': (_slot_columns(row_cells, lesson_times,
                                      merge_runs=merge_runs)
                        if merge_runs or _has_stretched(row_cells)
                        else None),
            'total': sum(len(v) for v in row_cells.values()),
        })

    subjects = [
        {'label': s.label, 'color': s.color}
        for s in Subject.objects.order_by('name')
    ]

    # Сколько колонок-пар реально непустых.
    # Для фильтра «Пары» это всегда одна: карточки этой единственной пары
    # в таблице выстраивались вертикальным столбиком в узкой ячейке.
    # Режим карточек включаем только когда пара выбрана явно (cards=True) —
    # иначе у группы с одной парой в неделю пропал бы привычный вид таблицы.
    filled = [lt.pk for lt in lesson_times
              if any(row['cells'][lt.pk] for row in rows)]
    # У выбранной пары занятий может не оказаться (нечётная неделя, пара пустая).
    # Показывать карточки не о чем — оставляем обычную таблицу с шапкой пар,
    # иначе на пустой паре исчезла бы вся сетка.
    cards_mode = cards and len(filled) == 1

    return {
        'rows': rows,
        'lesson_times': lesson_times,
        'filled_times': filled,
        'cards_mode': cards_mode,
        'has_cards': bool(filled),
        'card_time': filled[0] if cards_mode else None,
        'subjects': subjects,
        'is_empty': not any(lesson.pk for lesson in lessons) if not lessons else False,
    }


def timezone_today():
    from django.utils import timezone

    return timezone.localdate()


def grid_has_content(grid):
    return any(row['cells'] and any(cells for cells in row['cells'].values())
               for row in grid['rows'])