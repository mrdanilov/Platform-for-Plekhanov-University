"""Выгрузка расписания в CSV и палитра подложек занятий.

Модуль выделен из бывшего ``importer.py``. Импорт расписания удалён:
сотрудницы заполняют базу вручную через админку, а импорт оказался не
только не нужен, но и опасен — кнопка «Проверить файл» писала данные в
базу, пока показывала «будет создано» (см. audit_todos/01).

Здесь осталось то, что нужно и без импорта:

* ``export_csv`` — выгрузка кнопкой «Excel»;
* ``SUBJECT_COLORS`` и ``_pick_color`` — палитра предметов: её берут
  виджет в админке и команда ``set_subject_palette``.

Функция выгрузки перенесена как есть: разделитель ``;``, набор колонок и
порядок сортировки — это формат файла, который сотрудницы уже видели.
Менять их вместе с переносом значило бы тихо поменять выгрузку.

Импортные функции сюда не переносились: держать неиспользуемый код
опаснее, чем его удалить — он ломается сам и проверяется тестами,
которые проверяют мёртвый код. Шаблон CSV удалён вместе с разделом
«Импорт», а разбор ячеек аудитории и номера группы — вместе с ним.
"""

import csv
import io

from .formula_guard import escape_formula
from .models import Lesson

# Палитра подложек занятий. Раньше здесь были цвета Material Design 50
# (светлота ~0.88) — на белой таблице такие цвета выглядели как выцветшая
# бумага, и два соседних предмета было не отличить. Теперь светлота
# ~0.67: блок заметен, но текст внутри остаётся контрастным
# (11–14:1 на тёмном --text, проверено расчётом).
SUBJECT_COLORS = [
    '#b8d4f2', '#bfe3bc', '#ffe08a', '#d6c4ee', '#ffc79a',
    '#9fded4', '#f7c2d4', '#dcea9e',
]


def _pick_color(subject):
    """Цвет подложки для названия предмета.

    Детерминированно: одинаковые названия получают одинаковый цвет,
    поэтому перезапуск ничего не меняет. Порядок палитры тот же, что был
    при импорте, — иначе цвета у всех предметов поменялись бы разом.
    """
    import hashlib

    digest = int(hashlib.md5((subject or '').strip().lower().encode(
        'utf-8')).hexdigest(), 16)
    return SUBJECT_COLORS[digest % len(SUBJECT_COLORS)]


def export_csv(queryset=None):
    """Выгрузка текущего расписания в CSV (для бэкапа и сверки).

    Значения проходят через `escape_formula`: иначе предмет с названием
    «=HYPERLINK(...)» открылся бы в Excel как формула. Приём безвреден
    и для повторного использования файла — апостроф ставится только
    по-настоящему опасным значениям.
    """
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
        writer.writerow([escape_formula(value) for value in [
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
        ]])
    return buffer.getvalue()