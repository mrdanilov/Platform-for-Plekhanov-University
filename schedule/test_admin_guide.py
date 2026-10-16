"""Админка должна объяснять, что «Время пар» — справочник, а не список уроков.

Путаница была реальной: сотрудница шла в «Время пар», чтобы «выбрать пару и
указать время», и не находила там уроков. Проверяем, что объяснение стоит
на всех трёх страницах, где человек может запутаться, и что ссылка ведёт
живьём, а не текстом.
"""

import re

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class LessonTimeGuideTests(TestCase):
    """Подсказки в разделе «Время пар» и в занятии."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('guide_admin', 'a@b.c', 'pw')

    def setUp(self):
        self.client.force_login(self.admin)

    def body(self, url):
        """Текст страницы с схлопнутыми переносами.

        В шаблонах текст разбит на строки для читаемости, поэтому пробел
        подряд не совпадёт с проверкой. HTML тут не проверяем — только
        наличие и разборность подсказок.
        """
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        return re.sub(r'\s+', ' ', page.content.decode())

    def test_pair_list_explains_it_is_a_reference_book(self):
        """На списке пар должно быть видно, что это не уроки."""
        body = self.body(reverse('admin:schedule_lessontime_changelist'))
        self.assertIn('Что это за раздел', body)
        self.assertIn('справочник', body)
        self.assertIn('Свои уроки сюда не добавляются', body)

    def test_pair_list_points_to_lessons_and_time_slots(self):
        """Список пар должен говорить и куда идти за уроком, и когда нужна новая пара."""
        body = self.body(
            reverse('admin:schedule_lessontime_changelist'))
        self.assertIn('новый интервал', body)
        self.assertIn('выпадающего списка', body)

    def test_pair_form_explains_what_to_do(self):
        """Форма пары тоже объясняет: время здесь, уроки — в занятиях."""
        body = self.body(reverse('admin:schedule_lessontime_add'))
        self.assertIn('Здесь задаётся только время пар', body)

    def test_link_to_lessons_resolves(self):
        """Ссылка «К занятиям» должна вести в живой раздел, а не быть текстом."""
        body = self.body(
            reverse('admin:schedule_lessontime_changelist'))
        lessons_url = reverse('admin:schedule_lesson_changelist')
        self.assertIn(lessons_url, body)
        self.assertEqual(self.client.get(lessons_url).status_code, 200)

    def test_lesson_form_says_pair_comes_from_reference_book(self):
        """В занятии подсказано, что «Пара» — это время из справочника."""
        from schedule.models import Group, Subject
        g = Group.objects.create(name='G1')
        s = Subject.objects.create(name='Физика')
        body = self.body(reverse('admin:schedule_lesson_add'))
        self.assertIn('время из справочника', body)
        self.assertIn(reverse('admin:schedule_lessontime_changelist'), body)

    def test_pair_choice_shows_number_and_hours(self):
        """В выпадающем списке пары должно быть видно и номер, и часы.

        Именно этого не хватало, чтобы понять, что выбираешь: голый номер
        «1» ничего не говорит, а «3 пара: 11:00–12:20» говорит всё.
        """
        import datetime as dt

        from schedule.models import LessonTime
        LessonTime.objects.create(number=3, start=dt.time(11, 0), end=dt.time(12, 20))
        body = self.body(reverse('admin:schedule_lesson_add'))
        self.assertIn('3 пара: 11:00–12:20', body)