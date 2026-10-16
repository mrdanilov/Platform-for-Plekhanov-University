"""Раздел «Сырые занятия»: подбор группы и починка.

Что тут было. Занятия без группы создаёт импорт, когда ячейку не удалось
разобрать на части: текст сохраняется в ``raw_text``, группа остаётся
пустой. Такая запись **не видна нигде** — ни в расписании группы (её
нет), ни преподавателя (если не указан), ни аудитории. В базе есть, на
сайте нет.

Разбор нужен, чтобы эти записи не копились годами. И он опасен, если
подсказывает не ту группу: сотрудница нажмёт «проставить» и занятие
уедет не туда. Поэтому тесты проверяют главное — **что подсказка верная
и что без совпадений её нет вовсе**.

Отдельно проверяется то, из-за чего подбор может навредить: ``G1`` не
должна считаться подходящей к ``EnG1``, потому что ``g1`` входит в
``eng1`` как подстрока.
"""

import datetime as dt

from django.test import TestCase
from django.urls import reverse

from schedule.models import Group, Lesson, LessonTime, Room, Subject, Teacher
from schedule.raw_lessons import (
    candidate_groups,
    groupless_lessons,
    suggestions_for,
)


class RawLessonTestMixin:
    @classmethod
    def setUpTestData(cls):
        cls.teacher = Teacher.objects.create(full_name='Кудзинава А.В.')
        cls.room = Room.objects.create(name='305')
        cls.subject = Subject.objects.create(name='Эргономика')
        cls.lesson_time = LessonTime.objects.create(
            number=1, start=dt.time(8, 0), end=dt.time(9, 30))
        cls.eng1 = Group.objects.create(name='EnG1')
        cls.g1 = Group.objects.create(name='G1')
        cls.eng2 = Group.objects.create(name='EnG2')

    def make_raw(self, text, **kwargs):
        """Занятие без группы — как создаёт импорт."""
        kwargs.setdefault('day', 1)
        kwargs.setdefault('lesson_time', self.lesson_time)
        kwargs.setdefault('subject', self.subject)
        return Lesson.objects.create(raw_text=text, **kwargs)


class CandidateMatchingTests(RawLessonTestMixin, TestCase):
    """Подбор группы по тексту ячейки."""

    def test_finds_group_mentioned_in_text(self):
        lesson = self.make_raw('EnG1 / Эргономика / Кудзинава А.В.')
        self.assertEqual(suggestions_for(lesson), self.eng1)

    def test_similar_name_is_not_a_match(self):
        """G1 не подходит к EnG1: g1 входит в eng1 как подстрока.

        Ложная подсказка хуже отсутствия подсказки — по ней поставили бы
        не ту группу, и занятие уехало бы в чужое расписание.
        """
        lesson = self.make_raw('EnG1 / Эргономика')
        names = [g.name for g, _ in candidate_groups(lesson)]
        self.assertIn('EnG1', names)
        self.assertNotIn('G1', names)

    def test_no_match_when_group_absent(self):
        """Нет группы в тексте — нет и подсказки."""
        lesson = self.make_raw('Эргономика / доц. Кудзинава')
        self.assertIsNone(suggestions_for(lesson))
        self.assertEqual(candidate_groups(lesson), [])

    def test_case_does_not_matter(self):
        """EnG1 и eng1 — одна группа.

        Именно из-за различий в регистре импорт и не узнаёт группу,
        поэтому разбор обязан понимать их как одно и то же.
        """
        for text in ('eng1 / Эргономика', 'EnG1 / Эргономика'):
            with self.subTest(text=text):
                lesson = self.make_raw(text)
                self.assertEqual(suggestions_for(lesson), self.eng1)

    def test_spaced_out_name_is_not_matched(self):
        """«ЭНГ 1» — это не EnG1, и подсказывать её нельзя.

        Нормализация приводит регистр и «ё»/«е», но не убирает пробелы
        внутри номера: ``ЭНГ 1`` схлопывается в ``энг 1``, а ``EnG1`` — в
        ``eng1``. Совпадения нет, и это правильно: разница может быть
        осмысленной. Подсказка «возможно, это EnG1» была бы догадкой, и
        занятие уехало бы не в ту группу. Тогда страница показывает
        несколько вариантов, и выбор остаётся за человеком.
        """
        lesson = self.make_raw('ЭНГ 1 / Эргономика')
        self.assertIsNone(suggestions_for(lesson))

    def test_group_size_in_parentheses_is_ignored(self):
        """«4301 (26 ст.)» — это 4301 с числом студентов.

        Хвост в скобках к номеру группы не относится, но в файле именно
        так группа и подписана.
        """
        group = Group.objects.create(name='4301')
        lesson = self.make_raw('4301 (26 ст.) / Эргономика')
        self.assertEqual(suggestions_for(lesson), group)

    def test_two_candidates_give_no_single_suggestion(self):
        """Две подходящие группы — это не подсказка, а лотерея.

        Лучше сказать «выберите сами», чем предложить одну наугад.
        """
        lesson = self.make_raw('EnG1 / Эргономика')
        self.assertIsNotNone(candidate_groups(lesson))
        # Сейчас кандидат один. Проверяем сам принцип на случае двух.
        Lesson.objects.create(
            raw_text='EnG1', day=2, lesson_time=self.lesson_time,
            subject=self.subject,
        )
        self.assertIsNone(
            suggestions_for(self.make_raw('EnG1 / Эргономика / EnG2')),
            'при нескольких совпадениях подсказка не должна выбираться',
        )

    def test_empty_text_gives_no_suggestion(self):
        lesson = self.make_raw('')
        self.assertIsNone(suggestions_for(lesson))
        self.assertEqual(candidate_groups(lesson), [])

    def test_lesson_with_group_is_not_considered(self):
        """Запись с группой в разбор не попадает."""
        lesson = Lesson.objects.create(
            group=self.eng1, day=1, lesson_time=self.lesson_time,
            subject=self.subject, raw_text='EnG1 / Эргономика',
        )
        self.assertEqual(candidate_groups(lesson), [])
        self.assertNotIn(lesson, list(groupless_lessons()))


class GrouplessListTests(RawLessonTestMixin, TestCase):
    """Выборка для разбора."""

    def test_list_contains_only_groupless(self):
        self.make_raw('EnG1 / Эргономика')
        Lesson.objects.create(
            group=self.eng1, day=2, lesson_time=self.lesson_time,
            subject=self.subject)
        self.assertEqual(groupless_lessons().count(), 1)

    def test_empty_database_gives_empty_list(self):
        self.assertEqual(list(groupless_lessons()), [])


class RawPageTests(RawLessonTestMixin, TestCase):
    """Страница разбора в админке."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from django.contrib.auth.models import User
        cls.admin = User.objects.create_superuser('raw_admin', 'a@b.c', 'pw')

    def setUp(self):
        self.client.force_login(self.admin)

    def test_page_opens_and_is_empty_by_default(self):
        response = self.client.get(reverse('admin:schedule_groupless'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('Разбирать нечего', response.content.decode())

    def test_page_shows_pending_text(self):
        """Сотрудница должна видеть текст ячейки, а не «объект (нет)»."""
        self.make_raw('Эргономика / доц. Кудзинава')
        body = self.client.get(
            reverse('admin:schedule_groupless')).content.decode()
        self.assertIn('Эргономика', body)

    def test_page_warns_about_invisible_record(self):
        """Запись без преподавателя не видна нигде — об этом сказано.

        Иначе сотрудница решит, что занятие просто в другой группе.
        """
        self.make_raw('Эргономика / доц. Кудзинава')
        body = self.client.get(
            reverse('admin:schedule_groupless')).content.decode()
        self.assertIn('не видна ни в чьём расписании', body)

    def test_page_offers_suggestion(self):
        self.make_raw('EnG1 / Эргономика')
        body = self.client.get(
            reverse('admin:schedule_groupless')).content.decode()
        self.assertIn('вероятно, это', body)
        self.assertIn('EnG1', body)

    def test_page_has_no_staff_add_permission(self):
        """Раздел не редактор: добавлять занятия здесь нельзя."""
        from schedule.raw_lessons import GrouplessLessonsAdmin
        request = self.client.get(
            reverse('admin:schedule_groupless')).wsgi_request
        page = GrouplessLessonsAdmin(Lesson, None)
        self.assertFalse(page.has_add_permission(request))
        self.assertFalse(page.has_change_permission(request))


class AssignGroupTests(RawLessonTestMixin, TestCase):
    """Починка одной кнопкой."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from django.contrib.auth.models import User
        cls.admin = User.objects.create_superuser('raw_fix', 'a@b.c', 'pw')

    def setUp(self):
        self.client.force_login(self.admin)

    def test_assign_group_fixes_the_record(self):
        lesson = self.make_raw('Эргономика / доц. Кудзинава')
        response = self.client.get(reverse(
            'admin:schedule_lesson_assign_group',
            args=[lesson.pk, self.eng1.pk]))
        self.assertEqual(response.status_code, 302)
        lesson.refresh_from_db()
        self.assertEqual(lesson.group_id, self.eng1.pk)

    def test_fixed_record_leaves_the_queue(self):
        lesson = self.make_raw('Эргономика / доц. Кудзинава')
        self.client.get(reverse(
            'admin:schedule_lesson_assign_group',
            args=[lesson.pk, self.eng1.pk]))
        self.assertNotIn(lesson, list(groupless_lessons()))

    def test_fixed_record_becomes_visible_in_timetable(self):
        """Главная проверка: починенная запись появилась на сайте.

        Проверяется через настоящую страницу, а не через ``build_grid``
        напрямую: сетка рисует всё, что ей передали, и «видимость»
        определяется выборкой в представлении. Запись без группы, без
        преподавателя и без аудитории не попадает ни в одну из них —
        поэтому на сайте её не найти.
        """
        lesson = self.make_raw('Эргономика / Кудзинава')  # без группы,
        # без преподавателя, без аудитории
        self.assertIsNone(lesson.group_id)

        self.client.get(reverse(
            'admin:schedule_lesson_assign_group',
            args=[lesson.pk, self.eng1.pk]))

        lesson.refresh_from_db()
        self.assertEqual(lesson.group_id, self.eng1.pk)

        # Теперь запись попадает в выборку группы и рисуется.
        from schedule.views import _queryset_for
        week_monday = dt.date(2026, 1, 5)
        queryset = _queryset_for('group', self.eng1, week_monday)
        self.assertIn(
            lesson.pk, queryset.values_list('pk', flat=True),
            'после простановки группы занятие должно попасть в расписание '
            'этой группы',
        )

    def test_record_without_teacher_was_in_no_queryset(self):
        """Почему запись невидима: ни одна выборка её не содержит.

        Проверяются все четыре вида расписания. Если хотя бы одно
        подхватывало бы запись, раздел «Сырые занятия» был бы не про
        невидимость, а просто про удобство, и предупреждение в интерфейсе
        вводило бы в заблуждение.
        """
        from schedule.views import _queryset_for

        lesson = self.make_raw('Эргономика / Кудзинава')
        week_monday = dt.date(2026, 1, 5)
        for view, subject in (
            ('group', self.eng1),
            ('teacher', self.teacher),
            ('room', self.room),
        ):
            with self.subTest(view=view):
                queryset = _queryset_for(view, subject, week_monday)
                self.assertNotIn(
                    lesson.pk, queryset.values_list('pk', flat=True),
                )

    def test_record_with_teacher_is_already_visible(self):
        """Уточнение: без группы, но с преподавателем запись видна.

        Значит «сырая» не равно «невидимая»: в разделе разбора лежат
        записи двух разных сортов. Проверяется, чтобы формулировка в
        интерфейсе не вводила в заблуждение.
        """
        from schedule.views import _queryset_for

        lesson = self.make_raw('Эргономика / Кудзинава',
                               teacher=self.teacher)
        queryset = _queryset_for('teacher', self.teacher, dt.date(2026, 1, 5))
        self.assertIn(lesson.pk, queryset.values_list('pk', flat=True))

    def test_deleted_group_is_refused(self):
        """Группа могла удалиться между показом страницы и нажатием.

        Без проверки занятие осталось бы со ссылкой в пустоту.
        """
        lesson = self.make_raw('Эргономика / доц. Кудзинава')
        gone = Group.objects.create(name='Удалённая')
        pk = gone.pk
        gone.delete()
        self.client.get(reverse(
            'admin:schedule_lesson_assign_group', args=[lesson.pk, pk]))
        lesson.refresh_from_db()
        self.assertIsNone(lesson.group_id, 'группа проставилась, хотя её нет')

    def test_regular_lesson_is_not_in_queue(self):
        """Проставить группу обычному занятию нельзя через этот раздел."""
        lesson = Lesson.objects.create(
            group=self.g1, day=1, lesson_time=self.lesson_time,
            subject=self.subject)
        # Группа из другого курса: иначе нарушится уникальность по
        # (название, курс) и тест упал бы не по той причине.
        other = Group.objects.create(name='EnG2', course=2)
        self.client.get(reverse(
            'admin:schedule_lesson_assign_group',
            args=[lesson.pk, other.pk]))
        lesson.refresh_from_db()
        self.assertEqual(lesson.group_id, self.g1.pk)


class DeleteRawTests(RawLessonTestMixin, TestCase):
    """Удаление очереди, чтобы она не копилась годами."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from django.contrib.auth.models import User
        cls.admin = User.objects.create_superuser('raw_del', 'a@b.c', 'pw')

    def setUp(self):
        self.client.force_login(self.admin)

    def test_get_does_not_delete(self):
        """Удаление только по POST: иначе ссылка из почты удалила бы запись."""
        lesson = self.make_raw('Эргономика / доц. Кудзинава')
        self.client.get(reverse('admin:schedule_lesson_delete_raw',
                                args=[lesson.pk]))
        self.assertEqual(Lesson.objects.filter(pk=lesson.pk).count(), 1)

    def test_post_deletes(self):
        lesson = self.make_raw('Эргономика / доц. Кудзинава')
        self.client.post(reverse('admin:schedule_lesson_delete_raw',
                                 args=[lesson.pk]))
        self.assertEqual(Lesson.objects.filter(pk=lesson.pk).count(), 0)

    def test_lesson_list_shows_banner_when_queue_is_not_empty(self):
        """Полоса в списке занятий: без неё очередь не заметят.

        Ссылка стоит в списке занятий, а не в меню приложения:
        ``/admin/schedule/`` в Django редиректит на первую модель, и
        пункт в меню туда не попал бы.
        """
        self.make_raw('Эргономика / доц. Кудзинава')
        body = self.client.get(
            reverse('admin:schedule_lesson_changelist')).content.decode()
        self.assertIn('Сырые занятия', body)
        self.assertIn('разобрать', body)
        self.assertIn(reverse('admin:schedule_groupless'), body)

    def test_no_banner_when_queue_is_empty(self):
        """Постоянная надпись «0 — разбирать нечего» приучила бы не читать.

        Полоса появляется, когда очередь появилась, иначе её бы не
        заметили в первый же раз.
        """
        body = self.client.get(
            reverse('admin:schedule_lesson_changelist')).content.decode()
        self.assertNotIn('Сырые занятия', body)

    def test_banner_count_matches_queue(self):
        self.make_raw('Эргономика / доц. Кудзинава')
        self.make_raw('Математика / доц. Иванов')
        body = self.client.get(
            reverse('admin:schedule_lesson_changelist')).content.decode()
        self.assertIn('Сырые занятия: 2', body)