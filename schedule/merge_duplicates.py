"""Слияние дублей справочников и заполнение нормализованных ключей.

Зачем это отдельный модуль, а не код внутри миграции. Слияние удаляет
объекты, на которые ссылаются занятия, — отменить такое из миграции нельзя,
единственный путь назад это бэкап. Поэтому перед включением уникальности
миграция обязана проверить, что данных для слияния не осталось, а сама
проверка нужна и обычной management-команде, чтобы можно было посмотреть
состояние до включения ограничений.

Что считается дублем. Два объекта — один и тот же, если их нормализованные
ключи совпали (``schedule/normkeys.py``): отличаются только регистр, буква
«ё», лишние пробелы. Каноническим остаётся объект с наименьшим ``pk`` —
то есть созданный раньше, обычно из самого первого импорта.

Что происходит с данными дубля. Ссылки перевешиваются на канонический
объект, а пустые поля канонического дополняются полями дубля. Так
«Сидорова», у которой был заполнен телефон, не теряет его из-за того, что
канонической оказалась та же «Сидорова» без телефона. Значения, где у обеих
сторон есть непустое и разное значение, оставляем у канонического — это
осознанно: разные телефоны одного человека невозможно различить
автоматически, и «последний импортированный» здесь не лучше «первого».
"""

from collections import defaultdict

from django.db import transaction

from .models import Group, LessonType, Room, Subject, Teacher
from .normkeys import norm_key

# Модель -> (поля, по которым ищется канонический объект; поля, которые
# дополняются у канонического, если у него они пустые).
MERGE_PLAN = {
    'teacher': {
        'model': Teacher,
        'key': ('norm_name',),
        'fill': ('position', 'department', 'phone', 'email'),
        'label': lambda obj: obj.full_name,
    },
    'subject': {
        'model': Subject,
        'key': ('norm_name',),
        'fill': ('short_name', 'color'),
        'label': lambda obj: obj.name,
    },
    'lessontype': {
        'model': LessonType,
        'key': ('norm_name',),
        'fill': (),
        'label': lambda obj: obj.name,
    },
    'room': {
        'model': Room,
        'key': ('norm_building', 'norm_name'),
        'fill': ('capacity',),
        'label': lambda obj: str(obj),
    },
    'group': {
        'model': Group,
        'key': ('norm_name', 'course'),
        'fill': ('specialty',),
        'label': lambda obj: obj.name,
    },
}


def _plan(apps=None):
    """План слияния на настоящих или на исторических моделях.

    Миграция 0007 запускает слияние на том состоянии схемы, которое было
    несколько миграций назад. Если бы она брала живые модели, то любое
    поле, добавленное позже, попало бы в SELECT и запрос упал бы с «no such
    column» — ровно так и случилось с цветом типа занятия. Поэтому всё, что
    нужно от моделей, передаётся явно.
    """
    if apps is None:
        return MERGE_PLAN

    plan = {}
    for name, source in MERGE_PLAN.items():
        # Имя исторической модели берём у настоящей: «Lessontype» из ключа
        # плана не совпадает с настоящим именем «LessonType».
        meta = source['model']._meta
        model = apps.get_model(meta.app_label, meta.model_name)
        plan[name] = {
            **source,
            'model': model,
            'fill': tuple(field for field in source['fill']
                          if any(f.name == field for f in model._meta.fields)),
        }
    return plan


def _norm_field_names(model):
    """Поля norm_* модели.

    У исторической модели нет метода ``norm_field_names``: он живёт в
    миксине, которого на том состоянии схемы ещё не было.
    """
    getter = getattr(model, 'norm_field_names', None)
    if getter is not None:
        return tuple(getter())
    return tuple(f.name for f in model._meta.fields if f.name.startswith('norm_'))


def _fill_norm(obj, model):
    """Проставить norm_* на объекте.

    Историческая модель не умеет ``fill_norm``, но правило одно и то же у
    всех пяти справочников: ``norm_«поле»`` — это нормализованное значение
    одноимённого поля. Поэтому ключи считаются здесь.
    """
    filler = getattr(obj, 'fill_norm', None)
    if filler is not None:
        filler()
        return
    for field in _norm_field_names(model):
        source = field[len('norm_'):]
        if hasattr(obj, source):
            setattr(obj, field, norm_key(getattr(obj, source) or ''))


def fill_normalized_keys(dry_run=False, apps=None):
    """Проставляет norm_* во всех справочниках.

    Отдельный шаг, а не побочный эффект ``save()``: сюда попадают в том числе
    строки, созданные в обход модели, иначе под ограничение попал бы объект
    с пустым ключом и следующий импорт молча слил бы его с первым же
    пустым ключом.
    """
    filled = 0
    for plan in _plan(apps).values():
        model = plan['model']
        for obj in model.objects.all():
            before = [getattr(obj, field) for field in _norm_field_names(model)]
            _fill_norm(obj, model)
            after = [getattr(obj, field) for field in _norm_field_names(model)]
            if before != after:
                filled += 1
                if not dry_run:
                    obj.save()
    return filled


def find_duplicate_groups(apps=None):
    """Находит дубли по нормализованному ключу: {имя: [pk, ...]}."""
    found = {}
    for name, plan in _plan(apps).items():
        buckets = defaultdict(list)
        for obj in plan['model'].objects.all():
            buckets[tuple(getattr(obj, field) for field in plan['key'])].append(obj.pk)
        dups = {k: v for k, v in buckets.items() if len(v) > 1}
        if dups:
            found[name] = {k: sorted(v) for k, v in sorted(dups.items())}
    return found


def _repoint(model, field, old_pk, new_pk):
    return model.objects.filter(**{f'{field}_id': old_pk}).update(**{field: new_pk})


def _delete(obj):
    """Удаляет дубль.

    Обычно это обычный ``delete()``: сборщик Django проверит PROTECT и не
    даст удалить предмет, на который кто-то ссылается, — такая проверка
    здесь и нужна, она страхует от забытой перевески.

    Но сборщик обходит все таблицы, связанные с моделью, включая созданные
    позже (черновики правок). При прокрутке истории миграций назад такой
    таблицы ещё нет, и удаление падает с «no such table». На старой схеме
    ссылок на модель и не существует, поэтому удаляем напрямую, минуя
    сборщик: на живой базе (все таблицы на месте) путь прежний.
    """
    from django.db import connection

    missing = [f.related_model._meta.db_table
               for f in obj._meta.related_objects
               if f.one_to_many or f.one_to_one]
    existing = set(connection.introspection.table_names())
    if any(table not in existing for table in missing):
        obj.__class__.objects.filter(pk=obj.pk)._raw_delete(obj._state.db)
    else:
        obj.delete()


def _build_references(apps=None):
    """Карта «справочник -> где на него ссылаются».

    Строится лениво, при первом вызове: на уровне модуля импорт Lesson и
    Substitution здесь лишний, а внутри функции цикла импорта не возникает.
    """
    from django.db import connection

    from .models import Lesson, LessonDraft, Substitution

    holders = {'Lesson': Lesson, 'LessonDraft': LessonDraft,
               'Substitution': Substitution}
    if apps is not None:
        # Модели, которых на этом шаге истории ещё нет, пропускаются: их
        # таблицы тоже нет, а ссылок на них в то время быть не могло.
        found = {}
        for name in holders:
            try:
                found[name] = apps.get_model('schedule', name)
            except LookupError:
                continue
        holders = found

    plan = {
        'teacher': ((holders.get('Lesson'), 'teacher'),
                    (holders.get('Substitution'), 'teacher'),
                    (holders.get('LessonDraft'), 'teacher')),
        'subject': ((holders.get('Lesson'), 'subject'),
                    (holders.get('LessonDraft'), 'subject')),
        'lessontype': ((holders.get('Lesson'), 'lesson_type'),
                       (holders.get('LessonDraft'), 'lesson_type')),
        'room': ((holders.get('Lesson'), 'room'),
                 (holders.get('Substitution'), 'room'),
                 (holders.get('LessonDraft'), 'room')),
        'group': ((holders.get('Lesson'), 'group'),
                  (holders.get('LessonDraft'), 'group')),
    }

    # Черновики правок появились позже этой миграции. При прокрутке истории
    # назад их таблицы ещё нет, и сборщик Django попытался бы её опросить и
    # упал на «no such table». Поэтому модель, чьей таблицы ещё нет,
    # отбрасывается: на той версии базы ссылок на неё и не существует.
    existing = set(connection.introspection.table_names())
    return {
        name: tuple((holder, field) for holder, field in holders
                    if holder is not None
                    and holder._meta.db_table in existing)
        for name, holders in plan.items()
    }


def merge_duplicates(dry_run=False, verbose=True, apps=None):
    """Сливает дубли, перевешивая ссылки на канонический объект.

    Возвращает отчёт для показа в интерфейсе и для записи в лог импорта.
    Пустой отчёт означает, что ограничения можно включать смело.
    """
    references = _build_references(apps)
    report = {}
    for name, plan in _plan(apps).items():
        model = plan['model']
        buckets = defaultdict(list)
        for obj in model.objects.all():
            buckets[tuple(getattr(obj, field) for field in plan['key'])].append(obj)

        merged = []
        for key, objects in buckets.items():
            if len(objects) < 2:
                continue
            objects.sort(key=lambda o: o.pk)
            keeper, *rest = objects
            lost = []
            with transaction.atomic():
                for duplicate in rest:
                    if not dry_run:
                        for holder, field in references[name]:
                            _repoint(holder, field, duplicate.pk, keeper.pk)
                        # пустые поля канонического дополняем полями дубля
                        for field in plan['fill']:
                            value = getattr(duplicate, field)
                            if value in (None, ''):
                                continue
                            if getattr(keeper, field) in (None, ''):
                                setattr(keeper, field, value)
                        if plan['fill']:
                            keeper.save()
                        _delete(duplicate)
                    lost.append(duplicate.pk)
            merged.append({
                'key': ' / '.join(str(part) for part in key),
                'kept_pk': keeper.pk,
                'kept': plan['label'](keeper),
                'merged_pks': lost,
            })
            if verbose:
                print(f'  {name}: «{plan["label"](keeper)}» '
                      f'— слито дублей: {lost}')
        if merged:
            report[name] = merged
    return report
