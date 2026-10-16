"""Растяжение пары на две-три пары.

Вуз ведёт расписание по парам, поэтому лекция на три часа в базе — это три
записи подряд, а не одна длинная. Раньше склеивать их умел только просмотр
кабинета, и то по совпадению предмета с преподавателем: растянуть
конкретную пару вручную было нечем.

Здесь — кнопка. Сотрудница нажимает «Растянуть на 3», и хвосты создаются
сами: интервалы берутся из таблицы пар, чётность копируется, группа и
преподаватель те же. Цифру в поле вводить не надо — именно потому, что
вписанная вручную тройка в пятой паре тихо ломает сетку.

Любая невозможность — занятое место, конец таблицы, длиннее таблицы —
возвращается отказом с объяснением, а не приводит к наложению карточек.
"""

from django.db import transaction

from .models import Lesson

MAX_SPANS = 4


class CannotStretch(Exception):
    """Растянуть нельзя. Текст уходит сотруднице дословно."""


def _slot_slots():
    from .models import LessonTime
    return list(LessonTime.objects.order_by('number'))


def _busy_in_room(lesson, slots, room_id):
    """Занятые места в кабинете: нельзя встать в чужое занятие."""
    if room_id is None:
        return []
    return list(Lesson.objects
                .filter(room_id=room_id, day=lesson.day,
                        week_parity=lesson.week_parity,
                        lesson_time__in=slots)
                .exclude(pk=lesson.pk)
                .select_related('lesson_time'))


def plan_stretch(lesson, spans):
    """Проверяет, можно ли растянуть, и на какие пары.

    Возвращает список занятий-целей. Ничего не меняет: сотрудница видит
    отказ до того, как что-то сохранится.
    """
    if spans < 1:
        raise CannotStretch('Длина задаётся числом пар: 1, 2 или 3.')
    if spans > MAX_SPANS:
        raise CannotStretch(f'Больше {MAX_SPANS} пар растянуть нельзя.')

    slots = _slot_slots()
    index = next((i for i, s in enumerate(slots)
                  if s.pk == lesson.lesson_time_id), None)
    if index is None:
        raise CannotStretch('У занятия неизвестная пара.')

    if spans == 1:
        return []

    if index + spans > len(slots):
        raise CannotStretch(
            f'{lesson.lesson_time.number}-я пара — последняя в таблице, '
            f'дальше растягивать некуда. Всего пар: {len(slots)}.')

    targets = slots[index + 1: index + spans]

    # Свои хвосты не считаем занятостью: повторное нажатие «растянуть» на
    # ту же длину должно быть разрешено.
    own = set(lesson.extensions.values_list('pk', flat=True))
    busy = [l for l in _busy_in_room(lesson, targets, lesson.room_id)
            if l.pk not in own]
    if busy:
        names = ', '.join(
            f'{l.lesson_time.number}-я пара'
            + (f' ({l.group.name})' if l.group_id else '')
            for l in busy)
        raise CannotStretch(
            'Растянуть некуда: место уже занято — '
            + names + '. Сначала разберитесь с теми занятиями.')

    return targets


@transaction.atomic
def stretch(lesson, spans):
    """Создаёт хвосты и ставит корню длину. Возвращает созданные занятия.

    Идемпотентно: повторный вызов с той же длиной не плодит копии, а
    прибавляет недостающие.
    """
    targets = plan_stretch(lesson, spans)
    lesson.spans = max(spans, lesson.spans or 1)
    lesson.save(update_fields=['spans'])

    existing = {l.lesson_time_id: l for l in lesson.extensions.all()}
    created = []
    for slot in targets:
        found = existing.get(slot.pk)
        if found is not None:
            found.spans = 1
            found.extend_of = lesson
            found.save(update_fields=['spans', 'extend_of'])
            created.append(found)
            continue
        copy = Lesson.objects.create(
            group=lesson.group,
            subject=lesson.subject,
            lesson_type=lesson.lesson_type,
            teacher=lesson.teacher,
            room=lesson.room,
            day=lesson.day,
            lesson_time=slot,
            week_parity=lesson.week_parity,
            note=lesson.note,
            extend_of=lesson,
        )
        created.append(copy)
    return created


@transaction.atomic
def collapse(lesson):
    """Убирает хвосты, оставляя только исходное занятие."""
    removed = list(lesson.extensions.all())
    if removed:
        Lesson.objects.filter(pk__in=[l.pk for l in removed]).delete()
    if lesson.spans != 1:
        lesson.spans = 1
        lesson.save(update_fields=['spans'])
    return removed


def changed_extensions(lesson):
    """Хвосты, которые правили отдельно от корня.

    Если такой есть, сворачивать нельзя: сотруд работала с хвостом как с
    самостоятельным занятием, и удаление унесёт её работу. Корни растяжения
    одинаковы по построению, поэтому любое расхождение — это правка.
    """
    return [tail for tail in lesson.extensions.all() if
            (tail.subject_id, tail.teacher_id, tail.group_id, tail.room_id,
             tail.day, tail.week_parity, tail.note)
            != (lesson.subject_id, lesson.teacher_id, lesson.group_id,
                lesson.room_id, lesson.day, lesson.week_parity, lesson.note)]
