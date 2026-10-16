"""Чётность недели: считается от начала семестра, а не по календарю.

Здесь жил баг, который стоил целой недели показа: расписание вуза на 12–16
октября 2026 года помечено как нечётные недели (7-я неделя нечётного
семестра), а сайт считал 12 октября 42-й ISO-неделей календаря — чётной, и
прятал все 482 занятия именно в ту неделю, для которой они и сделаны.

Проверяется само правило и то, что оно не разъезжается с данными: неделя
из файла расписания обязана считаться нечётной.
"""

import datetime
from datetime import date, timedelta

from django.conf import settings
from django.test import SimpleTestCase

from schedule import grid


class ParityCountsFromSemesterTests(SimpleTestCase):
    def setUp(self):
        self.first = settings.SEMESTER_FIRST_MONDAY
        self.assertIsNotNone(
            self.first,
            'дата начала семестра не задана — чётность посчитается по '
            'ISO-неделе календаря и расписание снова будет скрываться',
        )

    def test_first_week_is_odd(self):
        self.assertEqual(grid._parity(self.first), 1)

    def test_parity_alternates_every_week(self):
        previous = None
        for i in range(18):
            week = self.first + timedelta(weeks=i)
            current = grid._parity(week)
            self.assertIn(current, (1, 2))
            if previous is not None:
                self.assertNotEqual(
                    current, previous,
                    f'две недели подряд с одной чётностью: {week}',
                )
            previous = current

    def test_university_week_is_odd(self):
        """12 октября 2026 — неделя файла расписания, значит нечётная.

        Источник: файл вуза «7 неделя с 12 октября по 16 октября»,
        7-я неделя нечётного семестра.
        """
        self.assertEqual(
            grid._parity(date(2026, 10, 12)), 1,
            'неделя, на которую составлено расписание, должна быть нечётной',
        )

    def test_semester_week_numbers_match_the_file(self):
        """Отсчёт недель совпадает с нумерацией вуза.

        Первая неделя семестра — 31 августа 2026, значит 12 октября это
        седьмая. Если дату сдвинуть на неделю, расписание снова уедет на
        чётные недели.
        """
        semester_week = (date(2026, 10, 12) - self.first).days // 7 + 1
        self.assertEqual(semester_week, 7)

    def test_schedule_is_visible_on_its_own_week(self):
        """Занятия, помеченные нечётными, видны в неделю файла."""
        self.assertEqual(grid._parity(date(2026, 10, 12)), 1)

    def test_without_semester_start_falls_back_to_iso(self):
        """Без даты начала семестра остаётся прежнее поведение.

        Оно для вуза неверно, но полезно как запасной путь: приложение не
        должно падать, а дата начала семестра обязана быть в настройках.
        """
        from django.test import override_settings

        with override_settings(SEMESTER_FIRST_MONDAY=None):
            week = date(2026, 10, 5)   # ISO-неделя 41, нечётная
            self.assertEqual(grid._parity(week), 1)
            self.assertEqual(grid._parity(week + timedelta(weeks=1)), 2)

    def test_parity_does_not_use_iso_calendar(self):
        """Правило не должно опираться на номер ISO-недели.

        Именно эта строка была причиной: 12 октября — 42-я ISO-неделя, и
        по ней неделя считалась чётной.
        """
        iso_week_monday = date.fromisocalendar(2026, 42, 1)
        self.assertEqual(iso_week_monday, date(2026, 10, 12))
        self.assertEqual(
            grid._parity(date(2026, 10, 12)), 1,
            'неделя по ISO считается чётной, а расписание к ней относится',
        )