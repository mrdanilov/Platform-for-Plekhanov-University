"""Кнопки растяжки должны быть видны в списке занятий.

Проверяется не «метод написан», а «кнопка есть на странице»: действия
Django молча отбрасывает, если метода нет в том классе, чей список
рисуется. Именно так они потерялись однажды — объявленные в другом
классе, они выглядели написанными, а в интерфейсе отсутствовали.
"""

import re

from django.contrib import admin as dj_admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from schedule.models import (
    Group,
    Lesson,
    LessonTime,
    Subject,
    Teacher,
)


class LessonActionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.slot = LessonTime.objects.create(number=1, start='09:00',
                                             end='10:20')
        # Вторая пара нужна, чтобы было куда растягивать: растяжка упирается
        # в конец таблицы, а таблица — это список пар.
        cls.next_slot = LessonTime.objects.create(number=2, start='10:30',
                                                  end='11:50')
        cls.subject = Subject.objects.create(name='Физика', short_name='Физ')
        cls.group = Group.objects.create(name='6301', course=1)
        cls.teacher = Teacher.objects.create(full_name='Холбаева С.Р.')
        for day in range(1, 5):
            Lesson.objects.create(group=cls.group, subject=cls.subject,
                                  lesson_time=cls.slot, day=day,
                                  teacher=cls.teacher)

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            'boss', 'boss@example.com', 'pass-12345678')
        self.client.force_login(self.user)

    def options(self):
        html = self.client.get('/admin/schedule/lesson/').content.decode()
        return [label.strip() for _value, label in re.findall(
            r'<option value="([^"]+)"[^>]*>([^<]*)</option>', html)]

    def test_stretch_buttons_are_registered(self):
        model_admin = dj_admin.site._registry[Lesson]
        request = RequestFactory().get('/admin/schedule/lesson/')
        request.user = self.user
        self.assertIn('stretch_by_two', model_admin.get_actions(request))
        self.assertIn('stretch_by_three', model_admin.get_actions(request))
        self.assertIn('collapse_lessons', model_admin.get_actions(request))

    def test_stretch_buttons_are_visible_on_the_page(self):
        """Главная проверка: кнопка есть там, где сотрудница её ищет."""
        options = self.options()
        self.assertIn('Растянуть отмеченные на 2 пары', options)
        self.assertIn('Растянуть отмеченные на 3 пары', options)
        self.assertIn('Свернуть отмеченные в одну пару', options)

    def test_existing_actions_survived(self):
        options = self.options()
        self.assertIn('Сдвинуть день недели +1', options)
        self.assertIn('Удалить выбранные Занятия', options)

    def test_actions_are_shown_above_the_table(self):
        """Панель действий под сотней строк не находят никогда."""
        html = self.client.get('/admin/schedule/lesson/').content.decode()
        bar = html.find('name="action"')
        table = html.find('id="result_list"')
        self.assertNotEqual(bar, -1, 'панель действий не отрисована')
        self.assertNotEqual(table, -1, 'таблица не отрисована')
        self.assertLess(bar, table,
                        'панель действий должна быть над таблицей, а не под '
                        'ней — под 482 строками её не видно')

    def test_span_column_shows_length(self):
        from schedule.spans import stretch
        lesson = Lesson.objects.order_by('pk').first()
        stretch(lesson, 2)
        html = self.client.get('/admin/schedule/lesson/').content.decode()
        self.assertIn('2 пары', html)