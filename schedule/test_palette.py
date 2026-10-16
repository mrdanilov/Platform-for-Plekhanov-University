"""Насыщенность оформления: палитра занятий и контраст блоков.

Палитра подложек — то, что задаёт «выцветший» вид всего расписания.
Раньше это были цвета Material Design 50 (светлота ~0.88): на белой
таблице они читались как бледная бумага, и два соседних предмета было
не отличить. Откатить это легко и незаметно — правка одной строки в
importer.py проходит все прочие тесты, поэтому контракт зафиксирован здесь.

Контраст текста внутри блока проверяется расчётом по той же формуле, что и
в test_ui.py: палитра не должна «поднять насыщенность», сломав читаемость.
"""

import re

from django.test import SimpleTestCase

from schedule.exports import SUBJECT_COLORS, _pick_color
from schedule.management.commands.set_subject_palette import _spread
from schedule.test_ui import contrast, css_text, css_variables


def relative_luminance(hex_color):
    color = hex_color.lstrip('#')
    channels = [int(color[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        for c in channels
    ]
    return (0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2])


class SubjectPaletteTests(SimpleTestCase):
    def setUp(self):
        self.themes = css_variables(css_text())

    def test_palette_is_large_enough(self):
        self.assertGreaterEqual(len(SUBJECT_COLORS), 6,
                                'предметов больше, чем цветов: половина блоков '
                                'будет совпадать')
        self.assertEqual(len(SUBJECT_COLORS), len(set(SUBJECT_COLORS)),
                         'в палитре есть повторяющиеся цвета')

    def test_no_colour_is_near_white(self):
        """Главный guard от «выцветшего» вида.

        Светлота 0.88 — это ровно то, из-за чего расписание выглядело
        бледным. Возвращать такие цвета нельзя.
        """
        for color in SUBJECT_COLORS:
            with self.subTest(color=color):
                self.assertLessEqual(
                    relative_luminance(color), 0.80,
                    f'{color} слишком близко к белому — блок занятия '
                    'не отличить от пустой ячейки',
                )
                self.assertGreaterEqual(
                    relative_luminance(color), 0.45,
                    f'{color} слишком тёмный: на тёмном фоне текст пропадёт',
                )

    def test_text_stays_readable_on_every_colour(self):
        text = self.themes['light']['text']
        for color in SUBJECT_COLORS:
            with self.subTest(color=color):
                self.assertGreaterEqual(
                    contrast(text, color), 4.5,
                    f'текст предмета нечитаем на {color}',
                )

    def test_pick_color_stays_inside_palette(self):
        for name in ('Финансы', 'English', 'Математика', 'Философия'):
            with self.subTest(name=name):
                self.assertIn(_pick_color(name), SUBJECT_COLORS)

    def test_spread_avoids_repeats_where_palette_allows(self):
        """Подбор без повторов, пока хватает палитры.

        Восемь цветов на двадцать названий — повторы неизбежны, и требовать
        их отсутствия бессмысленно. Проверяется то, что реально достижимо:
        при количестве предметов, равном размеру палитры, все цвета разные.
        """
        size = len(SUBJECT_COLORS)
        names = [f'Предмет {i}' for i in range(size)]
        got = [_spread(names)[n] for n in names]
        self.assertEqual(
            len(set(got)), size,
            f'при {size} предметах повторов быть не должно, '
            f'получено {len(set(got))} разных цветов',
        )

    def test_spread_never_returns_a_colour_outside_palette(self):
        for count in (1, 5, 8, 30):
            names = [f'Предмет {i}' for i in range(count)]
            assigned = _spread(names)
            for name, color in assigned.items():
                with self.subTest(count=count, name=name):
                    self.assertIn(color, SUBJECT_COLORS)

    def test_spread_is_deterministic(self):
        names = ['Финансы', 'Математика', 'Философия', 'English']
        self.assertEqual(_spread(names), _spread(names),
                         'перезапуск команды обязан давать тот же результат')


class BodyBackgroundTests(SimpleTestCase):
    """Фирменное фото должно быть видно, а не растворяться в фоне."""

    def setUp(self):
        self.css = css_text()

    def test_background_is_tinted_not_laid_plain(self):
        layer = self.css[self.css.index('body::before {'):]
        layer = layer[:layer.index('\n}')]
        self.assertIn('linear-gradient(var(--bg-veil)', layer,
                      'фото ложится без приглушающего слоя и выходит выцветшим')

    def test_photo_does_not_grow_with_the_page(self):
        """Фон не должен ехать за таблицей.

        Снимок висел на body с background-size: cover, а cover тянется под
        всю высоту документа. Стоило увеличить расписание — документ вырос,
        и фон приблизился вместе с таблицей. Слой на всё окно от высоты
        страницы не зависит вовсе.
        """
        layer = self.css[self.css.index('body::before {'):]
        layer = layer[:layer.index('\n}')]
        self.assertIn('position: fixed', layer)
        self.assertIn('inset: 0', layer)

    def test_no_background_attachment(self):
        """Привязки к окну обходным свойством не осталось.

        С background-attachment: fixed снимок рисовался один раз и не
        добирал страницу, если она короче окна — фото выходило полосой.
        Слой на всё окно полосы оставить не может.
        """
        body = re.sub(r'/\*.*?\*/', '', self.css, flags=re.S)
        self.assertNotIn('background-attachment', body)

    def test_page_background_is_distinct_from_panels(self):
        """Фон страницы должен отличаться от белых панелей.

        Конкретная светлота здесь не фиксируется: пользователь просил
        «светлый и воздушный» интерфейс, и зажимать тон здесь нельзя —
        иначе следующая правка палитры упрётся в тест. Проверяется
        сравнение: фон не белый и не совпадает с подложкой панелей.
        """
        themes = css_variables(self.css)
        bg, surface = themes['light']['bg'], themes['light']['surface']
        self.assertLessEqual(relative_luminance(bg), 0.92,
                             'фон страницы слишком светлый, панели с ним сливаются')
        self.assertGreater(relative_luminance(surface) - relative_luminance(bg), 0.10,
                           'фон и белые панели неразличимы')


class TableContrastTests(SimpleTestCase):
    """Рамки и читаемость таблицы."""

    def setUp(self):
        self.themes = css_variables(css_text())

    def test_grid_lines_are_not_hairline(self):
        """Рамки должны читаться: слишком светлые делают таблицу размытой."""
        grid = self.themes['light']['grid-line']
        self.assertLessEqual(
            relative_luminance(grid), 0.60,
            f'рамка {grid} слишком светлая — таблица расплывается',
        )

    def test_empty_cells_differ_from_filled(self):
        """Пустая ячейка должна отличаться от занятой."""
        css = css_text()
        slot = css[css.index('table.sheet-table td.slot {'):]
        slot = slot[:slot.index('\n}')]
        empty = css[css.index('table.sheet-table td.slot-empty'):]
        empty = empty[:empty.index('\n}')]
        self.assertIn('var(--surface)', slot)
        self.assertIn('var(--surface-3)', empty)
        self.assertNotEqual(slot, empty)
