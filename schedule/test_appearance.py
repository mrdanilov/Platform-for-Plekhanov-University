"""Фоновое фото страницы и тени блоков.

Фон проверяем отдельно от цветов текста: он задаётся картинкой, а её
влияние на читаемость ограничено прозрачностью подложек карточек.
"""

import os
import re

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase

IMAGE_DIR = os.path.join('schedule', 'static', 'schedule', 'img')
# 14 МБ фотографии 6720x4480 после обработки весит меньше мегабайта
MAX_BACKGROUND_BYTES = 400 * 1024


def css_text():
    with open(finders.find('schedule/style.css'), encoding='utf-8') as fh:
        return fh.read()


def strip_comments(css):
    """CSS без комментариев.

    Нужна там, где проверяется отсутствие свойства: комментарии в stylesheet
    законно упоминают то, чего в правилах уже нет, — например,
    background-attachment, о котором объясняют, почему его убрали.
    """
    return re.sub(r'/\*.*?\*/', '', css, flags=re.S)


def png_size(path):
    """Размеры PNG из заголовка IHDR."""
    with open(path, 'rb') as fh:
        head = fh.read(24)
    return (int.from_bytes(head[16:20], 'big'),
            int.from_bytes(head[20:24], 'big'))


class BackgroundAssetTests(SimpleTestCase):
    """Сам файл фона: он должен быть лёгким и в двух вариантах."""

    def _path(self, name):
        found = finders.find(f'schedule/img/{name}')
        self.assertIsNotNone(found, f'нет файла {name}')
        return found

    def test_both_variants_exist(self):
        for name in ('plekhanova_background.jpg',
                     'plekhanova_background_dark.jpg'):
            with self.subTest(name=name):
                self.assertIsNotNone(self._path(name))

    def test_files_are_light_enough(self):
        """Исходная фотография весила 14 МБ — столько тянуть нельзя."""
        for name in ('plekhanova_background.jpg',
                     'plekhanova_background_dark.jpg'):
            with self.subTest(name=name):
                size = os.path.getsize(self._path(name))
                self.assertLess(size, MAX_BACKGROUND_BYTES,
                                f'{name} весит {size // 1024} КБ')

    def test_jpeg_signature(self):
        for name in ('plekhanova_background.jpg',
                     'plekhanova_background_dark.jpg'):
            with self.subTest(name=name):
                with open(self._path(name), 'rb') as fh:
                    self.assertEqual(fh.read(2), b'\xff\xd8', 'не JPEG')


class BackgroundCssTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.css = css_text()

    def rule(self, selector):
        pattern = (r'(?m)^' + re.escape(selector)
                   if selector == 'body' else re.escape(selector))
        match = re.search(pattern + r'\s*\{([^}]*)\}', self.css)
        self.assertIsNotNone(match, f'нет правила {selector}')
        return match.group(1)

    def photo_layer(self):
        """Фоновая фотография — отдельный слой на всё окно.

        Раньше фото висело на body с background-size: cover, и его размер
        зависел от высоты документа: стоило увеличить расписание — документ
        вырос, и фотография растянулась вместе с таблицей. Теперь фото
        лежит на body::before с inset: 0 и ни от чего, кроме окна, не
        зависит.
        """
        rule = self.rule('body::before')
        self.assertIn("url('img/plekhanova_background.jpg')", rule)
        self.assertIn('background-size: cover', rule)
        self.assertIn('background-repeat: no-repeat', rule)
        self.assertIn('position: fixed', rule,
                      'слой не привязан к окну — фон поедет за таблицей')
        self.assertIn('inset: 0', rule,
                      'слой должен закрывать всё окно, иначе по краям '
                      'полоса ровной заливки')
        self.assertIn('z-index: -1', rule,
                      'слой перекрыл бы содержимое страницы')
        return rule

    def test_body_must_not_cover_the_layer(self):
        """Непрозрачный body перекрыл бы слой с отрицательным z-index."""
        body = self.rule('body')
        self.assertIn('background: transparent', body,
                      'body перекроет фоновый слой')

    def test_photo_url_is_relative(self):
        """Относительный адрес переживёт смену хоста и префикса STATIC_URL."""
        self.assertNotIn('url(/static/', self.photo_layer())

    def test_canvas_has_colour_for_missing_photo(self):
        """Цвет подложки нужен на случай, если картинка не загрузилась."""
        self.assertRegex(self.css, r'html,\s*body\s*\{\s*background:\s*var\(--bg\)')

    def test_dark_theme_keeps_the_photo_visible(self):
        """Тёмная тема не должна гасить фото.

        Раньше режим подмешивания задавался для body и наследовался тёмной
        темой: с подложкой #11171d multiply давал почти чистый чёрный.
        Сейчас режимов подмешивания нет вовсе, тон задаёт вуаль, поэтому
        проверяется именно она.
        """
        dark = self.rule('html[data-theme="dark"] body::before')
        self.assertIn('plekhanova_background_dark.jpg', dark)
        self.assertIn('var(--bg-veil)', dark,
                      'в тёмной теме фото идёт без приглушающего слоя')
        self.assertNotIn('multiply', dark)

    def test_light_theme_veils_the_photo(self):
        """Фото перекрывается приглушающим слоем, а не подмешиванием.

        multiply с светлой подложкой выдавал пересвеченный, почти белый
        снимок без деталей.
        """
        self.assertIn('linear-gradient(var(--bg-veil), var(--bg-veil))',
                      self.photo_layer())
        self.assertNotIn('background-blend-mode', self.css)

    def test_veil_is_not_too_strong(self):
        """Фон должен читаться, поэтому вуаль не перекрывает фото целиком."""
        for theme, marker in (('light', ':root,'), ('dark', 'html[data-theme="dark"] {')):
            block = self.css[self.css.index(marker):]
            alpha = re.search(r'--bg-veil:\s*rgba\([^,]+,[^,]+,[^,]+,\s*([\d.]+)\)',
                              block).group(1)
            with self.subTest(theme=theme):
                self.assertLessEqual(
                    float(alpha), 0.55,
                    'вуаль слишком плотная — фонового снимка не будет видно',
                )
                self.assertGreater(float(alpha), 0.05,
                                   'фото окажется совсем без приглушения')

    def test_photo_covers_whole_viewport(self):
        """Слой закрывает всё окно.

        Старый способ — background-attachment: fixed — на странице короче
        окна рисовал фото полосой, а поля оставались ровной заливкой.
        Отдельный слой с inset: 0 такой полосы оставить не может: он всегда
        размером с окно.
        """
        rule = self.photo_layer()
        self.assertNotIn('background-attachment', strip_comments(self.css),
                         'привязка к окну обходным свойством больше не нужна')

    def test_dark_theme_uses_its_own_variant(self):
        block = self.rule('html[data-theme="dark"] body::before')
        self.assertIn('plekhanova_background_dark.jpg', block)

    def test_cards_are_slightly_transparent(self):
        """Сквозь карточки фото видно, но текст остаётся на светлом."""
        for selector in ('.schedule-block', '.card', '.cards-day'):
            with self.subTest(selector=selector):
                self.assertIn('var(--surface-glass)', self.rule(selector))

    def test_glass_is_almost_opaque(self):
        """Прозрачность 0.93: фото заметно, контраст текста не страдает."""
        light = self.css[self.css.index(':root'):self.css.index(
            'html[data-theme="dark"]')]
        value = re.search(r'--surface-glass:\s*rgba\([^,]+,[^,]+,[^,]+,\s*([\d.]+)\)',
                          light).group(1)
        self.assertGreaterEqual(float(value), 0.9)

    def test_table_cells_stay_opaque(self):
        """Ячейки расписания непрозрачные — иначе текст потеряет контраст."""
        self.assertIn('var(--surface)', self.rule('table.sheet-table'))

    def test_print_has_no_background(self):
        block = self.css[self.css.index('@media print'):]
        print_block = block[:block.index('\n}', block.index('body {')) + 2]
        self.assertIn('background-image: none', print_block)
        self.assertIn('body::before', print_block,
                      'слой с фотографией на бумаге превратился бы в '
                      'серый прямоугольник')


class BlockShadowTests(SimpleTestCase):
    """Тень должна быть у всех блоков, шапка — особенно."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.css = css_text()

    def rule(self, selector):
        match = re.search(r'(?m)^' + re.escape(selector) + r'\s*\{([^}]*)\}', self.css)
        self.assertIsNotNone(match, f'нет правила {selector}')
        return match.group(1)

    def test_every_block_has_shadow(self):
        for selector in ('.topbar', '.schedule-block', '.card', '.placeholder',
                         '.cards-day', '.picker', '.menu-bar', '.alert',
                         '.lesson-card', '.btn'):
            with self.subTest(selector=selector):
                self.assertIn('box-shadow', self.rule(selector),
                              f'у {selector} нет тени')

    def test_header_shadow_is_present_and_soft(self):
        shadow = self.rule('.topbar')
        self.assertIn('box-shadow', shadow)
        # два слабых слоя, а не один жёсткий
        self.assertGreaterEqual(shadow.count('rgba'), 2)
        # Тень читается отдельно от border-bottom: полупрозрачная линия
        # под шапкой — это оформление, а не тень, и её прозрачность
        # за��авлять тут нельзя.
        shadow_only = re.sub(r'border-bottom:[^;]+;', '', shadow)
        for value in re.findall(r'rgba\([^)]*?,\s*([\d.]+)\)', shadow_only):
            self.assertLessEqual(float(value), 0.15,
                                 'тень шапки не должна быть заметной')

    def test_today_outline_replaces_shadow(self):
        """У сегодняшнего дня тень заменена обводкой, иначе будет две тени."""
        self.assertIn('box-shadow: 0 0 0 1px var(--blue)',
                      self.rule('.cards-day.is-today'))

    def test_shadows_dropped_in_print(self):
        block = self.css[self.css.index('@media print'):]
        self.assertIn('.schedule-block { border: none; padding: 0; box-shadow: none; }',
                      block)


class LessonTimeWeightTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.css = css_text()

    def time_rule(self, selector=''):
        """Действующие параметры времени пары внутри указанного блока.

        Раньше бралось первое совпадение ``.lesson-time`` — то есть базовые
        11.5px в углу 5px. Но внутри таблицы и карточек это правило перебито:
        там кегль 9.5px и отступ 3px. Проверка брала завышенные 18.8px и
        требовала от блока отступа снизу больше, чем нужно на самом деле, —
        то есть требовала лишнего.

        Поэтому параметры ищутся по каскаду: сперва точное правило с
        уточнением, затем то же с классом карточки, и только в конце —
        базовое. Иначе тест мерил бы не тот блок, который реально видит
        сотрудник.
        """
        if selector:
            table = 'table.sheet-table' in selector
            card = selector.endswith('.lesson-card')
            candidates = []
            if table:
                candidates.append('table.sheet-table .lesson-time')
            if card:
                candidates.append('.lesson-card .lesson-time')
            for candidate in candidates:
                found = re.search(
                    re.escape(candidate) + r'\s*\{([^}]*)\}', self.css,
                )
                if found:
                    return found.group(1)
        return re.search(r'\.lesson-time\s*\{([^}]*)\}', self.css).group(1)

    def test_time_in_header_is_bold(self):
        """Время в шапке пары читается."""
        body = re.search(r'\.pair-time\s*\{([^}]*)\}', self.css).group(1)
        size = float(re.search(r'font-size:\s*([\d.]+)px', body).group(1))
        self.assertGreaterEqual(size, 10.0, 'время в шапке стало нечитаемым')
        card = re.search(r'\.cards-time\s*\{([^}]*)\}', self.css).group(1)
        self.assertRegex(card, r'font-weight:\s*(600|700|bold)')

    def test_time_in_header_is_not_shrunk(self):
        """Время в шапке не мельчает вместе с ячейкой.

        Карточки убрали время, и шапка осталась единственным местом, где
        оно видно: если кегль уменьшится, время исчезнет совсем.
        """
        body = re.search(r'\.pair-time\s*\{([^}]*)\}', self.css).group(1)
        size = float(re.search(r'font-size:\s*([\d.]+)px', body).group(1))
        self.assertGreaterEqual(size, 10.0, 'время в шапке стало нечитаемым')

    def test_time_is_out_of_the_card(self):
        """В карточке занятия времени нет — оно живёт в шапке.

        Правила на ``.lesson-time`` быть не должно: элемент из разметки
        убран, а оставшееся правило вводило бы в заблуждение.
        """
        self.assertIsNone(
            re.search(r'\.lesson-time', self.css),
            'стили .lesson-time остались, хотя элемента уже нет')

    def test_meta_has_no_horizontal_reserve(self):
        """У подписей не должно быть отступа справа под время.

        Он не нужен: время отведено по вертикали. А по горизонтали
        отступ сужает колонку, длинное ФИО переносится и рвётся
        посреди слова. Возвращать такое нельзя.
        """
        for selector in ('table.sheet-table .lesson-meta', '.lesson-card .lesson-meta'):
            with self.subTest(selector=selector):
                rule = re.search(
                    re.escape(selector) + r'\s*\{([^}]*)\}', self.css)
                self.assertIsNotNone(rule, f'нет правила {selector}')
                self.assertNotIn(
                    'padding-right', rule.group(1),
                    f'у {selector} появился горизонтальный запас под время — '
                    'ФИО начнёт рваться посреди слова',
                )

    def test_words_are_not_broken_mid_word(self):
        """``break-word``, а не ``anywhere``.

        ``anywhere`` рвёт слово на любом символе, даже когда оно помещается
        целиком: «Кудзинава» превращалась в «Кудзина / ва». Ломает вид
        всей таблицы.
        """
        for selector in ('table.sheet-table .lesson',
                         'table.sheet-table .lesson-subject',
                         'table.sheet-table .lesson-meta > div',
                         '.lesson-card'):
            with self.subTest(selector=selector):
                rule = re.search(
                    re.escape(selector) + r'\s*\{([^}]*)\}', self.css)
                self.assertIsNotNone(rule, f'нет правила {selector}')
                self.assertRegex(rule.group(1), r'overflow-wrap:\s*break-word')
                self.assertNotRegex(
                    rule.group(1), r'overflow-wrap:\s*anywhere',
                    'anywhere рвёт слова посреди — вид таблицы ломается',
                )

    def test_slot_height_fits_a_substitution(self):
        """Ячейка должна вмещать занятие с меткой «замена».

        Метка добавляет строку сверху. Раньше высоты не хватало, и время
        в углу наезжало на аудиторию.
        """
        slot = re.search(r'table\.sheet-table td\.slot\s*\{([^}]*)\}', self.css)
        height = float(re.search(r'(?:min-)?height:\s*([\d.]+)px',
                                 slot.group(1)).group(1))
        # верхний отступ 8 + бейдж 17 + название 18 + подпись 32 + низ 20
        needed = 8 + 17 + 18 + 3 + 32 + 20
        self.assertGreaterEqual(
            height, needed,
            f'ячейка {height:.0f}px, а с меткой «замена» нужно {needed}px',
        )

    def test_lesson_centres_its_children(self):
        """Контейнер центрирует содержимое по обеим осям.

        По вертикали — чтобы предмет стоял на одном уровне в каждой
        ячейке ряда. По горизонтали — иначе flex растянет содержимое.
        """
        for selector in ('table.sheet-table .lesson', '.lesson-card'):
            with self.subTest(selector=selector):
                rule = re.search(
                    re.escape(selector) + r'\s*\{([^}]*)\}', self.css).group(1)
                self.assertIn('display: flex', rule)
                self.assertRegex(rule, r'flex-direction:\s*column')
                self.assertRegex(rule, r'align-items:\s*center')
                self.assertRegex(
                    rule, r'justify-content:\s*center',
                    f'у {selector} нет justify-content: center — предмет '
                    'будет стоять на разной высоте в соседних ячейках',
                )

    def test_badge_is_out_of_flow(self):
        """Плашка «замена» наложена сверху и не влияет на раскладку.

        Пока она была элементом flex-колонки, она занимала всю ширину
        ячейки (элементы flex растягиваются по поперечной оси) и, главное,
        добавляла свою высоту: сумма не влезала, и предмет в ячейке с
        заменой стоял ниже соседних. Абсолютное позиционирование снимает
        обе проблемы разом.
        """
        flag = re.search(r'(?m)^\.flag\s*\{([^}]*)\}', self.css)
        self.assertIsNotNone(flag, 'нет правила .flag')
        body = flag.group(1)
        self.assertRegex(
            body, r'position:\s*absolute',
            'плашка обязана быть вне потока, иначе она сдвигает содержимое',
        )
        self.assertRegex(body, r'top:\s*[\d.]+px')
        self.assertRegex(body, r'left:\s*50%')
        self.assertIn(
            'translateX(-50%)', body,
            'плашка без сдвига от центра налезала бы на правый край ячейки',
        )
        self.assertNotIn('align-self', body,
                         'плашка вне потока — align-self ей больше не нужен')

    def test_badge_text_is_bold(self):
        """Белым по 10px на красном читалось плохо."""
        flag = re.search(r'(?m)^\.flag\s*\{([^}]*)\}', self.css).group(1)
        self.assertRegex(flag, r'font-weight:\s*700',
                         'надпись на плашке должна быть жирной')

    def test_badge_space_is_reserved_on_the_block(self):
        """Место под плашку зарезервировано на самом блоке, всегда.

        Раньше резерв стоял внутри ``.lesson-body`` (padding-top), а сама
        плашка была в потоке. Она добавляла высоту сверху, сумма не
        влезала в ячейку, и предмет в ячейке с заменой уезжал ниже
        соседних. Теперь плашка лежит поверх, а место под неё держит
        верхний отступ блока — и он задан всегда, а не только при замене.
        """
        lesson = re.search(
            r'table\.sheet-table \.lesson\s*\{([^}]*)\}', self.css).group(1)
        padding = re.search(
            r'padding:\s*([\d.]+)px\s+([\d.]+)px\s+([\d.]+)px\s+([\d.]+)px',
            lesson)
        if padding is None:
            # краткая форма: «сверху по бокам снизу»
            short = re.search(
                r'padding:\s*([\d.]+)px\s+([\d.]+)px\s+([\d.]+)px', lesson)
            self.assertIsNotNone(
                short, 'у блока занятия нет padding — резерв под плашку не найден')
            reserve = float(short.group(1))
        else:
            reserve = float(padding.group(1))

        # Ищется именно отдельное правило `.flag {`, а не `.lesson-card .flag`:
        # второе идёт в файле раньше и про положение плашки ничего не говорит.
        flag = re.search(r'(?m)^\.flag\s*\{([^}]*)\}', self.css)
        self.assertIsNotNone(flag, 'нет правила .flag')
        size = float(re.search(r'font-size:\s*([\d.]+)px', flag.group(1)).group(1))
        top = float(re.search(r'top:\s*([\d.]+)px', flag.group(1)).group(1))
        needed = top + size * 1.2 + 2
        self.assertGreaterEqual(
            reserve, needed,
            f'резерв сверху {reserve:.0f}px, плашке нужно {needed:.0f}px — '
            'предмет наедет на метку',
        )

    def test_body_has_no_vertical_hacks(self):
        """В обёртке не осталось хаков с вертикальным выравниванием.

        ``margin-top: auto`` и ``padding-top`` в обёртке были попыткой
        прижать текст к низу и оставить место под метку. С абсолютной
        плашкой это не нужно, а ``justify-content`` блока центрует
        содержимое само. Возвращать эти свойства нельзя — они снова
        разъедутся по высоте.
        """
        body = re.search(r'\.lesson-body\s*\{([^}]*)\}', self.css).group(1)
        self.assertNotIn('margin-top', body,
                         'margin-top: auto в обёртке ломает выравнивание')
        self.assertNotIn('padding-top', body,
                         'резерв под плашку держит блок, а не обёртка')

    def test_text_block_fills_width(self):
        """Обёртка занимает ширину ячейки.

        Родитель центрирует элементы по горизонтали, поэтому без
        ``width: 100%`` обёртка села бы по содержимому и длинное название
        предмета полезло бы через край ячейки.
        """
        body = re.search(r'\.lesson-body\s*\{([^}]*)\}', self.css).group(1)
        self.assertRegex(body, r'width:\s*100%')
        self.assertRegex(body, r'max-width:\s*100%')

    def test_body_does_not_touch_the_time(self):
        """Между опущенным текстом и временем должен быть зазор."""
        body = re.search(r'\.lesson-body\s*\{([^}]*)\}', self.css).group(1)
        padding_bottom = re.search(r'padding-bottom:\s*([\d.]+)px', body)
        self.assertIsNotNone(
            padding_bottom,
            'у .lesson-body нет нижнего отступа — текст прижмётся к времени',
        )
        self.assertGreater(
            float(padding_bottom.group(1)), 0,
            'нижний отпуск нужен хотя бы в пару пикселей',
        )

    def test_lesson_fills_the_cell_height(self):
        """Цвет предмета должен закрашивать ячейку целиком.

        У блока занятия и ячейки должна быть одна и та же минимальная
        высота во всех ступенях. Если блок ниже, под цветом остаётся
        белая полоса; если выше — строка расползается.

        Раньше у блока стояло ``min-height: 100%``. Процент у табличной
        ячейки считается от высоты, которая для таблицы «авто», и
        превращается в ноль — цвет тогда не доходил до низа.
        """
        steps = [('', None), ('620px', '@media (max-width: 620px)'),
                 ('1800px', '@media (min-width: 1800px)'),
                 ('2400px', '@media (min-width: 2400px)'),
                 ('3200px', '@media (min-width: 3200px)')]
        for label, marker in steps:
            with self.subTest(step=label):
                text = self.css[self.css.index(marker):] if marker else self.css
                cell = re.search(
                    r'table\.sheet-table td\.slot\s*\{([^}]*)\}', text)
                lesson = re.search(
                    r'table\.sheet-table \.lesson\s*\{([^}]*)\}', text)
                self.assertIsNotNone(cell, f'нет ячейки для ступени {label}')
                self.assertIsNotNone(lesson, f'нет блока для ступени {label}')
                cell_h = re.search(
                    r'(?:min-)?height:\s*(\d+)px', cell.group(1))
                lesson_h = re.search(r'min-height:\s*(\d+)px', lesson.group(1))
                self.assertIsNotNone(
                    cell_h, f'у ячейки нет высоты на ступени {label}')
                self.assertIsNotNone(
                    lesson_h,
                    f'у блока занятия нет min-height на ступени {label} — '
                    'цвет не дойдёт до низа ячейки',
                )
                self.assertEqual(
                    cell_h.group(1), lesson_h.group(1),
                    f'на ступени {label} блок {lesson_h.group(1)}px, '
                    f'ячейка {cell_h.group(1)}px — цвет не закрасит ячейку',
                )


    def test_lesson_height_is_not_a_percentage(self):
        """Процент высоты у блока занятия не работает в таблице."""
        rule = re.search(
            r'table\.sheet-table \.lesson\s*\{([^}]*)\}', self.css).group(1)
        self.assertNotRegex(
            re.sub(r'/\*.*?\*/', '', rule, flags=re.S),
            r'min-height:\s*\d+%',
            'высота блока в процентах от ячейки не разрешается — '
            'цвет останется неполным',
        )

    def test_lesson_block_is_not_height_locked(self):
        """Блок занятия не должен иметь фиксированную высоту.

        С ``height: 100%`` блок запирался по высоте ячейки, и содержимое
        при метке «замена» выходило за его пределы: аудитория наезжала на
        время, а текст торчал за границу ячейки. ``min-height`` позволяет
        блоку и ячейке расти под содержимое.
        """
        rule = re.search(
            r'table\.sheet-table \.lesson\s*\{([^}]*)\}', self.css).group(1)
        without_comments = re.sub(r'/\*.*?\*/', '', rule, flags=re.S)
        self.assertRegex(without_comments, r'min-height:\s*\d+px',
                         'у блока занятия нет min-height в пикселях — '
                         'он не вырастет под текст')
        self.assertNotRegex(
            without_comments, r'(?<!min-)height:\s*100%',
            'у блока занятия фиксированная высота: содержимое будет '
            'вываливаться за его пределы',
        )


    def test_card_block_is_not_height_locked(self):
        """То же для карточек режима «Пары»."""
        rule = re.search(r'\.lesson-card\s*\{([^}]*)\}', self.css).group(1)
        without_comments = re.sub(r'/\*.*?\*/', '', rule, flags=re.S)
        self.assertNotRegex(without_comments, r'(?<!min-)height:\s*100%')