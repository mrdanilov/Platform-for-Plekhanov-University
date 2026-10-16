"""Раскладка для широких экранов.

Проверяем главное: блок расписания на Full HD и крупнее не тонет,
а ноутбуки и телефоны продолжают получать старые правила.
"""

import re

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase

# Ширины ноутбуков, которые обязаны остаться нетронутыми.
LAPTOPS = (1280, 1366, 1440, 1512, 1536, 1600, 1728)

# Границы, на которых включается крупная раскладка
WIDE = 1800
ULTRA = 2400
MEGA = 3200
STEPS = (WIDE, ULTRA, MEGA)


def css_text():
    with open(finders.find('schedule/style.css'), encoding='utf-8') as fh:
        return fh.read()


class WideLayoutTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.css = css_text()

    def media(self, marker):
        """Тело @media-блока по маркеру, со счётом скобок."""
        start = self.css.index(marker)
        depth, i = 1, start + len(marker)
        while i < len(self.css) and depth:
            if self.css[i] == '{':
                depth += 1
            elif self.css[i] == '}':
                depth -= 1
            i += 1
        return self.css[start:i]

    def rule(self, selector, text=None):
        match = re.search(re.escape(selector) + r'\s*\{([^}]*)\}',
                          text if text is not None else self.css)
        self.assertIsNotNone(match, f'нет правила {selector}')
        return match.group(1)

    # ---------------------------------------------------- точки перелома

    def test_wide_breakpoints_exist(self):
        for width in STEPS:
            self.assertIn(f'@media (min-width: {width}px)', self.css)

    def test_no_min_width_below_laptops(self):
        """Главная гарантия: ноутбуки не задеты.

        Если появится min-width ниже 1728, ноутбук 16" на 1728px
        неожиданно получит крупную раскладку — значит, граница выбрана неверно.
        """
        widths = [int(x) for x in
                  re.findall(r'@media \(min-width: (\d+)px\)', self.css)]
        self.assertTrue(widths)
        for width in widths:
            self.assertGreaterEqual(
                width, max(LAPTOPS) + 1,
                f'правило min-width: {width}px заденет ноутбуки')

    def test_braces_balanced(self):
        self.assertEqual(self.css.count('{'), self.css.count('}'))

# ---------------------------------------------- контейнер расширяется

    def max_width_px(self, body):
        """Пиксельный потолок из max-width.

        Понимает оба вида записи: обычный `max-width: 1400px`
        и адаптивный `max-width: min(1880px, 96vw)`.
        """
        match = re.search(r'max-width:\s*(?:min\(\s*)?(\d+)px', body)
        self.assertIsNotNone(match, f'в max-width нет пикселей: {body!r}')
        return int(match.group(1))

    def container_rule(self, width=None):
        block = self.media(f'@media (min-width: {width}px)') if width else None
        return self.rule('.container', block)

    def test_container_grows_stepwise(self):
        base = self.max_width_px(self.container_rule())
        widths = [self.max_width_px(self.container_rule(w)) for w in STEPS]
        self.assertLess(base, widths[0], 'на широком экране контейнер должен расти')
        for earlier, later in zip(widths, widths[1:]):
            self.assertLess(earlier, later,
                            'каждая следующая ступень должна быть ещё шире')

    def test_container_not_flush_to_screen_edge(self):
        """На широких экранах нужен «запас» по бокам.

        Фиксированные пиксели на мониторе 2560 оставляли пустые поля,
        поэтому потолок задан в процентах от ширины экрана.
        """
        for width in STEPS:
            body = self.container_rule(width)
            with self.subTest(width=width):
                self.assertRegex(body, r'max-width:\s*min\(',
                                 'нужна связка min(px, vw)')
                match = re.search(r'max-width:\s*min\(\s*(\d+)px\s*,\s*(\d+)vw', body)
                self.assertIsNotNone(match)
                cap_px, cap_vw = int(match.group(1)), int(match.group(2))
                self.assertGreater(cap_vw, 90, 'контейнер не должен липнуть к краю')
                # при экране 2560 потолок должен упираться в vw, а не в px
                self.assertGreater(
                    2560 * cap_vw / 100, 1760,
                    'на 2560 контейнер снова станет прежним и пустота вернётся')
                self.assertGreater(cap_px, 1760, 'потолок в пикселях должен вырасти')

    def test_ultrawide_keeps_growing(self):
        """На 3440 предыдущая ступень упиралась в потолок и пустота возвращалась.

        Поэтому для широких экранов есть отдельная ступень с более
        высоким потолком, а не только большее значение vw.
        """
        prev = re.search(r'max-width:\s*min\(\s*(\d+)px\s*,\s*(\d+)vw',
                         self.container_rule(ULTRA))
        mega = re.search(r'max-width:\s*min\(\s*(\d+)px\s*,\s*(\d+)vw',
                         self.container_rule(MEGA))
        # доля экрана может совпадать — важнее потолок в пикселях
        self.assertGreater(int(mega.group(1)), int(prev.group(1)),
                           'потолок в пикселях не вырос — на ultrawide снова поля')
        # и на 3440 новая ступень обязана дать реальный выигрыш
        self.assertGreater(3440 * int(mega.group(2)) / 100,
                           int(prev.group(1)),
                           'на 3440 контейнер не отличается от предыдущей ступени')

    def test_base_container_untouched(self):
        """Базовое правило остаётся прежним — его правят только через медиазапрос."""
        body = self.rule('.container')
        self.assertIn('max-width: 1400px', body)
        self.assertIn('padding: 0 16px', body)

    # --------------------------------------------- таблица не разваливается

    def test_table_min_width_only_grows(self):
        base = int(re.search(r'min-width:\s*(\d+)px',
                             self.rule('table.sheet-table')).group(1))
        for width in STEPS:
            got = int(re.search(r'min-width:\s*(\d+)px',
                                self.rule('table.sheet-table',
                                          self.media(f'@media (min-width: {width}px)'))
                                ).group(1))
            with self.subTest(width=width):
                self.assertGreaterEqual(
                    got, base,
                    'min-width нельзя уменьшать: колонки начнут схлопываться')

    def test_table_still_fills_container(self):
        """width: 100% остаётся в силе — иначе таблица не растёт с экраном."""
        self.assertIn('width: 100%', self.rule('table.sheet-table'))

    def test_day_column_widens(self):
        base = int(re.search(r'width:\s*(\d+)px',
                             self.rule('table.sheet-table thead th.corner')
                             ).group(1))
        for width in STEPS:
            got = int(re.search(r'width:\s*(\d+)px',
                                self.rule('table.sheet-table thead th.corner',
                                          self.media(f'@media (min-width: {width}px)'))
                                ).group(1))
            with self.subTest(width=width):
                self.assertGreater(got, base)

    def test_day_and_corner_stay_equal(self):
        """Колонка дня и угловая ячейка должны быть одной ширины,
        иначе шапка разъедется с первой колонкой таблицы."""
        for width in STEPS:
            block = self.media(f'@media (min-width: {width}px)')
            corner = re.search(r'width:\s*(\d+)px',
                               self.rule('table.sheet-table thead th.corner', block)
                               ).group(1)
            day = re.search(r'width:\s*(\d+)px',
                            self.rule('table.sheet-table .day-head', block)
                            ).group(1)
            with self.subTest(width=width):
                self.assertEqual(corner, day,
                                 'ширина угловой ячейки и колонки дня разошлась')

    # ------------------------------------- место под время в правом нижнем углу

    def test_lesson_keeps_room_for_time(self):
        """Снизу у блока место резервировано под подвал на всех ступенях."""
        needed = float(re.search(
            r'--lesson-foot-h:\s*([\d.]+)px', css_text()).group(1))
        for width in STEPS:
            block = self.media(f'@media (min-width: {width}px)')
            rule = self.rule('table.sheet-table .lesson', block)
            values = re.search(
                r'padding:\s*([\d.]+px(?:\s+[\d.]+px){1,3})\s*;',
                rule).group(1).split()
            with self.subTest(width=width):
                self.assertGreaterEqual(
                    float(values[-1][:-2]), needed,
                    f'снизу {values[-1]}, а подвалу нужно {needed}px')

    def test_meta_keeps_clearance_from_time(self):
        """Подписи разделены со временем самим подвалом.

        Отступ справа у подписей по-прежнему не нужен: он лишь сужает
        колонку и рвёт ФИО. Разводить их теперь подвал — отдельный блок
        под текстом.
        """
        meta = self.rule('table.sheet-table .lesson-meta')
        self.assertNotIn('padding-right', meta,
                         'горизонтальный запас сужает колонку и рвёт ФИО')
        css = css_text()
        self.assertRegex(css, r'\.lesson-foot[^{]*\{[^}]*display:\s*flex',
                         'подвала нет — подписи и время снова слипнутся')


    # ------------------------------------------- размер самих ячеек урока

    def slot_rule(self, width=None):
        block = self.media(f'@media (min-width: {width}px)') if width else None
        return self.rule('table.sheet-table td.slot', block)

    def test_row_height_grows(self):
        """Ячейка пары должна стать выше, а не остаться узкой полосой."""
        base = int(re.search(r'(?:min-)?height:\s*(\d+)px',
                             self.slot_rule()).group(1))
        for width in STEPS:
            got = int(re.search(r'(?:min-)?height:\s*(\d+)px',
                                self.slot_rule(width)).group(1))
            with self.subTest(width=width):
                self.assertGreater(got, base)

    def test_row_height_uses_min_height(self):
        """min-height, а не height: длинный текст должен уметь растянуть ячейку."""
        for width in STEPS:
            with self.subTest(width=width):
                self.assertIn('min-height:', self.slot_rule(width))

    def test_lesson_padding_grows(self):
        """Боковые отступы растут с шириной окна, вертикальные — нет.

        Раньше проверялось, что растут все четыре стороны. Это неверно:
        верхний отступ — резерв под плашку «замена», а её кегль от ширины
        окна не зависит. Росли бы и нижние, а низ занят временем пары,
        размер которого тоже задан кеглем. Расти должен только боковой
        запас: чем шире колонка, тем больше воздуха вокруг текста.

        Проверка требовала лишнего и заставляла наращивать верхний отступ
        вместе с шириной — из-за чего предмет прыгал по вертикали при
        изменении размера окна.
        """
        base = self.lesson_padding(self.rule('table.sheet-table .lesson'))
        for width in STEPS:
            got = self.lesson_padding(
                self.rule('table.sheet-table .lesson',
                          self.media(f'@media (min-width: {width}px)')))
            with self.subTest(width=width):
                # sides: [top, left, bottom] — в сокращённой записи.
                if len(base) == 3:
                    top_i, side_i, bottom_i = 0, 1, 2
                else:
                    top_i, side_i, bottom_i = 0, 1, 2

                self.assertGreater(
                    got[side_i], base[side_i],
                    'боковые отступы должны расти с шириной окна',
                )
                self.assertEqual(
                    got[top_i], base[top_i],
                    'верхний отпуск — резерв под плашку, он не зависит '
                    'от ширины окна: иначе предмет прыгает по вертикали',
                )
                self.assertGreaterEqual(
                    got[bottom_i], base[bottom_i],
                    'нижний отступ под время не должен уменьшаться',
                )

    def test_badge_reserve_is_the_same_everywhere(self):
        """Резерв под плашку одинаков во всех ступенях.

        Плашка «замена» не меняет кегль при изменении ширины окна, и
        резерв под неё обязан быть одним и тем же. Иначе предмет в
        таблице на Full HD и на ноутбуке встанет на разной высоте —
        ровно тот дефект, который и ловит эта проверка.
        """
        reserves = [('base', self.lesson_padding(
            self.rule('table.sheet-table .lesson'))[0])]
        for width in STEPS:
            body = self.rule(
                'table.sheet-table .lesson',
                self.media(f'@media (min-width: {width}px)'),
            )
            reserves.append(
                (f'@{width}px', self.lesson_padding(body)[0]),
            )

        first = reserves[0][1]
        for where, value in reserves[1:]:
            with self.subTest(where=where):
                self.assertEqual(
                    value, first,
                    f'в {where} верхний отпуск {value}px, а в базовом '
                    f'{first}px — резерв под плашку должен быть один',
                )

    def lesson_padding(self, body):
        values = re.search(r'padding:\s*([\d.]+px(?:\s+[\d.]+px){1,3})\s*;',
                           body).group(1).split()
        return [float(v[:-2]) for v in values]

    def test_lesson_font_grows(self):
        base = float(re.search(r'font-size:\s*([\d.]+)px',
                               self.rule('table.sheet-table .lesson')).group(1))
        for width in STEPS:
            got = float(re.search(r'font-size:\s*([\d.]+)px',
                                  self.rule('table.sheet-table .lesson',
                                            self.media(f'@media (min-width: {width}px)'))
                                  ).group(1))
            with self.subTest(width=width):
                self.assertGreater(got, base)

    # ------------------------------------- текст помещается внутри ячейки

    def test_lesson_text_wraps_inside_cell(self):
        """Таблица с table-layout: fixed не расширяет колонку под текст.

        Без переноса длинное название предмета или ФИО вылезало
        за границу ячейки, поэтому перенос обязателен в обоих режимах.

        Именно break-word: ``anywhere`` рвёт слово на любом символе,
        даже когда оно помещается целиком, и превращает «Кудзинава»
        в «Кудзина / ва».
        """
        for selector in ('table.sheet-table .lesson',
                         'table.sheet-table .lesson-subject',
                         '.lesson-card'):
            with self.subTest(selector=selector):
                body = self.rule(selector)
                self.assertRegex(body, r'overflow-wrap:\s*break-word')
                self.assertNotRegex(body, r'overflow-wrap:\s*anywhere')

    def test_meta_lines_wrap_independently(self):
        """Каждая подпись переносится сама, иначе длинное ФИО вытолкнет
        соседние строки за пределы ячейки."""
        for selector in ('table.sheet-table .lesson-meta > div',
                         '.lesson-card .lesson-meta > div'):
            with self.subTest(selector=selector):
                self.assertRegex(self.css, re.escape(selector) + r'\s*\{[^}]*overflow-wrap')

    def test_line_height_readable(self):
        """Мелкий интерлиньяж при крупном кегле рвёт строки."""
        for selector in ('table.sheet-table .lesson', '.lesson-card'):
            value = float(re.search(r'line-height:\s*([\d.]+)',
                                    self.rule(selector)).group(1))
            with self.subTest(selector=selector):
                self.assertGreaterEqual(value, 1.4)

    # ------------------------------------------- сетка карточек «Пары»

    def test_cards_grid_widens(self):
        base = int(re.search(r'minmax\((\d+)px',
                             self.rule('.cards-grid')).group(1))
        for width in STEPS:
            got = int(re.search(r'minmax\((\d+)px',
                                self.rule('.cards-grid',
                                          self.media(f'@media (min-width: {width}px)'))
                                ).group(1))
            with self.subTest(width=width):
                self.assertGreater(got, base,
                                   'карточки должны стать шире, а не слипться')

    def test_cards_keep_time_room(self):
        """Карточка «Пар» резервирует под подвал столько же."""
        needed = float(re.search(
            r'--lesson-foot-h:\s*([\d.]+)px', css_text()).group(1))
        for width in STEPS:
            block = self.media(f'@media (min-width: {width}px)')
            rule = self.rule('.lesson-card', block)
            values = re.search(
                r'padding:\s*([\d.]+px(?:\s+[\d.]+px){1,3})\s*;',
                rule).group(1).split()
            with self.subTest(width=width):
                self.assertGreaterEqual(float(values[-1][:-2]), needed)

    # -------------------------------------------------- мобильные не сломаны

    def test_mobile_breakpoints_still_present(self):
        for width in (900, 620, 400):
            self.assertIn(f'@media (max-width: {width}px)', self.css)

    def test_mobile_sticky_day_column_intact(self):
        """Закреплённая колонка дня на мобильных — не трогаем."""
        block = self.media('@media (max-width: 620px)')
        self.assertIn('position: sticky', block)
        self.assertIn('left: 0', block)

    def test_horizontal_scroll_still_allowed(self):
        """Таблица с минимальной шириной обязана уметь скроллиться."""
        self.assertIn('overflow-x: auto', self.rule('.tt-wrap'))