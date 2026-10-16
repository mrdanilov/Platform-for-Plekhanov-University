"""Меню, окно «Кабинеты» и служебная сводка в админке.

Кнопка выбора группы называется «Пары»: сотрудникам слово «пара» привычнее,
и сетка расписания идёт именно по парам. Срез по времени (бывшее «Пары»)
из меню убран — он отвечает на вопрос деканата, а не пользователя, и живёт
теперь в админке отдельной страницей.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from schedule.analytics import build_report
from schedule.models import (
    Group,
    Lesson,
    LessonTime,
    Room,
    Subject,
    Teacher,
)


class MenuTests(TestCase):
    def test_group_button_is_named_pairs(self):
        html = self.client.get('/').content.decode()
        self.assertIn('data-view="group"', html)
        self.assertIn('Пары<span class="menu-count">', html)

    def test_rooms_button_present(self):
        html = self.client.get('/').content.decode()
        self.assertIn('data-kind="room"', html)
        self.assertIn('data-view="room"', html)
        self.assertIn('Кабинеты', html)

    def test_time_slot_button_gone(self):
        """Среза по времени в меню нет — он уехал в админку."""
        html = self.client.get('/').content.decode()
        self.assertNotIn('data-view="pair"', html)
        self.assertNotIn('data-kind="pair"', html)

    def test_picker_dialog_still_says_groups(self):
        """Кнопка «Пары», а окно выбора — «Группы»: выбираем именно группу."""
        html = self.client.get('/').content.decode()
        self.assertIn('data-kind="group" data-view="group" '
                      'data-title="Группы"', html)


class RoomPickerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.room = Room.objects.create(name='201', building='Главный')
        cls.other = Room.objects.create(name='302', capacity=45)

    def test_rooms_are_listed(self):
        html = self.client.get('/list/?kind=room').content.decode()
        self.assertIn('201', html)
        self.assertIn('302', html)

    def test_building_and_capacity_shown(self):
        html = self.client.get('/list/?kind=room').content.decode()
        self.assertIn('Главный', html)
        self.assertIn('45', html)

    def test_search_by_room_name(self):
        Room.objects.create(name='спорт зал', building='Спортзал')
        html = self.client.get('/list/?kind=room&q=спорт').content.decode()
        self.assertIn('спорт зал', html)
        self.assertNotIn('>201<', html)

    def test_no_course_filter_for_rooms(self):
        """У аудитории нет курса: кнопки курсов были бы пустыми."""
        Group.objects.create(name='6301', course=1)
        html = self.client.get('/list/?kind=room').content.decode()
        self.assertNotIn('picker-filters', html)


class RoomGridTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.slot = LessonTime.objects.create(number=1, start='09:00', end='10:20')
        cls.subject = Subject.objects.create(name='Матан', short_name='Матем',
                                             color='#cfe3ff')
        cls.teacher = Teacher.objects.create(full_name='Соколова С.Р.')
        cls.room = Room.objects.create(name='201')
        cls.group = Group.objects.create(name='6301', course=1)
        Lesson.objects.create(group=cls.group, subject=cls.subject,
                              lesson_time=cls.slot, day=1,
                              teacher=cls.teacher, room=cls.room)

    def test_room_view_shows_who_occupies_it(self):
        html = self.client.get(f'/block/?view=room&id={self.room.pk}').content.decode()
        self.assertIn('Матем', html)   # предмет выводится короткой меткой
        self.assertIn('6301', html)
        self.assertIn('Соколова', html)

    def test_room_not_repeated_in_its_own_footer(self):
        """Номер кабинета в подвале карточки лишний: окно и есть этот кабинет."""
        html = self.client.get(f'/block/?view=room&id={self.room.pk}').content.decode()
        foot = html.split('lesson-foot')[1]
        self.assertNotIn('201', foot.split('</div>')[0])

    def test_room_still_shown_in_group_view(self):
        html = self.client.get(f'/block/?view=group&id={self.group.pk}').content.decode()
        self.assertIn('201', html)


class AnalyticsTests(TestCase):
    """Сводка для деканата: видна сотруднику и не видна постороннему."""

    @classmethod
    def setUpTestData(cls):
        cls.slot = LessonTime.objects.create(number=1, start='09:00', end='10:20')
        cls.empty_slot = LessonTime.objects.create(number=2, start='10:30',
                                                   end='11:50')
        cls.subject = Subject.objects.create(name='Матан', short_name='Матем')
        cls.teacher = Teacher.objects.create(full_name='Соколова С.Р.')
        cls.room = Room.objects.create(name='201')
        cls.first = Group.objects.create(name='6301', course=1)
        cls.second = Group.objects.create(name='6303', course=1)
        for group in (cls.first, cls.second):
            Lesson.objects.create(group=group, subject=cls.subject,
                                  lesson_time=cls.slot, day=1,
                                  teacher=cls.teacher, room=cls.room)

    def setUp(self):
        # Суперпользователь: у сотрудника без прав на модель Django отдаёт
        # 403 на список занятий, и проверка ссылки попала бы не туда.
        self.user = get_user_model().objects.create_superuser(
            'boss', 'boss@example.com', 'pass-12345678',
        )

    def report(self):
        return build_report()

    def test_url_reachable_for_staff(self):
        self.client.force_login(self.user)
        resp = self.client.get('/admin/schedule/lesson/analytics/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Сводка по парам', resp.content.decode())

    def test_closed_for_anonymous(self):
        resp = self.client.get('/admin/schedule/lesson/analytics/')
        self.assertIn(resp.status_code, (302, 403))

    def test_combined_groups_counted_as_one_lesson(self):
        """Две группы на одной лекции — одно занятие, а не два."""
        self.assertEqual(self.report()['grand_total'], 1)

    def test_busiest_and_spread(self):
        report = self.report()
        self.assertEqual(report['busiest']['lt'].number, 1)
        self.assertEqual(report['quietest']['lt'].number, 2)
        self.assertEqual(report['spread'], 1)

    def test_missing_room_and_type_are_counted(self):
        report = self.report()
        self.assertEqual(report['no_room'], 0)
        # Тип не заполнен у обеих записей объединённой группы.
        self.assertEqual(report['no_type'], 2)

    def test_load_by_teacher(self):
        report = self.report()
        self.assertEqual(report['top_teachers'][0]['name'], 'Соколова С.Р.')
        self.assertEqual(report['top_teachers'][0]['count'], 1)

    def test_link_from_lesson_list(self):
        self.client.force_login(self.user)
        html = self.client.get('/admin/schedule/lesson/').content.decode()
        self.assertIn('Сводка по парам', html)
        self.assertIn('/admin/schedule/lesson/analytics/', html)