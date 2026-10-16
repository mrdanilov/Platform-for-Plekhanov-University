"""Короткие названия дней недели: «Пн», «Вт», «Ср», «Чт», «Пт», «Сб».

Раньше в трёх местах стояла обрезка ``имя[:2]``. По буквам начала слова она
давала «По», «Че», «Пя» и «Су» — четверг и пятница выглядели не как
дни недели, а как опечатки. Проверяются все места, где короткая форма
показывается пользователю: страница дня, сетка, выгрузка и админка.
"""

import re

from django.test import TestCase
from django.urls import reverse

from config.settings import WEEKDAYS
from schedule.admin import LessonAdmin, SubstitutionAdmin
from schedule.grid import build_days
from schedule.i18n import COOKIE_NAME, RU, UZ, day_short
from schedule.models import Group, Lesson, LessonTime, Subject, Teacher

EXPECTED_RU = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб']
EXPECTED_UZ = ['Du', 'Se', 'Ch', 'Pa', 'Ju', 'Sh']


class DayAbbreviationTests(TestCase):
    """Сама функция и то, что она не делает обрезку."""

    def test_russian_forms(self):
        got = [day_short(num) for num, _ in WEEKDAYS]
        self.assertEqual(got, EXPECTED_RU)

    def test_uzbek_forms(self):
        got = [UZ[f'dow_{num}'] for num, _ in WEEKDAYS]
        self.assertEqual(got, EXPECTED_UZ)

    def test_translated_by_request_language(self):
        """С request берётся язык из cookie, без него — русский."""
        request = type('R', (), {'COOKIES': {}})()
        self.assertEqual(day_short(4, request), 'Чт')
        request.COOKIES = {COOKIE_NAME: 'uz'}
        self.assertEqual(day_short(4, request), 'Pa')

    def test_not_a_prefix_of_the_full_name(self):
        """Главная проверка: это не обрезка названия.

        Для вторника и среды обрезка случайно совпадает с правильной формой,
        поэтому сравнивать списки целиком — иначе тест проверял бы совпадение
        там, где ошибки нет. Списки обязаны различаться хотя бы в четверге.
        """
        nums = [num for num, _ in WEEKDAYS]
        correct = [day_short(num) for num in nums]
        truncated = [RU[f'day_{num}'][:2] for num in nums]
        self.assertNotEqual(
            correct, truncated,
            'короткие формы совпадают с обрезкой — значит словарь не читается',
        )
        for num, name in zip(nums, correct):
            with self.subTest(day=num):
                self.assertEqual(len(name), 2, f'«{name}» должен быть двухбуквенным')
                self.assertTrue(
                    name.startswith(RU[f'day_{num}'][0]),
                    f'«{name}» должен начинаться с той же буквы, '
                    f'что «{RU[f"day_{num}"]}»',
                )

    def test_wednesday_is_cht_and_friday_is_pt(self):
        """То, что было видно на скриншоте."""
        self.assertEqual(day_short(4), 'Чт')
        self.assertEqual(day_short(5), 'Пт')

    def test_every_day_has_a_key_in_both_dictionaries(self):
        for num, _ in WEEKDAYS:
            for table in (RU, UZ):
                self.assertIn(f'dow_{num}', table)


class DayAbbreviationRenderedTests(TestCase):
    """Короткие формы там, где их видит человек."""

    def setUp(self):
        self.time = LessonTime.objects.create(number=1, start='08:30', end='09:50')
        self.group = Group.objects.create(name='ЭК-01', course=2)
        self.teacher = Teacher.objects.create(full_name='Петров И.Р.')
        Lesson.objects.create(
            group=self.group, subject=Subject.objects.create(name='Финансы'),
            teacher=self.teacher, day=1, lesson_time=self.time,
        )

    def test_day_view_tabs(self):
        """Страница «Расписание на сегодня» — здесь были «Че» и «Пя».

        В шаблоне вкладка набрана как `{{ d.short }} {{ d.date_label }}`,
        поэтому короткая форма идёт в тексте узла, а не отдельным тегом.
        """
        html = self.client.get(reverse('schedule:day'), {'day': 1}).content.decode()
        found = re.findall(r'>\s*([А-Яа-я]{2})\s+\d{2}\.\d{2}', html)
        self.assertEqual(found, EXPECTED_RU,
                         'вкладки дней содержат обрезанные названия')
        for bad in ('>Че ', '>Пя ', '>По ', '>Су '):
            self.assertNotIn(bad, html)

    def test_day_view_tabs_follow_language(self):
        self.client.cookies[COOKIE_NAME] = 'uz'
        html = self.client.get(reverse('schedule:day'), {'day': 1}).content.decode()
        found = re.findall(r'>\s*([A-Za-z]{2})\s+\d{2}\.\d{2}', html)
        self.assertEqual(found, EXPECTED_UZ)

    def test_grid_short_names(self):
        days = build_days()
        self.assertEqual([d['short'] for d in days], EXPECTED_RU)

    def test_grid_short_names_follow_language(self):
        request = type('R', (), {'COOKIES': {COOKIE_NAME: 'uz'}})()
        days = build_days(request)
        self.assertEqual([d['short'] for d in days], EXPECTED_UZ)

    def test_admin_columns(self):
        """В админке обрезка стояла в двух разных классах."""
        lesson = type('L', (), {'day': 4})()
        self.assertEqual(LessonAdmin.day_short(None, lesson), 'Чт',
                         'колонка «День» у занятия')
        substitution = type('S', (), {
            'lesson': type('L2', (), {'day': 5})(),
        })()
        self.assertEqual(SubstitutionAdmin.day_short(None, substitution), 'Пт',
                         'колонка «День» у замены')
