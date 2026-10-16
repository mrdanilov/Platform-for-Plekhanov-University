"""Поиск и слияние дублей справочников.

Осталось от `test_import_dedup.py` после удаления импорта: проверки
команды `merge_duplicates` и миграции 0007, которая сводила дубли,
накопленные до введения нормализованных ключей.

Сам импорт удалён — сотрудницы заполняют базу вручную. Команда нужна
всё равно: дубли появляются не только при загрузке, но и при ручном
вводе, когда сотрудница написала «Финансы» и «финансы» двумя строками.
Именно с таким случаем столкнулись при наполнении базы вручную.
"""

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase

from schedule.merge_duplicates import (
    MERGE_PLAN,
    _build_references,
    fill_normalized_keys,
    find_duplicate_groups,
    merge_duplicates,
)
from schedule.models import (
    Group,
    Lesson,
    LessonTime,
    LessonType,
    Room,
    Subject,
    Teacher,
)

class MergeDuplicatesTests(TestCase):
    """Проверки самого алгоритма слияния.

    Создать дубли обычным способом уже нельзя: ограничение уникальности
    работает. Поэтому здесь проверяется то, что работает без дублей —
    группировка, поиск и пустой отчёт. Собственно слияние на данных с
    дублями проверяет MergeMigrationTests, который откатывает базу на шаг
    назад и прогоняет миграцию по-настоящему.
    """

    def setUp(self):
        self.lesson_time = LessonTime.objects.create(
            number=1, start='08:30', end='09:50')
        self.subject = Subject.objects.create(name='Финансы')
        self.group = Group.objects.create(name='ЭК-01', course=1)

    def test_clean_database_has_no_duplicates(self):
        Teacher.objects.create(full_name='Фёдоров А.А.')
        Room.objects.create(name='201', building='А')
        self.assertEqual(find_duplicate_groups(), {})

    def test_merge_on_clean_database_changes_nothing(self):
        Teacher.objects.create(full_name='Фёдоров А.А.')
        self.assertEqual(merge_duplicates(verbose=False), {})
        self.assertEqual(Teacher.objects.count(), 1)

    def test_merge_plan_covers_every_constrained_model(self):
        """Каждая модель с ограничением обязана быть в плане слияния.

        Иначе миграция поставит constraint, не почистив эту таблицу,
        и упадёт на живой базе.
        """
        from schedule.merge_duplicates import MERGE_PLAN
        constrained = {
            model._meta.db_table
            for model in (Teacher, Subject, LessonType, Room, Group)
        }
        planned = {plan['model']._meta.db_table
                   for plan in MERGE_PLAN.values()}
        self.assertEqual(constrained - planned, set(),
                         'модель с уникальностью не попала в план слияния')

    def test_merge_plan_references_cover_all_foreign_keys(self):
        """Каждая ссылка на справочник должна быть перевешена до удаления.

        Lesson.subject защищён PROTECT: если забыть перевесить, удаление
        дубля упадёт с ProtectedError.
        """
        from schedule.merge_duplicates import _build_references
        from schedule.models import Lesson, LessonDraft, Substitution
        references = _build_references()
        self.assertIn((Lesson, 'subject'), references['subject'],
                      'Lesson.subject не перевешивается')
        self.assertIn((Substitution, 'teacher'), references['teacher'])
        self.assertIn((Substitution, 'room'), references['room'])
        # Черновик правок тоже ссылается на справочники: забытая ссылка
        # осталась бы на удалённом дубле и правка потеряла бы смысл.
        self.assertIn((LessonDraft, 'teacher'), references['teacher'])
        self.assertIn((LessonDraft, 'subject'), references['subject'])
        self.assertIn((LessonDraft, 'room'), references['room'])
        self.assertIn((LessonDraft, 'group'), references['group'])

    def test_fill_keys_is_idempotent(self):
        fill_normalized_keys()
        self.assertEqual(fill_normalized_keys(), 0,
                         'второй проход не должен ничего менять')


class MergeMigrationTests(TransactionTestCase):
    """Критерий приёмки: `migrate` на существующей БД проходит, дубли слиты.

    Единственный честный способ это проверить: откатить базу на миграцию до
    ограничений, создать дубли вручную — так они выглядели на реальной базе —
    и прогнать миграцию вперёд.
    """

    migrate_from = ('schedule', '0005_importlog_deleted_count')
    # Вперёд до последней миграции, а не до 0007: тест откатывает базу
    # назад, и если не дойти до конца, таблицы свежих миграций (черновики
    # правок) не будет — упадёт уже снос базы, а не проверка.
    migrate_to = ('schedule', '0010_lessondraft')

    def _migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([target])
        return executor.loader.project_state([target])

    def test_migration_merges_duplicates_and_adds_constraint(self):
        try:
            old = self._migrate(self.migrate_from)
            Teacher = old.apps.get_model('schedule', 'Teacher')
            Subject = old.apps.get_model('schedule', 'Subject')
            Group = old.apps.get_model('schedule', 'Group')
            Lesson = old.apps.get_model('schedule', 'Lesson')
            LessonTime = old.apps.get_model('schedule', 'LessonTime')

            lesson_time = LessonTime.objects.create(
                number=1, start='08:30', end='09:50')
            group = Group.objects.create(name='ЭК-01', course=1)
            canonical = Subject.objects.create(
                name='Финансы', short_name='Фин', color='#c8e6c9')
            dupe = Subject.objects.create(
                name='финансы', short_name='', color='#bbdefb')
            Lesson.objects.create(
                group=group, subject=dupe, day=1, lesson_time=lesson_time,
                week_parity=0,
            )
            # канонический преподаватель без телефона, дубль — с телефоном
            teacher_canonical = Teacher.objects.create(full_name='Иванов И.И.')
            Teacher.objects.create(
                full_name='иванов и.и.', phone='+998 900 000')
            self.assertEqual(Subject.objects.count(), 2)

            self._migrate(self.migrate_to)
        finally:
            merged_apps = self._migrate(
                ('schedule', '0007_merge_duplicates_and_unique')).apps

        # дубли слиты
        self.assertEqual(Subject.objects.count(), 1)
        self.assertEqual(Teacher.objects.count(), 1)
        self.assertEqual(Subject.objects.get().pk, canonical.pk)
        # ссылка занятия перевешена на канонический объект
        self.assertEqual(Lesson.objects.get().subject_id, canonical.pk)
        # пустое поле канонического дополнено полем дубля
        self.assertEqual(Teacher.objects.get().pk, teacher_canonical.pk)
        self.assertEqual(Teacher.objects.get().phone, '+998 900 000')
        # ограничение теперь на месте и дубли больше не проходят
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Subject.objects.create(name='ФИНАНСЫ')
        # Ищем дубли по моделям того состояния, на котором стоит схема:
        # живые модели содержат поля, добавленные позже (например, цвет типа
        # занятия), и запрос к ним упал бы с «no such column».
        self.assertEqual(find_duplicate_groups(apps=merged_apps), {})

    def test_migration_on_clean_database_changes_nothing(self):
        try:
            old = self._migrate(self.migrate_from)
            Subject = old.apps.get_model('schedule', 'Subject')
            Subject.objects.create(name='Финансы')
            Subject.objects.create(name='Философия')
            self._migrate(self.migrate_to)
        finally:
            self._migrate(('schedule', '0007_merge_duplicates_and_unique'))
        self.assertEqual(Subject.objects.count(), 2)
        self.assertEqual(
            sorted(Subject.objects.values_list('name', flat=True)),
            ['Философия', 'Финансы'],
        )

    def test_merge_covers_every_model_with_constraint(self):
        for kind in ('teacher', 'subject', 'lessontype', 'room', 'group'):
            with self.subTest(kind=kind):
                from schedule.merge_duplicates import MERGE_PLAN
                self.assertIn(kind, MERGE_PLAN)