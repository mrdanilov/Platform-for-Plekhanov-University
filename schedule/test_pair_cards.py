"""Раскладка фильтра «Пары»: сетка карточек вместо пустых колонок."""

import re
import pathlib
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

    def test_pair_cards_show_time_in_header(self):
        """Время пары — в заголовке дня, а не в каждой карточке."""
        html = self.client.get(f'/?view=pair&id={self.pair}').content.decode()
        self.assertIn('01:00–01:45', html)
        self.assertNotIn('lesson-time', html,
                         'время вернулось в карточку')

    def test_substitution_flag_on_card(self):
        lesson = Lesson.objects.filter(day=1, lesson_time=self.times[0]).first()
        lesson.substitutions.create(date=date(2026, 10, 5),
                                    teacher=self.teacher, reason='болел')
        html = self.client.get(
            f'/?view=pair&id={self.pair}&week=2026-W41').content.decode()
        self.assertIn('lesson-card changed', html)
        self.assertIn('замена', html)
        self.assertNotIn('болел', html,
                         'причина замены на карточке не показывается')


class TimeLivesInTheHeaderTests(TestCase):
    """Время пары — в шапке, а не в каждой карточке.

    Раньше время дублировалось внутри карточки занятия. В таблице оно и так
    стоит в шапке колонки вместе с номером пары, а в режиме «Пары» — в
    заголовке дня. Второе место для одних и тех же цифр занимало строку в
    каждой карточке и делало сетку выше без единой новой информации.

    Проверяется, что в шапке время есть, а в карточках его нет.
    """

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

    def test_table_header_carries_the_time(self):
        """В таблице время стоит в шапке колонки пары."""
        html = self._html(f'/?view=group&id={self.group.pk}')
        self.assertIn('09:30', html)
        self.assertIn('11:00', html)

    def test_cards_header_carries_the_time(self):
        """В режиме «Пары» время стоит в заголовке дня."""
        html = self._html(f'/?view=pair&id={self.morning.pk}')
        self.assertIn('09:30–10:50', html)

    def test_cards_do_not_repeat_the_time(self):
        """В карточках занятий времени нет ни разу."""
        for view, pk in (('group', self.group.pk), ('teacher', self.teacher.pk),
                         ('room', self.room.pk)):
            with self.subTest(вкладка=view):
                html = self._html(f'/?view={view}&id={pk}')
                self.assertNotIn('lesson-time', html,
                                 'время вернулось в карточку')

    def test_empty_cells_have_no_time(self):
        self.assertNotIn('lesson-time', self._html('/'))

    def test_footer_still_shows_the_room(self):
        """Аудитория на месте: подвал не должен был исчезнуть вместе со
        временем."""
        html = self._html(f'/?view=group&id={self.group.pk}')
        foot = re.search(r'<div class="lesson-foot">(.*?)</div>', html, re.S)
        self.assertIsNotNone(foot, 'подвала с аудиторией нет')
        self.assertIn('205', foot.group(1))
        self.assertNotIn('lesson-time', foot.group(1))


class LessonTimeStyleTests(TestCase):
    def setUp(self):
        from django.contrib.staticfiles import finders

        with open(finders.find('schedule/style.css'), encoding='utf-8') as fh:
            self.css = fh.read()

    def test_time_is_not_in_the_footer(self):
        """Подвал несёт только аудиторию — отдельного правила для времени
        в нём быть не должно."""
        self.assertIsNone(
            re.search(r'\.lesson-foot \.lesson-time', self.css),
            'правило для времени в подвале осталось, а самой плашки '
            'с временем уже нет')

    def test_time_in_header_does_not_wrap(self):
        """Время в шапке не переносится на две строки."""
        block = re_search(self.css, '.cards-time')
        self.assertIn('white-space: nowrap', block,
                      'время в заголовке дня переноситься не должно')
        self.assertIn('white-space: nowrap',
                      re_search(self.css, '.pair-time'),
                      'время в шапке колонки переноситься не должно')

    def test_time_in_header_is_readable(self):
        """Время в шапке читаемо: кегль не меньше 11px."""
        for selector in ('.cards-time', '.pair-time'):
            with self.subTest(селектор=selector):
                body = re_search(self.css, selector)
                size = float(re.search(
                    r'font-size:\s*([\d.]+)px', body).group(1))
                self.assertGreaterEqual(
                    size, 11.0,
                    f'{selector}: {size}px — время в шапке стало нечитаемым')

    def test_time_colour_is_not_lost_in_dark_theme(self):
        """Тёмная тема не перекрашивает время в шапке."""
        dark = re.search(
            r'html\[data-theme="dark"\][^{]*\{[^}]*\.cards-time[^}]*\}',
            self.css, re.S)
        self.assertIsNone(dark, 'время не должно перекрашиваться в тёмной теме')

    def test_cards_are_positioning_context(self):
        match = re.search(
            r'table\.sheet-table \.lesson,\s*\n?\.lesson-card\s*\{([^}]*)\}', self.css)
        self.assertIsNotNone(match, 'нет общего правила position: relative')
        self.assertIn('position: relative', match.group(1),
                      'плашка «замена» лежит поверх карточки и без этого '
                      'уедет за её пределы')

    def test_footer_holds_room_only(self):
        """В подвале только аудитория, и больше ничего.

        Время из карточек убрано: в таблице оно стоит в шапке колонки, в
        режиме «Пары» — в заголовке дня. Правил на `.lesson-time` в стилях
        быть не должно: элемента в разметке уже нет, а оставшиеся правила
        вводили бы в заблуждение при следующей правке.
        """
        self.assertIsNone(
            re.search(r'\.lesson-time', self.css),
            'стили .lesson-time остались, хотя элемента уже нет')


    def test_footer_has_no_own_background(self):
        """У плашки нет своей заливки — видно родной цвет карточки.

        Цвет приходит из базы (Subject.color) и может быть любым, поэтому
        плашка не должна ни подмешивать свой оттенок, ни наследовать его
        явно: достаточно остаться прозрачной поверх заливки карточки.
        """
        block = re_search(self.css, '.lesson-foot')
        self.assertRegex(block, r'background:\s*transparent')
        fill = re.search(r'background(?:-color)?:\s*([^;]+);', block).group(1)
        self.assertNotIn('255', fill,
                         'белый слой бледнит плашку')
        self.assertNotIn('0, 0, 0', fill,
                         'чёрный слой даёт грязно-серый на пастели')

    def test_footer_is_saturated_not_pale(self):
        """Плашка сочнее карточки, а не грязнее и не бледнее.

        Обе попытки через полупрозрачный слой провалились по-разному: белый
        высветлял, чёрный давал грязь. Насыщенность набирается пересчётом
        самой подложки — backdrop-filter берёт то, что нарисовано под
        элементом, и усиливает именно фон.
        """
        block = re_search(self.css, '.lesson-foot')
        self.assertRegex(block, r'backdrop-filter:\s*saturate\(\s*(\d+)%\s*\)')
        boost = float(re.search(r'backdrop-filter:[^;]*?saturate\(\s*(\d+)%\s*\)',
                                block).group(1))
        self.assertGreaterEqual(boost, 150.0,
                                'насыщенность не усилена — плашка бледная')
        self.assertNotRegex(block, r'(?<!-)\bfilter:\s',
                             'filter исказит текст вместе с фоном')

    def test_footer_has_webkit_fallback(self):
        """Safari и старые Chrome знают только -webkit-backdrop-filter."""
        block = re_search(self.css, '.lesson-foot')
        self.assertIn('-webkit-backdrop-filter:', block)

    def test_card_background_reaches_under_the_footer(self):
        """Под плашкой есть что пересчитывать.

        Цвет карточки задан инлайном и заливает весь блок, нижний отступ
        под плашку входит в эту заливку. Если бы фон стоял на фоне ячейки,
        подложкой был бы белый лист и насыщенность бралась бы не из чего.
        """
        templates = pathlib.Path(__file__).resolve().parent / 'templates' / 'schedule'
        for template in ('_block.html', '_cards.html'):
            with self.subTest(шаблон=template):
                source = (templates / template).read_text(encoding='utf-8')
                self.assertRegex(source, r'style="background: \{\{')
                self.assertIn('lesson-foot', source)

    def test_footer_stretches_to_card_width(self):
        """Подвал занимает всю ширину карточки и прижат к низу.

        Абсолютное положение обходит внутренние отступы карточки. Из-за
        этого плашка вне потока, и место под неё обязано резервировать
        нижний отпуск карточки: иначе она накроет текст.
        """
        block = re_search(self.css, '.lesson-foot')
        self.assertRegex(block, r'position:\s*absolute')
        self.assertRegex(block, r'left:\s*0')
        self.assertRegex(block, r'bottom:\s*0')
        self.assertRegex(block, r'width:\s*100%')
        self.assertIn('box-sizing: border-box', block,
                      'без этого рамка и отступы прибавятся к высоте, '
                      'и подвал станет выше задуманного')
        for selector in ('table.sheet-table .lesson', '.lesson-card'):
            with self.subTest(селектор=selector):
                foot_h = float(re.search(
                    r'--lesson-foot-h:\s*([\d.]+)px', self.css).group(1))
                padding = re.search(
                    r'padding:\s*([\d.]+px)\s+[\d.]+px\s+([\d.]+)px\s*;',
                    re_search(self.css, selector))
                self.assertIsNotNone(padding, f'у {selector} нет отступов')
                self.assertGreaterEqual(
                    float(padding.group(2)), foot_h,
                    f'{selector}: снизу {padding.group(2)}, подвалу нужно '
                    f'{foot_h}px — он накроет текст',
                )

    def test_footer_is_rounded_on_all_sides(self):
        """Плашка скруглена со всех сторон.

        Раньше скругление было только снизу, под форму карточки. С прозрачной
        заливкой и рамкой это выглядит обрубком: сверху и по бокам углы
        оставались острыми. Теперь радиус общий.
        """
        block = re_search(self.css, '.lesson-foot')
        self.assertRegex(block, r'border-radius:\s*4px')
        self.assertNotRegex(block, r'border-radius:\s*0\s+0',
                            'скругление только снизу — рамка будет угловатой')

    def test_footer_has_a_soft_border(self):
        """Рамка тонкая и полупрозрачная.

        Сплошной чёрный на пастели читается как гвоздь; мягкий контур лишь
        отделяет плашку. Задано одним правилом, поэтому таблица и карточки
        не разъезжаются.
        """
        block = re_search(self.css, '.lesson-foot')
        border = re.search(r'border:\s*([^;]+);', block)
        self.assertIsNotNone(border, 'у плашки нет рамки')
        spec = border.group(1)
        width = float(re.search(r'([\d.]+)px', spec).group(1))
        self.assertLessEqual(width, 2.0, f'рамка {spec} слишком толстая')
        rgba = re.search(
            r'rgba\(\s*0\s*,\s*0\s*,\s*0\s*,\s*([\d.]+)\s*\)', spec)
        self.assertIsNotNone(rgba, f'рамка {spec} не полупрозрачная — '
                                 'на пастели будет выглядеть гвоздём')
        alpha = float(rgba.group(1))
        self.assertGreater(alpha, 0, 'полностью прозрачная рамка не видна')
        self.assertLessEqual(alpha, 0.35,
                             f'прозрачность {alpha} — контур почти чёрный')

    def test_footer_centres_the_room(self):
        """Аудитория в подвале по центру, врозь от времени."""
        block = re_search(self.css, '.lesson-foot')
        self.assertIn('text-align: center', block)
        self.assertIn('display: flex', block)
        self.assertIn('align-items: center', block)
        self.assertIn('justify-content: center', block)

    def test_room_number_is_bold(self):
        """Номер аудитории выделен.

        Плашка — единственное место, где видна аудитория, и текст в ней
        мелкий: без веса он теряется на насыщенном фоне.
        """
        block = re_search(self.css, '.lesson-foot')
        self.assertRegex(block, r'font-weight:\s*(600|700|bold)')

    def test_room_number_is_dark_enough(self):
        """Номер аудитории заметен на насыщенном фоне плашки.

        Плашка усиливается backdrop-filter, и прежний светло-серый на ней
        бледнел. Проверяется не только цвет, но и контраст к самой светлой
        пастели палитры: под насыщенным фоном он падает.
        """
        from schedule.test_ui import contrast
        from schedule.exports import SUBJECT_COLORS

        block = re_search(self.css, '.lesson-foot')
        colour = re.search(r'color:\s*(#[0-9a-fA-F]{6})', block).group(1)
        lightest = max(SUBJECT_COLORS, key=lambda c: sum(
            int(c.lstrip('#')[i:i + 2], 16) for i in (0, 2, 4)))
        self.assertGreaterEqual(
            contrast(colour, lightest), 4.5,
            f'{colour} на пастели {lightest} — контраст ниже AA для мелкого '
            'текста',
        )

    def test_border_does_not_squeeze_the_text(self):
        """Рамка не выдавливает текст за границы плашки.

        Высота фиксирована и включает рамку, поэтому вертикальные отступы
        пришлось урезать: при прежних 4px на надпись оставалось 9px при
        кегле 11px, и строка наезжала бы на рамку.
        """
        block = re_search(self.css, '.lesson-foot')
        height = float(re.search(r'--lesson-foot-h:\s*([\d.]+)px',
                                 self.css).group(1))
        size = float(re.search(r'font-size:\s*([\d.]+)px', block).group(1))
        border = float(re.search(r'border:\s*([\d.]+)px', block).group(1))
        pad = re.search(r'padding:\s*([\d.]+)px\s+[\d.]+px', block)
        vertical = float(pad.group(1)) * 2
        free = height - border * 2 - vertical
        self.assertGreaterEqual(
            free, size * 1.15,
            f'на текст остаётся {free}px при кегле {size}px — надпись '
            'налезет на рамку',
        )

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

    def test_reason_is_not_rendered(self):
        """Причина замены в публичном расписании не показывается.

        Это личная информация преподавателя — болезнь, семейные обстоятельства.
        В расписании остаётся только метка «замена». Проверяется и разметка, и
        стили: если бы стиль остался, а разметка исчезла, причина вернулась бы
        при следующей правке шаблона.
        """
        self.assertNotIn('.lesson-card .reason', self.css)
        self.assertNotIn('.lesson-meta .reason', self.css)

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