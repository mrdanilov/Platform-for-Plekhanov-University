from datetime import date, timedelta

from django.conf import settings

from .models import LessonTime, Subject

VIEWS = ('group', 'pair', 'teacher', 'room')


def monday_of(d):
    return d - timedelta(days=d.weekday())


def iso_week(d):
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
    return iso_week(week_monday)


def _parity(week_monday):
    return 1 if week_monday.isocalendar()[1] % 2 else 2


def _shift_day(monday, day_number):
    return monday + timedelta(days=day_number - 1)


def build_days(request=None):
    """Строки таблицы: дни недели в порядке файла (Пн..Сб), локализованные."""
    from .i18n import translate

    days = []
    for num, _name in settings.WEEKDAYS:
        if request is None:
            name = dict(settings.WEEKDAYS)[num]
            short = name[:2]
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


def build_grid(*, queryset, week_monday, request=None, cards=False):
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

    lessons = list(
        queryset.select_related(
            'subject', 'lesson_type', 'teacher', 'room', 'lesson_time', 'group',
        )
    )

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
            'reason': sub.reason if sub else '',
            'note': lesson.note,
            'raw_text': lesson.raw_text,
        }
        cells.setdefault(lesson.lesson_time_id, {}).setdefault(lesson.day, []).append(cell)

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