"""Черновики правок: предпросмотр и публикация.

Сотрудница правит расписание, но сайт до публикации не меняется. Здесь две
операции, и обе должны считать расписание одинаково — иначе сотрудница
увидит одно, а после публикации получит другое.

Поэтому правило применения черновика описано ровно один раз, в
``_apply_to``:

* ``preview_lessons`` — применяет правило к копиям объектов в памяти и
  отдаёт список для сетки. База не трогается;
* ``publish`` — применяет то же правило к настоящим записям в одной
  транзакции и помечает черновики опубликованными.

Любая ошибка посреди публикации откатывает всё: половина применённых
правок хуже, чем ни одной.
"""

import json

from django.db import transaction

from .models import DraftPublication, Lesson, LessonDraft

# Что черновик может перенести в занятие.
#
# week_parity намеренно не переносится: правка недели — редкость, и её
# проще сделать прямо в опубликованном расписании.
#
# note занятия сюда НЕ входит. Заметка черновика — это объяснение
# сотрудницы, и раньше она перетирала заметку самого занятия: правка дня
# молча стирала то, что преподаватель написал о своей паре. Пояснение
# хранится в журнале публикаций.
RELATION_FIELDS = ('group', 'subject', 'lesson_type', 'teacher', 'room',
                   'lesson_time')
PLAIN_FIELDS = ('day',)
DRAFT_FIELDS = RELATION_FIELDS + PLAIN_FIELDS

# Поля, которые можно осознанно очистить галочкой. Без них пустое поле
# пришлось бы читать как «удали», и правка одного дня молча сносила бы
# аудиторию или преподавателя у всех занятий подряд.
CLEARABLE = (('room', 'clear_room'),
             ('teacher', 'clear_teacher'),
             ('lesson_type', 'clear_lesson_type'))


def pending_drafts():
    """Неопубликованные черновики — их и показывают, их и публикуют."""
    return (LessonDraft.objects
            .filter(published_at__isnull=True)
            .select_related('lesson', 'lesson__subject', 'lesson__teacher',
                            'group', 'subject', 'lesson_type', 'teacher',
                            'room', 'lesson_time', 'author')
            .order_by('created_at'))


def _value(lesson, field):
    """Значение поля занятия в виде, одинаковом у черновика."""
    if field in RELATION_FIELDS:
        return getattr(lesson, field + '_id')
    return getattr(lesson, field)


def _snapshot(lesson):
    """Снимок занятия для журнала. None, если занятия уже нет.

    week_parity берётся тоже: без него «было» не восстановить полностью,
    а вернуть занятие с неверной чётностью хуже, чем не вернуть вовсе.
    """
    if lesson is None:
        return {}
    data = {field: _value(lesson, field) for field in DRAFT_FIELDS}
    data['week_parity'] = lesson.week_parity
    # Заметка самого занятия хранится отдельно от пояснения сотрудницы и
    # при возврате отменённого занятия обязана восстановиться: иначе
    # вернули бы занятие, но без слов преподавателя.
    data['lesson_note'] = lesson.note
    return data


def _changed_fields(lesson, draft):
    """Поля правки: только те, что сотрудница действительно поменяла.

    Пустое поле черновика означает «не трогать», а не «стереть». Без этого
    правила достаточно было открыть правку и нажать «Сохранить», чтобы
    потерять у занятия преподавателя, аудиторию и тип — сотрудница правила
    не знает, а форма обязана быть безопасной по умолчанию.

    Осознанное удаление значения — отдельная галочка (CLEARABLE).
    """
    changes = {}
    for field in DRAFT_FIELDS:
        new = _value(draft, field)
        if field in RELATION_FIELDS:
            old = getattr(lesson, field + '_id')
        else:
            old = getattr(lesson, field)
        if new and new != old:
            changes[field] = new
        elif not new and old:
            pass  # оставляем как есть — пустое не значит «стереть»
    for field, flag in CLEARABLE:
        if getattr(draft, flag) and _value(lesson, field):
            changes[field] = None
    return changes


def _apply_changes(lesson, changes):
    """Применяет только перечисленные поля."""
    for field, value in changes.items():
        if field in RELATION_FIELDS:
            setattr(lesson, field + '_id', value)
        else:
            setattr(lesson, field, value)


def _apply_to(lesson, draft):
    """Переносит правку в объект занятия. Не сохраняет в базу.

    Возвращает словарь применённых полей. Используется и предпросмотром,
    и публикацией, поэтому правило одно.
    """
    if draft.action == 'create':
        changes = {field: _value(draft, field)
                   for field in DRAFT_FIELDS if _value(draft, field)}
    else:
        changes = _changed_fields(lesson, draft)
    _apply_changes(lesson, changes)
    return changes


def _new_lesson(draft):
    """Черновик «новое занятие» как объект Lesson — ещё не сохранённый."""
    lesson = Lesson()
    _apply_to(lesson, draft)
    lesson.week_parity = 0
    lesson._draft_new = True
    return lesson


def build_draft_grid(week_monday, request=None, drafts=None):
    """Сетка «как будет»: опубликованное расписание плюс черновики.

    Тот же построитель, что и у сайта, — иначе сотрудница увидит одно, а
    после публикации получит другое. Отличие одно: новые занятия помечены,
    изменённые тоже, а отменённых просто нет.
    """
    from .grid import build_grid
    from .models import Lesson as LessonModel

    if drafts is None:
        drafts = list(pending_drafts())

    lessons = preview_lessons(LessonModel.objects.all(), drafts)
    grid = build_grid(queryset=lessons, week_monday=week_monday,
                      request=request, merge_runs=True)
    grid['dropped'] = sum(1 for d in drafts if d.action == 'delete')
    return grid


def preview_lessons(queryset, drafts=None):
    """Список занятий с учётом черновиков — для предпросмотра.

    База не меняется: существующие занятия копируются, отменённые убираются,
    новые добавляются как несохранённые объекты. Так видно ровно то, что
    будет на сайте после публикации.
    """
    if drafts is None:
        drafts = list(pending_drafts())

    lessons = [copy_lesson(l) for l in _as_list(queryset)]
    dropped = set()
    added = []
    for draft in drafts:
        if draft.action == 'create':
            added.append(_new_lesson(draft))
            continue
        if draft.lesson_id is None:
            continue
        if draft.action == 'delete':
            dropped.add(draft.lesson_id)
            continue
        for lesson in lessons:
            if lesson.pk == draft.lesson_id:
                _apply_to(lesson, draft)
                lesson._draft_changed = True
    return [l for l in lessons if l.pk not in dropped] + added


def _as_list(queryset):
    if hasattr(queryset, 'select_related'):
        return list(queryset.select_related(
            'subject', 'lesson_type', 'teacher', 'room', 'lesson_time', 'group',
        ))
    return list(queryset)


def copy_lesson(lesson):
    """Копия занятия для предпросмотра — база не трогается."""
    clone = Lesson()
    clone.pk = lesson.pk
    for field in DRAFT_FIELDS + ('week_parity', 'raw_text'):
        setattr(clone, field, getattr(lesson, field))
    return clone


@transaction.atomic
def publish(drafts, user=None):
    """Публикует черновики: переносит правки в расписание.

    Всё или ничего: если хоть одна правка не проходит, не проходит ни одна,
    а черновики остаются неопубликованными — их можно поправить и
    попробовать снова.

    Каждая публикация пишется в журнал со снимком «было» и «стало».
    Корректировок много, и через месяц по одной этой странице видно, что
    вообще происходило с расписанием и кто это делал.
    """
    done = []
    for draft in drafts:
        if not draft.pending:
            continue
        # Черновик помечается опубликованным ДО применения. Иначе при
        # отмене занятия не удастся сохранить метку: удаление занятия тянет
        # за собой и черновик, который к нему привязан.
        draft.published_at = _now()
        draft.published_by = user
        draft.save(update_fields=['published_at', 'published_by'])

        lesson = None
        before = {}
        after = {}
        if draft.action == 'create':
            lesson = _new_lesson(draft)
            before = {}
            lesson.save()
            after = _snapshot(lesson)
        else:
            lesson = Lesson.objects.select_for_update().filter(
                pk=draft.lesson_id).first()
            if lesson is None:
                # Занятие успели удалить мимо черновика. Молча пропустить
                # нельзя: сотрудница решит, что правка опубликована.
                raise LessonDraft.DoesNotExist(
                    'Занятие, которое меняет черновик, уже удалено.')
            before = _snapshot(lesson)
            if draft.action == 'delete':
                lesson.delete()
                after = {}
            else:
                _apply_to(lesson, draft)
                lesson.save()
                after = _snapshot(lesson)

        DraftPublication.objects.create(
            # При отмене занятия исчезает и сам черновик: он привязан к
            # занятию каскадом. Ссылаться на удалённую строку нельзя, и
            # запись в журнале не создалась бы вовсе — а именно она и
            # нужна, чтобы потом вернуть занятие руками. Поэтому при
            # отмене черновик в журнале не указывается: остаются действие,
            # пояснение и полный снимок «было».
            draft=(None if draft.action == 'delete' else draft),
            action=draft.action,
            lesson=(None if draft.action == 'delete'
                    else (lesson if lesson is not None else None)),
            before=json.dumps(before, ensure_ascii=False, default=str),
            after=json.dumps(after, ensure_ascii=False, default=str),
            note=draft.note,
            author=user,
        )
        done.append(draft)
    return done


def _now():
    from django.utils import timezone
    return timezone.now()


class CannotUndo(Exception):
    """Возврат отменён: объяснение уходит сотруднице дословно."""


def _later_publications(publication):
    """Публикации после этой, которые ещё не возвращены."""
    return (DraftPublication.objects
            .filter(published_at__gt=publication.published_at,
                    undone_at__isnull=True)
            .order_by('published_at'))


def _assert_can_undo(publication, later):
    """Проверяет, что возврат не наступит на чужую работу.

    Возвращать можно только последнюю правку по занятию. Если после неё уже
    что-то публиковали, состояние «было» перестало быть правдой: вернуть
    его — значит затереть более позднее решение. Об этом честнее сказать,
    чем молча испортить расписание.
    """
    if publication.undone_at is not None:
        raise CannotUndo('Эта публикация уже возвращена.')
    if publication.action in ('update', 'create') and publication.lesson_id:
        clash = [p for p in later if p.lesson_id == publication.lesson_id]
        if clash:
            raise CannotUndo(
                'После этой правки опубликована ещё одна для того же '
                f"занятия ({clash[0].published_at:%d.%m.%Y %H:%M}). "
                'Сначала верните её, потом эту.')
    if publication.action == 'create':
        if not Lesson.objects.filter(pk=publication.lesson_id).exists():
            raise CannotUndo(
                'Занятия, созданного этой правкой, уже нет — '
                'возвращать нечего.')
    if publication.action == 'delete':
        before = json.loads(publication.before or '{}')
        taken = Lesson.objects.filter(
            group_id=before.get('group'), subject_id=before.get('subject'),
            day=before.get('day'), lesson_time_id=before.get('lesson_time'),
            week_parity=before.get('week_parity', 0),
        ).exists()
        if taken:
            raise CannotUndo(
                'На это место уже поставили другое занятие. '
                'Сначала уберите его, потом возвращайте отменённое.')


def restore(publication, user=None):
    """Возвращает расписание к состоянию «было» из журнала.

    Обратный ход к конкретной публикации, а не откат всего: журнал и нужен
    затем, чтобы можно было вернуть именно ту правку, которая мешает.
    """
    with transaction.atomic():
        publication.refresh_from_db()
        _assert_can_undo(publication, list(_later_publications(publication)))
        before = json.loads(publication.before or '{}')

        if publication.action == 'update':
            lesson = Lesson.objects.select_for_update().filter(
                pk=publication.lesson_id).first()
            if lesson is None:
                raise CannotUndo('Занятия больше нет — возвращать нечего.')
            _apply_snapshot(lesson, before)
            lesson.save()
        elif publication.action == 'create':
            Lesson.objects.filter(pk=publication.lesson_id).delete()
        elif publication.action == 'delete':
            Lesson.objects.create(**_snapshot_kwargs(before))

        publication.undone_at = _now()
        publication.undone_by = user
        publication.save(update_fields=['undone_at', 'undone_by'])
    return publication


def _apply_snapshot(lesson, data):
    """Кладёт значения из снимка в занятие."""
    for field in DRAFT_FIELDS:
        value = data.get(field)
        if field in RELATION_FIELDS:
            setattr(lesson, field + '_id', value)
        else:
            setattr(lesson, field, value)
    if 'week_parity' in data:
        lesson.week_parity = data['week_parity']
    if 'lesson_note' in data:
        lesson.note = data['lesson_note']


def _snapshot_kwargs(data):
    """Снимок -> аргументы create(). Всё, чего нет, остаётся пустым."""
    kwargs = {}
    for field in DRAFT_FIELDS:
        value = data.get(field)
        kwargs[field + '_id' if field in RELATION_FIELDS else field] = value
    kwargs['week_parity'] = data.get('week_parity', 0)
    kwargs['note'] = data.get('lesson_note', '')
    return kwargs
