"""Отступы блока занятия должны считаться, а не подбираться на глаз.

Что тут было. Верхний отпуск блока брался «с запасом» — 26px, потом 28px в
широких ступенях. Плашка «замена» при этом занимает 22px, и предмет в
результате висел низко, а в строках с переносом ФИО весь ряд оказывался
выше соседних.

Чтобы это не повторилось, отступы сверху и снизу проверяются расчётом от
кегля: сверху — высота плашки, снизу — высота времени пары. А min-height
проверяется на худший случай из четырёх строк текста, потому что одна такая
ячейка растягивает весь ряд таблицы.

Проверки живут отдельно от проверок внешнего вида: те смотрят, красиво ли,
эти — арифметика. При рассинхроне (например блок выше ячейки, из-за чего под
цветом остаётся белая полоса) тест внешнего вида ловит побочный симптом, а
здесь видна причина.
"""

import pathlib
import re

from django.test import SimpleTestCase

CSS = pathlib.Path(__file__).resolve().parent / 'static' / 'schedule' / 'style.css'

# Высота строки задана line-height: 1.45 в самих блоках.
LINE_HEIGHT = 1.45
# Под текстом в обёртке 4px — последний отступ .lesson-body.
BODY_BOTTOM = 4
# Худший случай: предмет, ФИО на две строки, аудитория.
WORST_CASE_LINES = 4


def css_text():
    return CSS.read_text(encoding='utf-8')


def rule(source, selector):
    match = re.search(re.escape(selector) + r'\s*\{([^}]*)\}', source)
    return match.group(1) if match else ''


def media_body(css, marker):
    """Тело @media-блока по маркеру, со счётом скобок.

    Вложенных @media нет, но полный разбор всё равно надёжнее: поиск по
    первому «}» обрезал бы блок на вложенном правиле.
    """
    start = css.index(marker)
    depth, i = 1, start + len(marker)
    while i < len(css) and depth:
        if css[i] == '{':
            depth += 1
        elif css[i] == '}':
            depth -= 1
        i += 1
    return css[start:i]


def paddings(body):
    """Верхний, боковой и нижний отступы из сокращённой записи."""
    found = re.search(
        r'padding:\s*([\d.]+)px(?:\s+([\d.]+)px)?'
        r'(?:\s+([\d.]+)px)?(?:\s+([\d.]+)px)?\s*;',
        body,
    )
    values = [float(v) for v in found.groups() if v]
    if len(values) == 2:      # по вертикали и по горизонтали
        top, bottom, sides = values[0], values[0], values[1]
    elif len(values) == 3:    # сверху, по бокам, снизу
        top, sides, bottom = values
    else:                     # все четыре
        top, sides, bottom = values[0], values[1], values[2]
    return top, sides, bottom


def font_size(body):
    return float(re.search(r'font-size:\s*([\d.]+)px', body).group(1))


class BadgeReserveMatchesBadgeHeightTests(SimpleTestCase):
    """Верхний отпуск = высота плашки «замена», а не больше."""

    def setUp(self):
        self.css = css_text()

    def badge_height(self, body):
        """Сколько места плашка занимает сверху.

        top + высота строки кегля + вертикальные поля + рамка. Раньше бралось
        «с запасом» 26px против фактических 22px, и этот запас был причиной
        того, что предмет стоял низко.
        """
        top = float(re.search(r'top:\s*([\d.]+)px', body).group(1))
        size = font_size(body)
        fields = re.search(r'padding:\s*([\d.]+)px(?:\s+([\d.]+)px)?'
                           r'(?:\s+([\d.]+)px)?\s*;', body)
        parts = [float(v) for v in fields.groups() if v]
        # в «padding: 1px 6px» первый — вертикальный
        vertical = parts[0] if len(parts) in (1, 2, 3) else parts[0]
        return top + size * 1.2 + vertical * 2 + 1

    def test_base_reserve_equals_badge_height(self):
        flag = rule(self.css, '.flag')
        needed = self.badge_height(flag)
        top, _, _ = paddings(rule(self.css, 'table.sheet-table .lesson'))
        self.assertGreaterEqual(
            top, needed,
            f'сверху {top:.0f}px, плашке нужно {needed:.0f}px — наезд',
        )
        # Разница больше 4px — это уже не запас, а пустое место.
        self.assertLessEqual(
            top - needed, 4,
            f'сверху {top:.0f}px при потребности {needed:.0f}px — лишние '
            f'{top - needed:.0f}px висят пустым местом над предметом',
        )

    def test_reserve_is_identical_in_every_step(self):
        """Один резерв во всех ширинах.

        Плашка не меняет кегль от ширины окна. Если резерв растёт вместе с
        ней, предмет в таблице на ноутбуке и на Full HD встаёт на разной
        высоте — то самое, ради чего резерв и вводился.
        """
        base, _, _ = paddings(rule(self.css, 'table.sheet-table .lesson'))
        for width in (1800, 2400, 3200):
            with self.subTest(width=width):
                body = media_body(self.css, f'@media (min-width: {width}px)')
                top, _, _ = paddings(rule(body, 'table.sheet-table .lesson'))
                self.assertEqual(
                    top, base,
                    f'на {width}px сверху {top:.0f}px, в базе {base:.0f}px',
                )

    def test_card_reserve_matches_table(self):
        """Карточка и ячейка резервируют одно и то же место.

        Иначе предмет прыгал бы по вертикали при переключении режима
        «Пары», где вместо таблицы рисуются карточки.
        """
        table_top, _, _ = paddings(rule(self.css, 'table.sheet-table .lesson'))
        card_top, _, _ = paddings(rule(self.css, '.lesson-card'))
        self.assertEqual(
            card_top, table_top,
            f'в карточке сверху {card_top:.0f}px, в ячейке {table_top:.0f}px',
        )


class BottomReserveCoversTimeTests(SimpleTestCase):
    """Место под подвал резервирует нижний отпуск карточки.

    Подвал абсолютный и в поток не входит, поэтому освобождать для него
    место должна сама карточка. Если отпуск меньше подвала, плашка
    накроет нижнюю строку текста.
    """

    def setUp(self):
        self.css = css_text()

    def foot_height(self):
        return float(re.search(
            r'--lesson-foot-h:\s*([\d.]+)px', self.css).group(1))

    def test_lesson_reserves_room_for_footer(self):
        for selector in ('table.sheet-table .lesson', '.lesson-card'):
            with self.subTest(селектор=selector):
                _, _, bottom = paddings(rule(self.css, selector))
                self.assertGreaterEqual(
                    bottom, self.foot_height(),
                    f'{selector}: снизу {bottom:.0f}px, подвалу нужно '
                    f'{self.foot_height():.0f}px — накроет текст',
                )

    def test_footer_height_matches_its_own_rule(self):
        """Высоты подвала хватает под рамку, отступы и текст.

        Высота задана жёстко и включает в себя рамку (border-box), а текст
        выравнивается по центру. Поэтому проверяется, что внутрь высоты
        помещается надпись, а не что содержимое равно высоте: если строки
        не хватит, flex выдавит её за границы плашки.
        """
        footer = rule(self.css, '.lesson-foot')
        self.assertIn('height: var(--lesson-foot-h)', footer)
        size = font_size(footer)
        pad = float(re.search(r'padding:\s*([\d.]+)px', footer).group(1))
        border = re.search(r'border:\s*([\d.]+)px', footer)
        frame = float(border.group(1)) * 2 if border else 0.0
        free = self.foot_height() - frame - pad * 2
        self.assertGreaterEqual(
            free, size * 1.15,
            f'внутри подвала остаётся {free:.0f}px при кегле {size:.0f}px — '
            'надпись вылезет за рамку',
        )

    def test_header_time_has_explicit_size(self):
        """У времени в шапке задан кегль, а не только отступ."""
        body = rule(self.css, '.pair-time')
        self.assertRegex(body, r'font-size:\s*[\d.]+px')

class MinHeightCoversWorstCaseTests(SimpleTestCase):
    """min-height покрывает худший случай, иначе ряд растянет."""

    STEPS = [('', None),
             ('620px', '@media (max-width: 620px)'),
             ('1800px', '@media (min-width: 1800px)'),
             ('2400px', '@media (min-width: 2400px)'),
             ('3200px', '@media (min-width: 3200px)')]

    def setUp(self):
        self.css = css_text()

    def source_for(self, marker):
        if marker is None:
            return self.css
        return media_body(self.css, marker)

    def test_block_is_tall_enough_for_four_lines(self):
        """На широких ступенях блок покрывает четыре строки текста.

        Телефон сюда не входит намеренно. Там кегль 10.5px, четыре строки
        дали бы 95px, а вся сетка занимает 54px: длинное ФИО на телефоне
        занимает столько, сколько нужно, и ячейка растёт сама. Заставлять
        все строки телефона быть высокими ради одного длинного ФИО значит
        уменьшить вдвое то, что чаще всего открывают на телефоне.
        """
        for label, marker in self.STEPS:
            if marker and 'max-width' in marker:
                continue
            with self.subTest(step=label):
                body = rule(self.source_for(marker),
                            'table.sheet-table .lesson')
                top, _, bottom = paddings(body)
                size = font_size(body)
                # Подвал — часть содержимого блока, и в колонке он занимает
                # две строки плюс отступы. Без него в расчёте худший случай
                # занижается на высоту подвала, и ряд растянет та ячейка,
                # где предмет занял все четыре строки.
                #
                step = self.source_for(marker)

                # Подвал абсолютный и места в потоке не занимает — его
                # высоту знает только карточка, в переменной. Раньше он был
                # обычным блоком, и высота считалась по отступам.
                footer_height = float(re.search(
                    r'--lesson-foot-h:\s*([\d.]+)px', self.css).group(1))

                needed = (WORST_CASE_LINES * size * LINE_HEIGHT
                          + BODY_BOTTOM + top + bottom + footer_height)
                height = float(
                    re.search(r'min-height:\s*([\d.]+)px', body).group(1))
                self.assertGreaterEqual(
                    height, needed,
                    f'на ступени {label} блок {height:.0f}px, а нужно '
                    f'{needed:.0f}px — ячейка с длинным ФИО растянет ряд',
                )

    def test_cell_and_block_have_the_same_height(self):
        """Ячейка и блок одной высоты во всех ступенях.

        Блок ниже ячейки — под цветом предмета остаётся белая полоса.
        Блок выше — ряд расползается. Раньше здесь стояло 108px у ячейки и
        108px у блока, а резерв под плашку требовал 112: рассинхрон был
        прямой причиной белой полосы под блоком.
        """
        for label, marker in self.STEPS:
            with self.subTest(step=label):
                source = self.source_for(marker)
                cell = rule(source, 'td.slot')
                lesson = rule(source, 'table.sheet-table .lesson')
                self.assertTrue(cell, f'нет ячейки на ступени {label}')
                cell_h = re.search(
                    r'(?:min-)?height:\s*([\d.]+)px', cell)
                lesson_h = re.search(
                    r'min-height:\s*([\d.]+)px', lesson)
                self.assertIsNotNone(
                    lesson_h, f'у блока нет min-height на ступени {label}')
                self.assertEqual(
                    cell_h.group(1), lesson_h.group(1),
                    f'на ступени {label}: блок {lesson_h.group(1)}px, '
                    f'ячейка {cell_h.group(1)}px',
                )


class NoStaleVerticalHacksTests(SimpleTestCase):
    """Хаки вертикального выравнивания не возвращаются."""

    def setUp(self):
        self.css = css_text()

    def test_body_has_no_margin_top_or_padding_top(self):
        body = rule(self.css, '.lesson-body')
        self.assertNotIn('margin-top', body,
                         'margin-top: auto отключает justify-content блока — '
                         'предмет начнёт прыгать по вертикали')
        self.assertNotIn('padding-top', body,
                         'резерв под плашку держит блок, а не обёртка')

    def test_block_centres_content(self):
        body = rule(self.css, 'table.sheet-table .lesson')
        self.assertRegex(body, r'display:\s*flex')
        self.assertRegex(body, r'flex-direction:\s*column')
        self.assertRegex(body, r'align-items:\s*center')
        self.assertRegex(body, r'justify-content:\s*center')

    def test_badge_is_out_of_flow(self):
        flag = rule(self.css, '.flag')
        self.assertRegex(flag, r'position:\s*absolute')
        self.assertRegex(flag, r'left:\s*50%')
        self.assertIn('translateX(-50%)', flag)

    def test_comments_do_not_describe_removed_properties(self):
        """Комментарий не должен предлагать вернуть сломанное свойство.

        Именно из-за комментария «margin-top прижимает группу к низу» это
        свойство однажды вернули — и оно молча отключило
        justify-content, из-за чего предмет в ячейке с меткой уезжал вниз.

        Комментарий, который читается как инструкция и при этом
        противоречит коду, вреднее отсутствующего. Поэтому проверяется не
        текст, а код: свойства в CSS быть не должно, а пояснение в
        комментарии — можно и нужно.
        """
        # Вырезаются комментарии: «Никаких margin-top: auto» — это верное
        # пояснение, и его наличие как раз обязательно.
        code_only = re.sub(r'/\*.*?\*/', '', self.css, flags=re.S)
        # Проверяются только блоки занятия и обёртка текста. В других
        # правилах margin-top — обычное дело (отступ у заголовков,
        # у списков), и запрещать его там незачем.
        for selector in ('.lesson-body',
                         'table.sheet-table .lesson', '.lesson-card'):
            with self.subTest(selector=selector):
                body = rule(code_only, selector)
                self.assertNotIn(
                    'margin-top', body,
                    f'в {selector} появился margin-top, он ломает '
                    'justify-content блока',
                )
                self.assertNotIn(
                    'padding-top', body,
                    f'у {selector} не должно быть padding-top: резерв под '
                    'плашку держит блок, а не содержимое',
                )

    def test_comment_explains_why_the_hack_is_gone(self):
        """Почему свойство убрано — записано рядом с местом удаления.

        Без пояснения через месяц то же свойство вернут: выглядит
        безобидно, а ломает выравнивание незаметно.
        """
        before = self.css[:self.css.index('.lesson-body {')]
        tail = before[-1600:]
        self.assertIn(
            'margin-top', tail,
            'рядом с .lesson-body нет пояснения, что margin-top убран '
            'намеренно — свойство вернут',
        )