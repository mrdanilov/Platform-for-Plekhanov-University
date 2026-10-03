"""Раскладка фильтра «Пары»: сетка карточек вместо пустых колонок."""

import re
from datetime import date

from django.test import TestCase
from django.urls import reverse

from schedule.grid import build_grid
from schedule.models import (
    Group,
    Lesson,
    LessonTime,
    Room,
    Subject,
    Teacher,
)


class PairCardsLayoutTests(TestCase):
    """Пара выбрана одна: колонок с занятиями тоже одна, таблица бессмысленна."""

    @classmethod
    def setUpTestData(cls):
        cls.teacher = Teacher.objects.create(full_name='Петров П.П.')
        cls.room = Room.objects.create(name='101')
        cls.subject = Subject.objects.create(name='Математика', short_name='Матем',
                                             color='#cfe3ff')
        cls.other = Subject.objects.create(name='История', short_name='Ист',
                                          color='#ffe0c2')
        cls.group = Group.objects.create(name='Э-11', course=1)
        cls.group2 = Group.objects.create(name='Э-12', course=1)
        cls.times = [
            LessonTime.objects.create(number=n, start=f'0{n}:00',
                                      end=f'0{n}:45')
            for n in range(1, 4)
        ]
        # по пять занятий в первой и во второй паре — по разным дням
        for day in range(1, 6):
            for lt in (cls.times[0], cls.times[1]):
                Lesson.objects.create(
                    group=cls.group if day % 2 else cls.group2,
                    subject=cls.subject if lt.number == 1 else cls.other,
                    lesson_time=lt, day=day,
                    teacher=cls.teacher, room=cls.room,
                )
        cls.pair = cls.times[0].pk
        cls.monday = date(2026, 10, 5)   # понедельник той же чётности

    def _grid(self, *, pair_only=False, **kwargs):
        """Сетка. pair_only повторяет реальный запрос вида «Пары»:
        представление фильтрует по выбранной паре заранее."""
        queryset = Lesson.objects.all()
        if pair_only:
            queryset = queryset.filter(lesson_time_id=self.pair)
        return build_grid(queryset=queryset, week_monday=self.monday, **kwargs)

    # ------------------------------------------------------------ сетка

    def test_pair_view_asks_for_cards(self):
        grid = self._grid(pair_only=True, cards=True)
        self.assertTrue(grid['cards_mode'], 'для «Пар» нужны карточки')
        self.assertEqual(grid['filled_times'], [self.pair])
        self.assertEqual(grid['card_time'], self.pair)
        self.assertTrue(grid['has_cards'])

    def test_table_mode_without_cards_flag(self):
        """Группа с одной парой в неделю сохраняет привычную таблицу."""
        grid = self._grid(pair_only=True, cards=False)
        self.assertFalse(grid['cards_mode'])

    def test_cards_need_exactly_one_filled_pair(self):
        """Две непустые пары — это уже таблица, карточки не подходят.

        Фильтр «Пары» такого не создаёт: он отбирает одну пару заранее.
        Проверка защищает от включения карточек там, где колонок несколько.
        """
        grid = self._grid(cards=True)
        self.assertEqual(len(grid['filled_times']), 2)
        self.assertFalse(grid['cards_mode'])

    def test_empty_pair_falls_back_to_table(self):
        """У выбранной пары нет занятий: сетка карточек была бы пустой."""
        empty = build_grid(queryset=Lesson.objects.none(),
                           week_monday=self.monday, cards=True)
        self.assertEqual(empty['filled_times'], [])
        self.assertFalse(empty['cards_mode'])
        self.assertFalse(empty['has_cards'])

    # ------------------------------------------------------- разметка

    def test_pair_page_renders_cards(self):
        response = self.client.get(f'/?view=pair&id={self.pair}')
        html = response.content.decode()

        self.assertNotIn('<table class="sheet-table"', html,
                         'таблица из восьми колонок в режиме «Пар» лишняя')
        self.assertNotIn('pair-head', html)
        self.assertEqual(html.count('class="cards-grid"'), 5, 'по одному блоку на день')
        self.assertEqual(html.count('lesson-card'), 5)

    def test_cards_show_group_and_room(self):
        html = self.client.get(f'/?view=pair&id={self.pair}').content.decode()
        self.assertIn('Э-11', html)
        self.assertIn('Э-12', html)
        self.assertIn('Петров', html)
        self.assertIn('101', html)
        # на карточке предмет выводится через label: сокращение, если задано
        self.assertIn('Матем', html)

    def test_cards_show_pair_header_with_time(self):
        html = self.client.get(f'/?view=pair&id={self.pair}').content.decode()
        self.assertIn('cards-pair', html)
        self.assertIn('cards-time', html)
        self.assertIn('01:00', html)

    def test_days_as_blocks_with_names(self):
        html = self.client.get(f'/?view=pair&id={self.pair}').content.decode()
        self.assertIn('cards-day-name', html)
        self.assertIn('cards-day-date', html)
        self.assertIn('Понедельник', html)

    def test_group_view_still_table(self):
        """Режим «Группы» не должен пострадать."""
        html = self.client.get(f'/?view=group&id={self.group.pk}').content.decode()
        self.assertIn('<table class="sheet-table"', html)
        self.assertEqual(html.count('class="day-head"'), 6)
        self.assertEqual(html.count('pair-head'), 3)
        self.assertNotIn('cards-grid', html)

    def test_teacher_view_still_table(self):
        html = self.client.get(
            f'/?view=teacher&id={self.teacher.pk}').content.decode()
        self.assertIn('<table class="sheet-table"', html)
        self.assertNotIn('cards-grid', html)

    def test_empty_pair_page_renders_table(self):
        free = LessonTime.objects.create(number=9, start='09:00', end='09:45')
        html = self.client.get(f'/?view=pair&id={free.pk}').content.decode()
        self.assertIn('<table class="sheet-table"', html)
        self.assertEqual(html.count('pair-head'), 4)
        self.assertNotIn('lesson-card', html)

    def test_block_fragment_also_uses_cards(self):
        """Фрагмент /block/ должен вести себя так же, иначе смена фильтра
        без перезагрузки покажет старую таблицу."""
        html = self.client.get(
            f'/block/?view=pair&id={self.pair}&week=2026-W41').content.decode()
        self.assertIn('cards-grid', html)
        self.assertNotIn('<table class="sheet-table"', html)

    # ------------------------------------------------------- время пары

    def test_pair_cards_show_time(self):
        html = self.client.get(f'/?view=pair&id={self.pair}').content.decode()
        self.assertEqual(html.count('lesson-time'), 5)
        self.assertIn('01:00–01:45', html)

    def test_substitution_flag_on_card(self):
        lesson = Lesson.objects.filter(day=1, lesson_time=self.times[0]).first()
        lesson.substitutions.create(date=date(2026, 10, 5),
                                    teacher=self.teacher, reason='болел')
        html = self.client.get(
            f'/?view=pair&id={self.pair}&week=2026-W41').content.decode()
        self.assertIn('lesson-card changed', html)
        self.assertIn('болел', html)


class LessonTimeOnCardsTests(TestCase):
    """Время пары внутри карточки — во всех режимах просмотра."""

    @classmethod
    def setUpTestData(cls):
        cls.teacher = Teacher.objects.create(full_name='Иванов И.И.')
        cls.room = Room.objects.create(name='205')
        cls.subject = Subject.objects.create(name='Физика', short_name='Физ',
                                             color='#d8f0dc')
        cls.group = Group.objects.create(name='Т-21', course=2)
        cls.morning = LessonTime.objects.create(number=1, start='09:30',
                                                end='10:50')
        cls.even = LessonTime.objects.create(number=2, start='11:00', end='12:20')
        for lt in (cls.morning, cls.even):
            for day in (1, 3):
                Lesson.objects.create(
                    group=cls.group, subject=cls.subject, lesson_time=lt,
                    day=day, teacher=cls.teacher, room=cls.room,
                )

    def _html(self, url):
        return self.client.get(url).content.decode()

    def test_group_view_cards_show_time(self):
        html = self._html(f'/?view=group&id={self.group.pk}')
        self.assertEqual(html.count('lesson-time'), 4)
        self.assertIn('09:30–10:50', html)
        self.assertIn('11:00–12:20', html)

    def test_teacher_view_cards_show_time(self):
        html = self._html(f'/?view=teacher&id={self.teacher.pk}')
        self.assertEqual(html.count('lesson-time'), 4)
        self.assertIn('09:30–10:50', html)

    def test_pair_view_cards_show_time(self):
        html = self._html(f'/?view=pair&id={self.morning.pk}')
        self.assertEqual(html.count('lesson-time'), 2)
        self.assertIn('09:30–10:50', html)

    def test_room_view_cards_show_time(self):
        html = self._html(f'/?view=room&id={self.room.pk}')
        self.assertEqual(html.count('lesson-time'), 4)
        self.assertIn('11:00–12:20', html)

    def test_time_matches_the_card_pair(self):
        """Время на карточке — от той же пары, а не всегда первой в списке.

        У группы две пары, по два дня каждая: значит каждое время встречается
        ровно два раза. Если бы время бралось не из той карточки, одно из
        значений не совпало бы с количеством.
        """
        html = self._html(f'/?view=group&id={self.group.pk}')
        self.assertEqual(html.count('09:30–10:50'), 2)
        self.assertEqual(html.count('11:00–12:20'), 2)

    def test_time_is_last_inside_card(self):
        """Время идёт после подписей: визуально оно в углу, а не в потоке."""
        html = self._html(f'/?view=group&id={self.group.pk}')
        start = html.index('class="lesson"')
        card = html[start:html.index('</div>', html.index('lesson-time', start))]
        self.assertIn('lesson-meta', card)
        self.assertLess(card.index('lesson-meta'), card.index('lesson-time'))

    def test_no_time_in_empty_cells(self):
        """Пустая ячейка не должна содержать время."""
        html = self._html('/')
        self.assertNotIn('lesson-time', html)


class LessonTimeStyleTests(TestCase):
    def setUp(self):
        from django.contrib.staticfiles import finders

        with open(finders.find('schedule/style.css'), encoding='utf-8') as fh:
            self.css = fh.read()

    def test_time_block_is_pinned_to_corner(self):
        block = re_search(self.css, '.lesson-time')
        self.assertIn('position: absolute', block)
        self.assertRegex(block, r'right:\s*\d+px')
        self.assertRegex(block, r'bottom:\s*\d+px')
        self.assertIn('white-space: nowrap', block,
                      'время не должно переноситься на две строки')

    def test_time_font_is_small_and_bold(self):
        block = re_search(self.css, '.lesson-time')
        self.assertRegex(block, r'font-size:\s*1[0-2]px')
        self.assertRegex(block, r'font-weight:\s*(600|700|bold)')

    def test_time_colour_is_muted(self):
        """Полупрозрачный тёмный: одинаково читается на заливке предмета."""
        block = re_search(self.css, '.lesson-time')
        self.assertRegex(block, r'color:\s*rgba\(')

    def test_cards_are_positioning_context(self):
        match = re.search(
            r'table\.sheet-table \.lesson,\s*\n?\.lesson-card\s*\{([^}]*)\}', self.css)
        self.assertIsNotNone(match, 'нет общего правила position: relative')
        self.assertIn('position: relative', match.group(1))

    def test_room_has_room_not_to_overlap(self):
        """Нижний отступ резервирует место под время в углу."""
        self.assertRegex(re_search(self.css, 'table.sheet-table .lesson'),
                         r'padding:\s*[\d.]+px\s+[\d.]+px\s+1[0-9]px',
                         'снизу должен быть больший отступ, чем сверху')
        self.assertRegex(re_search(self.css, '.lesson-card'),
                         r'padding:\s*[\d.]+px\s+[\d.]+px\s+1[0-9]px')
        # в узкой ячейке таблицы подписи не должны доезжать до времени
        self.assertRegex(re_search(self.css, 'table.sheet-table .lesson-meta'),
                         r'padding-right')

    def test_time_survives_dark_theme(self):
        """Цвет времени не переопределяется белым в тёмной теме."""
        dark = re.search(r'html\[data-theme="dark"\][^{]*\{[^}]*\.lesson-time[^}]*\}',
                         self.css, re.S)
        self.assertIsNone(dark, 'время не должно перекрашиваться в тёмной теме')


class PairCardsStyleTests(TestCase):
    def setUp(self):
        from django.contrib.staticfiles import finders

        with open(finders.find('schedule/style.css'), encoding='utf-8') as fh:
            self.css = fh.read()

    def test_grid_wraps_cards(self):
        block = re_search(self.css, '.cards-grid')
        self.assertIn('display: grid', block)
        self.assertIn('repeat(auto-fill', block,
                      'карточки должны переноситься, а не тянуться вниз')
        self.assertIn('gap:', block)

    def test_card_width_is_bounded(self):
        """Без minmax карточки растянутся во всю страницу нечитаемыми полосами."""
        block = re_search(self.css, '.cards-grid')
        self.assertRegex(block, r'minmax\(\d+px,\s*1fr\)')

    def test_card_styled(self):
        self.assertRegex(re_search(self.css, '.lesson-card'), r'border-radius')
        self.assertIn('.lesson-card .lesson-subject', self.css)
        self.assertIn('.lesson-card .reason', self.css)

    def test_today_highlighted(self):
        self.assertIn('.cards-day.is-today', self.css)

    def test_print_rules(self):
        print_block = self.css[self.css.index('@media print'):]
        self.assertIn('break-inside: avoid', print_block)
        self.assertIn('lesson-card', print_block)


def re_search(css, selector):
    """Базовое (первое) правило по селектору.

    Первое, а не последнее: внутри медиазапросов селектор повторяется с
    мобильными размерами, и последнее совпадение было бы уже мобильным.
    """
    match = re.search(re.escape(selector) + r'\s*\{([^}]*)\}', css)
    assert match, f'нет стилей {selector}'
    return match.group(1)