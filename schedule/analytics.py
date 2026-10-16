"""Сводка по парам — для деканата и администрации.

Публичный сайт отвечает на вопрос «когда у меня занятия». Этот отвечает на
вопрос «как устроено расписание в целом»: сколько всего занятий, как они
распределены по парам и дням, где перекос и чего не хватает в данных.

Счёт ведётся по чётности текущей недели — так же, как показывает сайт.
Поэтому в нечётную неделю сводка описывает расписание, которое сейчас видно
на главной, а не всё сразу.

Страница служебная: доступна только сотрудникам с входом в админку.
"""

from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, F, Q
from django.shortcuts import render
from django.utils import timezone

from .grid import _parity, monday_of
from .models import Group, Lesson, LessonTime, Room, Teacher


def _cells(lessons):
    """Убирает объединённые группы: одна лекция на несколько групп — одна ячейка.

    Без этого объединённая пара посчиталась бы двумя занятиями, и нагрузка
    на слот выглядела бы выше настоящей.
    """
    unique = {}
    for lesson in lessons:
        key = (lesson['day'], lesson['lesson_time_id'],
               lesson['course'], lesson['teacher_id'], lesson['room_id'])
        unique.setdefault(key, 0)
        unique[key] += 1
    return list(unique)


def build_report(week_monday=None):
    """Считает сводку за неделю. week_monday=None — текущая."""
    if week_monday is None:
        week_monday = monday_of(timezone.localdate())
    parity = _parity(week_monday)

    lessons = list(
        Lesson.objects.filter(Q(week_parity=0) | Q(week_parity=parity))
        .values('day', 'lesson_time_id', 'teacher_id', 'room_id',
                'lesson_type_id', course=F('group__course'))
    )
    cells = _cells(lessons)

    times = list(LessonTime.objects.order_by('number'))
    day_nums = [num for num, _ in settings.WEEKDAYS]
    day_titles = [name for _, name in settings.WEEKDAYS]

    by_slot = defaultdict(lambda: defaultdict(int))
    for day, slot_id, _course, _teacher, _room in cells:
        by_slot[slot_id][day] += 1

    rows = []
    for lt in times:
        per_day = [by_slot[lt.pk].get(num, 0) for num in day_nums]
        rows.append({'lt': lt, 'per_day': per_day, 'total': sum(per_day)})

    slot_totals = [row['total'] for row in rows]
    # Пустая пара в разборе полезнее забитой: по ней видно свободное окно,
    # поэтому «самая пустая» считается по всем парам, включая нулевые.
    busiest = max(rows, key=lambda row: row['total']) if rows else None
    quietest = min(rows, key=lambda row: row['total']) if rows else None

    teacher_load = defaultdict(int)
    course_load = defaultdict(int)
    for _day, _slot, course, teacher_id, _room in cells:
        if teacher_id:
            teacher_load[teacher_id] += 1
        course_load[course] += 1

    top_ids = sorted(teacher_load.items(), key=lambda kv: -kv[1])[:10]
    names = dict(
        Teacher.objects.filter(pk__in=[pk for pk, _ in top_ids])
        .values_list('pk', 'full_name')
    )

    return {
        'week_monday': week_monday,
        'week_label': f'{week_monday:%d.%m} – '
                      f'{week_monday + timedelta(days=5):%d.%m.%Y}',
        'parity': parity,
        'parity_word': 'нечётная' if parity == 1 else 'чётная',
        'day_nums': day_nums,
        'day_titles': day_titles,
        'rows': rows,
        'totals_per_day': [
            sum(row['per_day'][i] for row in rows) for i in range(len(day_nums))
        ],
        'grand_total': sum(slot_totals),
        'busiest': busiest,
        'quietest': quietest,
        'spread': (max(slot_totals) - min(slot_totals)) if slot_totals else 0,
        'no_room': sum(1 for cell in cells if cell[4] is None),
        'no_type': sum(1 for lesson in lessons if not lesson['lesson_type_id']),
        'groups_without_room': [
            g.name for g in Group.objects.annotate(
                n=Count('lessons', filter=Q(lessons__room__isnull=True))
            ).filter(n__gt=0).order_by('-n')[:10]
        ],
        'top_teachers': [
            {'name': names.get(pk, '—'), 'count': count} for pk, count in top_ids
        ],
        'course_load': sorted(course_load.items()),
        'group_count': Group.objects.count(),
        'room_count': Room.objects.count(),
        'teacher_count': Teacher.objects.filter(active=True).count(),
    }


def analytics_view(request):
    return render(request, 'admin/schedule/analytics.html', build_report())