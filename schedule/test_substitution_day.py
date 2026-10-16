"""Замена не должна разойтись с расписанием.

Что тут было. Проверка «дата замены совпадает с днём недели занятия» жила
в ``Substitution.clean()``, а ``clean()`` зовёт только ``full_clean()`` —
то есть ModelForm в админке. ``Substitution.objects.create(...)`` правило
пропускал.

Чем это кончалось: запись с чужой датой попадала в базу и там пропадала.
``grid.build_grid`` ищет замену по ключу ``(lesson_id, date.isoweekday())``,
поэтому расхождение означало, что замена просто не появлялась в таблице.
Ни ошибки, ни метки «замена» — просто неверное расписание, и найти
причину можно только вручную.

Почему проверка в Python, а не CHECK в базе. SQLite не поддерживает
``strftime`` в CHECK (проверено: `near "%": syntax error`), а день недели
из даты без календаря арифметикой не выводится. Поэтому инвариант держит
код — в трёх точках: ``save()``, ``bulk_create()`` и ``update()``.
Последние двое обходят ``save()``, и без них дыра осталась бы наполовину
закрытой.

Тесты проверяют не «есть ли clean()», а последствие: после любого способа
записи замена обязана находиться в расписании. Это и есть тот инвариант,
ради которого всё затевалось.
"""

import datetime as dt

from django.core.exceptions import ValidationError
from django.test import TestCase

from schedule.grid import build_grid
from schedule.models import (
    Group,
    Lesson,
    LessonTime,
    Room,
    Substitution,
    Subject,
    Teacher,
)


class SubstitutionDayInvariantMixin:
    """Общая заготовка: одно занятие в понедельник.

    2026-01-05 — понедельник, 2026-01-07 — среда. Второе число нужно
    только как «заведомо чужой день».
    """

    @classmethod
    def setUpTestData(cls):
        cls.group = Group.objects.create(name='G1')
        cls.subject = Subject.objects.create(name='Физика')
        cls.teacher = Teacher.objects.create(full_name='Иванов И.И.')
        cls.lesson_time = LessonTime.objects.create(
            number=1, start=dt.time(8, 0), end=dt.time(9, 30),
        )
        cls.monday = dt.date(2026, 1, 5)
        cls.wednesday = dt.date(2026, 1, 7)
        cls.lesson = Lesson.objects.create(
            group=cls.group, day=1, lesson_time=cls.lesson_time,
            subject=cls.subject, teacher=cls.teacher,
        )
        cls.tuesday_lesson = Lesson.objects.create(
            group=cls.group, day=2, lesson_time=cls.lesson_time,
            subject=cls.subject, teacher=cls.teacher,
        )

    def replacement_teacher(self):
        return Teacher.objects.create(full_name='Петров П.П.')

    def assert_in_grid(self, lesson, monday_of_week, expected):
        """Видна ли замена в расписании заданной недели.

        Проверяется итог, а не устройство ключа: смысл инварианта в том,
        что корректно сохранённая замена находится в таблице.
        """
        grid = build_grid(
            queryset=Lesson.objects.filter(pk=lesson.pk),
            week_monday=monday_of_week,
        )
        found = [
            cell['changed']
            for row in grid['rows']
            for cells in row['cells'].values()
            for cell in cells
        ]
        self.assertEqual(
            any(found), expected,
            f'замена в расписании: {"видна" if any(found) else "НЕ видна"}, '
            f'ожидалось {"видна" if expected else "не видна"}',
        )


class CreateRejectsWrongDayTests(SubstitutionDayInvariantMixin, TestCase):
    """Обычный create не должен принимать чужую дату."""

    def test_create_with_wrong_day_is_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            Substitution.objects.create(
                lesson=self.lesson, date=self.wednesday,
                teacher=self.replacement_teacher(),
            )
        self.assertIn('date', ctx.exception.error_dict)

    def test_nothing_is_written_on_rejection(self):
        """Отказ не должен оставлятьhalf-запись в базе."""
        with self.assertRaises(ValidationError):
            Substitution.objects.create(
                lesson=self.lesson, date=self.wednesday,
            )
        self.assertEqual(Substitution.objects.count(), 0)

    def test_correct_day_is_accepted(self):
        sub = Substitution.objects.create(
            lesson=self.lesson, date=self.monday,
            teacher=self.replacement_teacher(), reason='заболел',
        )
        self.assertEqual(Substitution.objects.count(), 1)
        self.assertEqual(sub.date, self.monday)

    def test_correct_substitution_is_visible_in_grid(self):
        """Главный инвариант: сохранённая замена обязана быть в таблице."""
        Substitution.objects.create(
            lesson=self.lesson, date=self.monday,
            teacher=self.replacement_teacher(),
        )
        self.assert_in_grid(self.lesson, self.monday, True)


class SaveRejectsWrongDayTests(SubstitutionDayInvariantMixin, TestCase):
    """Смена даты у уже сохранённой записи — тоже проверяется."""

    def setUp(self):
        self.sub = Substitution.objects.create(
            lesson=self.lesson, date=self.monday,
        )

    def test_saving_a_changed_date_is_rejected(self):
        self.sub.date = self.wednesday
        with self.assertRaises(ValidationError):
            self.sub.save()

    def test_old_date_survives_rejected_save(self):
        """После отказа дата в базе должна остаться прежней.

        Иначе неудачная попытка тихо портит расписание — ровно та дыра,
        которую чиним, только с другой стороны.
        """
        self.sub.date = self.wednesday
        with self.assertRaises(ValidationError):
            self.sub.save()
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.date, self.monday)

    def test_changing_teacher_does_not_require_repeat_check(self):
        """Правка преподавателя проходит: день не менялся."""
        self.sub.teacher = self.replacement_teacher()
        self.sub.save()
        self.assertEqual(self.sub.teacher.full_name, 'Петров П.П.')


class BulkCreateRejectsWrongDayTests(SubstitutionDayInvariantMixin, TestCase):
    """bulk_create не зовёт save() и потому обходит clean().

    Если оставить как есть, в базе появлялась запись, которой в таблице
    нет. Это отдельный путь записи, и он тоже должен быть закрыт.
    """

    def test_bulk_create_with_wrong_day_is_rejected(self):
        with self.assertRaises(ValidationError):
            Substitution.objects.bulk_create([
                Substitution(lesson=self.lesson, date=self.wednesday),
            ])
        self.assertEqual(Substitution.objects.count(), 0)

    def test_bulk_create_with_correct_day_is_accepted(self):
        Substitution.objects.bulk_create([
            Substitution(lesson=self.lesson, date=self.monday),
        ])
        self.assertEqual(Substitution.objects.count(), 1)
        self.assert_in_grid(self.lesson, self.monday, True)

    def test_mixed_batch_is_rejected_whole(self):
        """Одна неверная запись отменяет всю пачку.

        Иначе в пачке часть записей сохранилась бы, часть — нет, и
        разбираться пришлось бы вручную.
        """
        with self.assertRaises(ValidationError):
            Substitution.objects.bulk_create([
                Substitution(lesson=self.lesson, date=self.monday),
                Substitution(lesson=self.lesson, date=self.wednesday),
            ])
        self.assertEqual(Substitution.objects.count(), 0)


class UpdateRejectsWrongDayTests(SubstitutionDayInvariantMixin, TestCase):
    """queryset.update() — третий путь в обход save().

    Проверять надо именно существующие записи, а не фильтр по новым
    значениям: если фильтровать по тому, что собираемся записать,
    подходящая запись не найдётся, проверка молча ничего не сделает и дыра
    останется.
    """

    def setUp(self):
        self.sub = Substitution.objects.create(
            lesson=self.lesson, date=self.monday,
        )

    def test_update_to_wrong_date_is_rejected(self):
        with self.assertRaises(ValidationError):
            Substitution.objects.filter(pk=self.sub.pk).update(
                date=self.wednesday)

    def test_update_to_wrong_lesson_is_rejected(self):
        """Смена занятия на вторник при дате понедельника — тоже ошибка."""
        with self.assertRaises(ValidationError):
            Substitution.objects.filter(pk=self.sub.pk).update(
                lesson=self.tuesday_lesson)

    def test_date_in_database_survives_rejected_update(self):
        with self.assertRaises(ValidationError):
            Substitution.objects.filter(pk=self.sub.pk).update(
                date=self.wednesday)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.date, self.monday)

    def test_update_unrelated_field_passes(self):
        """Причина к дню недели не относится — правка проходит."""
        Substitution.objects.filter(pk=self.sub.pk).update(reason='заболел')
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.reason, 'заболел')

    def test_update_to_valid_date_passes(self):
        other_monday = dt.date(2026, 1, 12)
        Substitution.objects.filter(pk=self.sub.pk).update(date=other_monday)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.date, other_monday)
        self.assert_in_grid(self.lesson, other_monday, True)

    def test_partial_update_is_merged_with_stored_values(self):
        """Смена даты при сохранении остальных полей сверяется целиком.

        ``update(date=...)`` не передаёт занятие, поэтому оно берётся из
        базы. Проверяется собранное состояние: если бы правило смотрело
        только на переданные аргументы, запись со сменой занятия
        проскочила бы незамеченной.

        Права по ``reason`` сюда не относятся: она не влияет на день
        недели, и правка причины проходит — это проверено отдельным
        тестом выше.
        """
        from django.db import connection

        # Прямая правка: так запись выглядела до починки.
        with connection.cursor() as cur:
            cur.execute(
                'UPDATE schedule_substitution SET date = %s WHERE id = %s',
                [self.wednesday.isoformat(), self.sub.pk],
            )

        # Правка причины записи с чужой датой проходит: она не трогает
        # день недели, и это осознанное поведение.
        Substitution.objects.filter(pk=self.sub.pk).update(reason='было')

        # А вот попытка починить дату «в ту же дату» обязана быть отклонена
        # — иначе запись навсегда осталась бы сломанной.
        with self.assertRaises(ValidationError):
            Substitution.objects.filter(pk=self.sub.pk).update(
                date=self.wednesday)


class AdminStillExplainsTests(SubstitutionDayInvariantMixin, TestCase):
    """Админка обязана давать ту же понятную ошибку."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from django.contrib.auth.models import User
        cls.admin = User.objects.create_superuser('sub_admin', 'a@b.c', 'pw')

    def setUp(self):
        self.client.force_login(self.admin)

    def test_admin_form_rejects_wrong_date_with_readable_text(self):
        from django.urls import reverse
        response = self.client.post(
            reverse('admin:schedule_substitution_add'),
            {
                'lesson': self.lesson.pk,
                'date': self.wednesday.isoformat(),
                'teacher': '',
                'room': '',
                'reason': 'заболел',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'совпадать с днём недели', status_code=200)
        self.assertEqual(Substitution.objects.count(), 0)

    def test_admin_form_accepts_correct_date(self):
        from django.urls import reverse
        response = self.client.post(
            reverse('admin:schedule_substitution_add'),
            {
                'lesson': self.lesson.pk,
                'date': self.monday.isoformat(),
                'teacher': '',
                'room': '',
                'reason': 'заболел',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Substitution.objects.count(), 1)


class MessageIsSharedTests(TestCase):
    """Текст ошибки один на всех, иначе сообщения разъедутся."""

    def test_model_and_queryset_share_message(self):
        from schedule.models import SubstitutionQuerySet
        self.assertEqual(
            SubstitutionQuerySet.DAY_MISMATCH,
            'Дата замены должна совпадать с днём недели занятия.',
        )

    def test_error_names_the_date_field(self):
        """Ошибка должна быть на поле date.

        На другом поле админка подсветит не то поле, и сотрудница не
        поймёт, что исправлять.
        """
        errors = None
        try:
            Substitution(lesson_id=None, date=dt.date(2026, 1, 5)).clean()
        except ValidationError as exc:
            errors = exc
        # Без занятия проверка не выполняется — ошибки быть не должно.
        self.assertIsNone(errors)


class ExistingDataAuditTests(TestCase):
    """Поиск нарушений в данных: команда аудита и её результат."""

    def test_broken_records_can_be_found(self):
        """Находит запись, у которой дата разошлась с днём недели.

        Именно так чинилась запись, созданная до появления проверки: её
        нашли не по симптому в таблице, а прямым сравнением.
        """
        group = Group.objects.create(name='G1')
        subject = Subject.objects.create(name='Физика')
        lesson_time = LessonTime.objects.create(
            number=1, start=dt.time(8, 0), end=dt.time(9, 30))
        monday_lesson = Lesson.objects.create(
            group=group, day=1, lesson_time=lesson_time, subject=subject)

        # Корректная замена.
        good = Substitution.objects.create(
            lesson=monday_lesson, date=dt.date(2026, 1, 5))

        # Нарушение вносится прямым INSERT: все пути через ORM теперь
        # проверяют день недели, иначе сломать запись было бы нечем.
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute(
                'INSERT INTO schedule_substitution '
                '(lesson_id, date, teacher_id, room_id, reason) '
                'VALUES (%s, %s, NULL, NULL, %s)',
                [monday_lesson.pk, '2026-01-07', 'сломана'],
            )

        mismatches = [
            sub for sub in Substitution.objects.select_related('lesson')
            if sub.lesson.day != sub.date.weekday() + 1
        ]
        self.assertEqual(len(mismatches), 1)
        self.assertEqual(mismatches[0].reason, 'сломана')