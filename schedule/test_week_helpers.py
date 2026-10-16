"""Правило недели определено ровно в одном месте.

Закрывает задачу 13 аудита. Логика недели — это одно и то же знание:
понедельник недели, номер недели в формате `YYYY-Www`, разбор параметра
запроса. Когда она написана дважды, копии разъезжаются при первой же правке:
тест считает неделю по-своему и проходит вхолостую, а представление — по
рабочей.

Проверяется не сам факт «функция где-то есть», а конкретные свойства:
реализация одна, формат параметра не изменился, копий в тестах нет.
"""

import re
from datetime import date, timedelta
from pathlib import Path

from django.test import SimpleTestCase
from django.utils import timezone

from schedule import grid, views

SCHEDULE_DIR = Path(__file__).resolve().parent

# Имена, которые должны жить в grid.py и нигде больше.
WEEK_NAMES = ('monday_of', 'iso_week', 'parse_week', 'week_param')


def sources():
    return {p.name: p.read_text(encoding='utf-8')
            for p in SCHEDULE_DIR.glob('*.py')}


class SingleSourceTests(SimpleTestCase):
    """Критерий приёмки: двух реализаций одной функции быть не должно."""

    def test_no_private_week_helpers_in_views(self):
        """Приватные копии с подчёркиванием удалены."""
        text = sources()['views.py']
        for name in WEEK_NAMES:
            with self.subTest(name=name):
                self.assertNotRegex(
                    text, rf'def\s+_{name}\s*\(',
                    f'в views.py осталась приватная копия _{name}',
                )

    def test_views_uses_public_names(self):
        text = sources()['views.py']
        self.assertNotRegex(text, r'\b_parse_week\b')
        self.assertNotRegex(text, r'\b_monday_of\b')
        self.assertNotRegex(text, r'\b_iso_week\b')

    def test_views_imports_from_grid(self):
        text = sources()['views.py']
        self.assertRegex(text, r'from \.grid import',
                         'views.py должен брать неделю из grid.py')
        for name in ('monday_of', 'parse_week', 'week_param'):
            with self.subTest(name=name):
                self.assertRegex(text, rf'\b{name}\b')

    def test_tests_do_not_redefine_week_helpers(self):
        """Копии в тестах опаснее всего: тест проходит, проверяя не то.

        Своя реализация в тесте означает, что проверка недели может стать
        успешной, даже если приложение считает неделю иначе.
        """
        for name, text in sources().items():
            if not name.startswith(('test_', 'tests')):
                continue
            for helper in WEEK_NAMES:
                with self.subTest(file=name, helper=helper):
                    self.assertNotRegex(
                        text, rf'def\s+{helper}\s*\(',
                        f'{name} определяет {helper} заново',
                    )

    def test_no_templates_contain_week_logic(self):
        """В шаблонах неделя приходит готовой строкой из контекста."""
        for template in SCHEDULE_DIR.glob('templates/**/*.html'):
            text = template.read_text(encoding='utf-8')
            with self.subTest(template=template.name):
                self.assertNotIn('isocalendar', text)
                self.assertNotIn('timedelta', text)

    def test_only_grid_defines_them(self):
        """Ровно одно место в приложении определяет эти имена."""
        found = {}
        for name, text in sources().items():
            for helper in WEEK_NAMES:
                if re.search(rf'^def\s+{helper}\s*\(', text, re.M):
                    found.setdefault(helper, []).append(name)
        self.assertEqual(
            found, {helper: ['grid.py'] for helper in WEEK_NAMES},
            f'имена определены не в одном месте: {found}',
        )

    def test_grid_has_no_dead_views_constant(self):
        """VIEWS остался в views.py — им пользуется проверка параметра.

        Копия в grid.py была мёртвой, но если её вернуть, то снова
        появятся два источника истины об одном перечне.
        """
        self.assertFalse(hasattr(grid, 'VIEWS'),
                         'grid.VIEWS — мёртвая копия, единственный источник '
                         'истины в views.py')
        self.assertEqual(views.VIEWS,
                         ('group', 'pair', 'teacher', 'room', 'substitution'))


class WeekParamFormatTests(SimpleTestCase):
    """Формат параметра не менялся: тесты и страницы завязаны на YYYY-Www."""

    def test_iso_week_format(self):
        self.assertEqual(grid.iso_week(date(2026, 10, 5)), '2026-W41')
        self.assertEqual(grid.iso_week(date(2026, 1, 1)), '2026-W01')

    def test_week_param_is_same_string(self):
        monday = date(2026, 10, 5)
        self.assertEqual(grid.week_param(monday), grid.iso_week(monday))

    def test_round_trip_through_parse_week(self):
        """Строка, которая уходит в ссылку, читается обратно в ту же неделю.

        Если формат поедет, ссылка «предыдущая неделя» начнёт вести не туда.
        """
        for monday in (date(2026, 10, 5), date(2026, 1, 5), date(2027, 12, 27)):
            with self.subTest(monday=monday):
                param = grid.week_param(monday)
                self.assertEqual(grid.parse_week(param), monday)

    def test_parse_week_accepts_lowercase_w(self):
        self.assertEqual(grid.parse_week('2026-w41'), date(2026, 10, 5))

    def test_parse_week_accepts_iso_date(self):
        self.assertEqual(grid.parse_week('2026-10-07'), date(2026, 10, 5))

    def test_garbage_falls_back_to_current_week(self):
        for value in ('ерунда', '', None, '2026-W99', '2026-WAA'):
            with self.subTest(value=value):
                self.assertEqual(
                    grid.parse_week(value),
                    grid.monday_of(timezone.localdate()),
                    'мусор в параметре должен давать текущую неделю, '
                    'а не исключение',
                )

    def test_monday_of_is_monday(self):
        for offset in range(7):
            with self.subTest(offset=offset):
                monday = grid.monday_of(date(2026, 10, 5) + timedelta(days=offset))
                self.assertEqual(monday.weekday(), 0)


class PublicContractTests(SimpleTestCase):
    """Имена публичные: без подчёркивания, потому что ими пользуются другие."""

    def test_grid_exports_public_names(self):
        for name in WEEK_NAMES:
            with self.subTest(name=name):
                self.assertTrue(hasattr(grid, name))
                self.assertTrue(callable(getattr(grid, name)))

    def test_views_reexports_through_import(self):
        """Имена в views.py — те же объекты, а не копии."""
        self.assertIs(views.monday_of, grid.monday_of)
        self.assertIs(views.parse_week, grid.parse_week)
        self.assertIs(views.week_param, grid.week_param)
