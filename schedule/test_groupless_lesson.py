"""Занятие без группы не должно ронять страницы.

Что тут было. Поле ``Lesson.group`` — nullable, и записи без группы
создаёт импорт: ячейку Excel не удалось разобрать на части, её текст
сохраняется целиком в ``raw_text``, а группа остаётся пустой (см.
``excel.py``, ветка со ``slot = {'group': None, ...}``). Это штатный
ход разбора, а не ошибка.

Из-за этого ``get_absolute_url()`` падал: он жёстко подставлял
``group_id``, и ``reverse('schedule:group_detail', args=[None])``
заканчивался ``NoReverseMatch``. Клик «Посмотреть на сайте» в админке
на такой записи давал 500.

Тесты проверяют не «есть ли guard в коде», а то, что сотрудник видит:
ссылка ведёт на живую страницу, в списке запись опознана как
импортная, а её текст не потерялся.
"""

import datetime as dt

from django.test import TestCase
from django.urls import reverse

from schedule.models import Group, Lesson, LessonTime, Room, Subject, Teacher


class GrouplessLessonTestMixin:
    @classmethod
    def setUpTestData(cls):
        cls.group = Group.objects.create(name='G1')
        cls.subject = Subject.objects.create(name='Физика')
        cls.teacher = Teacher.objects.create(full_name='Иванов И.И.')
        cls.room = Room.objects.create(name='305')
        cls.lesson_time = LessonTime.objects.create(
            number=1, start=dt.time(8, 0), end=dt.time(9, 30))

    def make(self, **kwargs):
        """Занятие без группы — как создаёт импорт."""
        kwargs.setdefault('subject', self.subject)
        kwargs.setdefault('day', 2)
        kwargs.setdefault('lesson_time', self.lesson_time)
        kwargs.setdefault('raw_text', 'неразобранная ячейка')
        return Lesson.objects.create(**kwargs)


class AbsoluteUrlTests(GrouplessLessonTestMixin, TestCase):
    """Ссылка «Посмотреть на сайте» не должна падать."""

    def test_groupless_lesson_url_is_built(self):
        """Главный критерий задачи: без исключения."""
        lesson = self.make(teacher=self.teacher)
        self.assertTrue(lesson.get_absolute_url())

    def test_with_group_url_unchanged(self):
        """Обычные записи ведут на страницу группы, как и раньше."""
        lesson = Lesson.objects.create(
            group=self.group, subject=self.subject, day=1,
            lesson_time=self.lesson_time, teacher=self.teacher,
        )
        self.assertEqual(
            lesson.get_absolute_url(),
            reverse('schedule:group_detail', args=[self.group.pk]),
        )

    def test_falls_back_to_teacher_page(self):
        """Занятие видно в расписании преподавателя — ведём туда."""
        lesson = self.make(teacher=self.teacher)
        self.assertEqual(
            lesson.get_absolute_url(),
            reverse('schedule:teacher_detail', args=[self.teacher.pk]),
        )

    def test_falls_back_to_room_page(self):
        """Если преподавателя нет, остаётся аудитория."""
        lesson = self.make(room=self.room)
        self.assertEqual(
            lesson.get_absolute_url(),
            reverse('schedule:room_detail', args=[self.room.pk]),
        )

    def test_teacher_preferred_over_room(self):
        """Преподаватель важнее аудитории: его расписание полнее."""
        lesson = self.make(teacher=self.teacher, room=self.room)
        self.assertIn(f'/teachers/{self.teacher.pk}/', lesson.get_absolute_url())

    def test_falls_back_to_index_when_nothing_known(self):
        """Совсем пустая запись — на главную, а не в никуда."""
        lesson = self.make()
        self.assertEqual(lesson.get_absolute_url(), reverse('schedule:index'))

    def test_url_is_never_empty(self):
        """Пустая строка в админке ведёт себя непредсказуемо.

        Без href браузер может трактовать её как относительный путь и
        увести на несуществующий адрес. Поэтому проверяется и сам факт
        непустоты, и то, что адрес начинается со слэша.
        """
        for kwargs in ({}, {'teacher': self.teacher}, {'room': self.room}):
            with self.subTest(kwargs=kwargs):
                url = self.make(**kwargs).get_absolute_url()
                self.assertTrue(url)
                self.assertTrue(url.startswith('/'), url)

    def test_link_actually_opens(self):
        """Критерий приёмки: не 500, а рабочая страница.

        Проверяется по цепочке: админка даёт ссылку, за ней идёт
        редирект на страницу расписания, и та отвечает 200.
        """
        lesson = self.make(teacher=self.teacher)
        resp = self.client.get(lesson.get_absolute_url())
        self.assertEqual(resp.status_code, 302)
        target = self.client.get(resp['Location'])
        self.assertEqual(target.status_code, 200)


class StringRepresentationTests(GrouplessLessonTestMixin, TestCase):
    """В списках запись не должна выглядеть как «None · …»."""

    def test_no_none_in_representation(self):
        lesson = self.make(teacher=self.teacher)
        self.assertNotIn('None', str(lesson))

    def test_shows_day_by_name(self):
        lesson = self.make(teacher=self.teacher)
        self.assertIn('Вторник', str(lesson))

    def test_shows_raw_text(self):
        """Текст неразобранной ячейки — единственная копия из файла.

        Если в строке его не видно, сотрудница не поймёт, что вообще
        сохранилось в этом занятии.
        """
        lesson = self.make(teacher=self.teacher,
                           raw_text='Микроэкономика 12, ауд 305')
        self.assertIn('Микроэкономика 12, ауд 305', str(lesson))

    def test_handles_missing_raw_text(self):
        """Пустой raw_text не должен ломать вывод строки."""
        lesson = self.make(raw_text='', teacher=self.teacher)
        self.assertNotIn('None', str(lesson))

    def test_regular_lesson_keeps_group_first(self):
        lesson = Lesson.objects.create(
            group=self.group, subject=self.subject, day=1,
            lesson_time=self.lesson_time, teacher=self.teacher,
        )
        self.assertIn('G1', str(lesson))


class AdminDisplayTests(GrouplessLessonTestMixin, TestCase):
    """В админке запись опознана как импортная, а не как битая."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from django.contrib.auth.models import User
        cls.admin = User.objects.create_superuser('gl_admin', 'a@b.c', 'pw')

    def setUp(self):
        self.client.force_login(self.admin)

    def test_list_marks_unparsed_group(self):
        self.make(teacher=self.teacher)
        body = self.client.get(
            reverse('admin:schedule_lesson_changelist')).content.decode()
        self.assertIn('не разобрана', body)

    def test_list_has_no_raw_none(self):
        """Колонка группы не должна показывать None."""
        self.make(teacher=self.teacher)
        body = self.client.get(
            reverse('admin:schedule_lesson_changelist')).content.decode()
        self.assertNotIn('>None<', body)

    def test_delete_is_blocked_for_imported_record(self):
        """Такая запись хранит текст ячейки — удалять её нельзя.

        Это последняя копия того, что было в файле. Удаление прошло бы
        молча, и понять, что строку не поняли при разборе, уже не
        получится.
        """
        from django.contrib.admin.sites import site
        from schedule.models import Lesson as LessonModel

        lesson = self.make(teacher=self.teacher)
        request = self.client.get(
            reverse('admin:schedule_lesson_changelist')).wsgi_request
        model_admin = site._registry[LessonModel]

        self.assertFalse(model_admin.has_delete_permission(request, lesson))
        self.assertFalse(
            self.client.get(
                reverse('admin:schedule_lesson_delete', args=[lesson.pk])
            ).status_code == 200,
            'страница удаления импортной записи должна быть недоступна',
        )

    def test_delete_still_allowed_for_regular_lesson(self):
        """Обычную запись удалять по-прежнему можно."""
        from django.contrib.admin.sites import site
        from schedule.models import Lesson as LessonModel

        lesson = Lesson.objects.create(
            group=self.group, subject=self.subject, day=1,
            lesson_time=self.lesson_time, teacher=self.teacher,
        )
        request = self.client.get(
            reverse('admin:schedule_lesson_changelist')).wsgi_request
        model_admin = site._registry[LessonModel]
        self.assertTrue(model_admin.has_delete_permission(request, lesson))

    def test_view_on_site_link_present(self):
        """Ссылка на сайте присутствует и не ведёт в 500."""
        lesson = self.make(teacher=self.teacher)
        response = self.client.get(
            reverse('admin:schedule_lesson_change', args=[lesson.pk]))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('viewsitelink', body)