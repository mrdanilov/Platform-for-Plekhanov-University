"""Тесты контраста типографики и мобильной вёрстки."""

import re

from django.contrib.staticfiles import finders
from django.test import TestCase, override_settings
from django.urls import reverse

from schedule.models import Group, Lesson, LessonTime, Subject, Teacher


def relative_luminance(hex_color):
    color = hex_color.lstrip('#')
    channels = [int(color[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        for c in channels
    ]
    return (0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2])


def contrast(fg, bg):
    a, b = relative_luminance(fg), relative_luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def css_text():
    path = finders.find('schedule/style.css')
    with open(path, encoding='utf-8') as fh:
        return fh.read()


def css_variables(text):
    """Возвращает {имя: значение} для светлой и тёмной темы."""
    themes = {}
    for theme, pattern in (
        ('light', r':root,\s*html\[data-theme="light"\]\s*\{(.*?)\n\}'),
        ('dark', r'html\[data-theme="dark"\]\s*\{(.*?)\n\}'),
    ):
        match = re.search(pattern, text, re.S)
        values = {}
        if match:
            for name, value in re.findall(r'--([\w-]+)\s*:\s*([^;]+);', match.group(1)):
                values[name] = value.strip()
        themes[theme] = values
    return themes


@override_settings(SECURE_SSL_REDIRECT=False)
class ContrastTests(TestCase):
    """WCAG AA требует 4.5:1 для мелкого текста, 7:1 — как «AAA»."""

    AA = 4.5
    AAA = 7.0

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.css = css_text()
        cls.themes = css_variables(cls.css)

    def var(self, theme, name):
        self.assertIn(name, self.themes[theme],
                      f'переменная --{name} не задана для темы {theme}')
        return self.themes[theme][name]

    def rule(self, selector):
        """Первое правило по точному селектору."""
        match = re.search(r'(?m)^' + re.escape(selector) + r'\s*\{([^}]*)\}', self.css)
        self.assertIsNotNone(match, f'нет правила {selector}')
        return match.group(1)

    # ---------------------------------------------- что должно быть читаемо

    def test_period_header_is_light_blue_not_dark(self):
        """Шапка периодов — светло-голубая с тёмным текстом.

        Насыщенный тёмно-синий вариант пробовался и отвергнут: тёмная
        полоса поверх светлой сетки читалась как отдельный блок и давила на
        расписание. Возврат к сплошному синему — регресс, ловится здесь.
        """
        block = self.rule('table.sheet-table thead th')
        self.assertIn('background: var(--head-blue)', block)
        self.assertIn('color: var(--on-blue)', block)
        for theme in ('light', 'dark'):
            head = self.var(theme, 'head-blue')
            fg = self.var(theme, 'on-blue')
            self.assertGreaterEqual(
                contrast(fg, head), self.AAA, f'тема {theme}: {fg} на {head}')
            # В светлой теме шапка обязана быть светлой: это и был смысл
            # отказа от тёмно-синего варианта. В тёмной теме шапка тёмная,
            # но проверяется контраст текста, а не тон заливки.
            if theme == 'light':
                r, g, b = (int(head[i:i + 2], 16) for i in (1, 3, 5))
                self.assertGreater(
                    r, 150, f'светлая шапка {head} ушла в тёмно-синий')
                self.assertGreater(b, r, f'шапка {head} должна быть голубоватой')

    def test_navigation_uses_soft_blue(self):
        """Верхняя панель и вкладки — мягкий синий, не тёмно-синий.

        Основной интерфейс должен оставаться светлым и воздушным.
        """
        for theme in ('light', 'dark'):
            blue = self.var(theme, 'blue')
            r, g, b = (int(blue[i:i + 2], 16) for i in (1, 3, 5))
            self.assertGreater(b, r, f'--blue {blue} должен быть голубовато-синим')
        self.assertNotEqual(self.var('light', 'blue'), '#0d47a1',
                            'вернули слишком тёмный синий основного цвета')
        topbar = self.var('light', 'topbar')
        self.assertEqual(topbar, self.var('light', 'blue'),
                         'шапка и основной синий должны совпадать, '
                         'иначе интерфейс становится пёстрым')

    def test_pair_time_has_strong_contrast(self):
        """Время пары 08:00–09:20 под номером периода."""
        block = self.css[self.css.index('table.sheet-table thead th .pair-time'):]
        self.assertIn('color: var(--on-blue-muted)', block[:400])
        for theme in ('light', 'dark'):
            ratio = contrast(self.var(theme, 'on-blue-muted'),
                             self.var(theme, 'head-blue'))
            self.assertGreaterEqual(ratio, self.AA,
                                    f'время пары в теме {theme}: {ratio:.2f}:1')

    def _resolve(self, rule, prop_name, theme, default_vars):
        """Значение свойства из правила: переменная → значение её темы."""
        match = re.search(prop_name + r':\s*var\((--[\w-]+)\)', rule)
        if match:
            return self.var(theme, match.group(1)[2:])
        match = re.search(prop_name + r':\s*(#[0-9a-fA-F]{3,6})', rule)
        if match:
            return match.group(1)
        raise AssertionError(
            f'в правиле нет {prop_name} с переменной или цветом:\n{rule[:200]}'
        )

    def test_menu_counts_readable_in_both_themes(self):
        """Счётчики групп и преподавателей видны в обеих темах.

        Здесь уже ловилась поломка: счётчик получил зашитый светлый фон,
        а текст — тематический. В тёмной теме вышло 1.2:1, то есть
        счётчика не было видно совсем. Поэтому фон обязан быть переменной,
        а не константой.
        """
        rule = self.rule('.menu-count')
        self.assertNotRegex(
            rule, r'background(?:-color)?:\s*#[0-9a-fA-F]',
            'фон счётчика зашит константой — в тёмной теме он не совпадёт '
            'с цветом текста',
        )
        for theme in ('light', 'dark'):
            bg = self._resolve(rule, 'background', theme, {})
            fg = self._resolve(rule, 'color', theme, {})
            ratio = contrast(fg, bg)
            self.assertGreaterEqual(
                ratio, self.AA,
                f'счётчик в теме {theme}: {fg} на {bg} = {ratio:.2f}:1',
            )

    def test_active_tab_count_readable(self):
        """Счётчик внутри активной вкладки.

        Текст не зашит белым: в тёмной теме --blue светлеет до небесного
        (2.2:1 с белым), поэтому цвет текста задан переменной темы.
        """
        rule = self.rule('.menu-btn.active .menu-count')
        self.assertNotRegex(rule, r'color:\s*#f',
                            'цвет счётчика зашит константой, а --blue в тёмной '
                            'теме светлеет — текст станет нечитаемым')
        for theme in ('light', 'dark'):
            fg = self._resolve(rule, 'color', theme, {})
            ratio = contrast(fg, self.var(theme, 'blue'))
            self.assertGreaterEqual(ratio, self.AA,
                                    f'тема {theme}: {fg}: {ratio:.2f}:1')

    def test_table_corner_readable_in_both_themes(self):
        """Уголок таблицы над колонкой дней — тоже через переменные."""
        rule = self.rule('table.sheet-table thead th.corner')
        for theme in ('light', 'dark'):
            bg = self._resolve(rule, 'background', theme, {})
            fg = self._resolve(rule, 'color', theme, {})
            ratio = contrast(fg, bg)
            self.assertGreaterEqual(
                ratio, self.AA,
                f'уголок в теме {theme}: {fg} на {bg} = {ratio:.2f}:1',
            )

    def test_today_marker_in_cards_follows_theme(self):
        """«Сегодня» в карточках: шапка светло-голубая в обеих темах,
        поэтому белым её красить нельзя — в светлой теме был 1.2:1."""
        rule = self.rule('.cards-day.is-today .cards-day-name')
        self.assertNotIn('#fff', rule,
                         'белый текст нечитаем на светло-голубой шапке')
        self.assertIn('var(--on-blue)', rule)
        for theme in ('light', 'dark'):
            ratio = contrast(self.var(theme, 'on-blue'),
                             self.var(theme, 'head-blue'))
            self.assertGreaterEqual(ratio, self.AA,
                                    f'тема {theme}: {ratio:.2f}:1')

    def test_language_switch_readable_in_both_themes(self):
        """Активный язык RU/UZ: плашка всегда белая.

        Значит и текст на ней должен быть константным, а не тематическим:
        в тёмной теме --blue светлеет, и надпись на белой плашке пропадала.
        """
        rule = self.rule('.lang-opt.active')
        self.assertIn('background: #fff', rule)
        self.assertNotRegex(
            rule, r'color:\s*var\(--',
            'на постоянном белом фоне цвет текста обязан быть постоянным, '
            'иначе тёмная тема его съест',
        )
        color = re.search(r'color:\s*(#[0-9a-fA-F]{3,6})', rule).group(1)
        self.assertGreaterEqual(contrast(color, '#ffffff'), self.AAA,
                                f'{color} на белом: {contrast(color, "#ffffff"):.2f}:1')

    def test_login_button_readable_in_both_themes(self):
        """Кнопка входа: в светлой теме синий на белом, в тёмной — наоборот."""
        light = contrast(self.var('light', 'blue'), '#ffffff')
        self.assertGreaterEqual(light, self.AA, f'светлая: {light:.2f}:1')
        dark_rule = self.css[self.css.index('html[data-theme="dark"] .login-btn'):]
        dark_rule = dark_rule[:dark_rule.index('\n}')]
        self.assertIn('background: var(--blue)', dark_rule)
        self.assertNotIn('#fff', dark_rule,
                         'белая надпись на светло-голубом не читается')
        self.assertGreaterEqual(contrast('#0a1219', self.var('dark', 'blue')),
                                self.AA, 'тёмная тема')

    def test_day_date_has_strong_contrast(self):
        """Дата под названием дня."""
        block = self.css[self.css.index('.day-head .day-date'):]
        self.assertIn('color: var(--muted-strong)', block[:200])
        for theme in ('light', 'dark'):
            fg = self.var(theme, 'muted-strong')
            ratio = contrast(fg, self.var(theme, 'day-grey'))
            self.assertGreaterEqual(ratio, self.AAA,
                                    f'дата в теме {theme}: {ratio:.2f}:1')

    def test_small_print_meets_aa_in_both_themes(self):
        """Подписи 10–12px: время, дата, низ таблицы, подпись в списке."""
        checks = [
            ('light', 'muted-strong', ('head-grey', 'day-grey', 'surface')),
            ('dark', 'muted-strong', ('head-grey', 'day-grey', 'surface')),
            ('light', 'muted', ('surface', 'surface-2', 'surface-3')),
            ('dark', 'muted', ('surface', 'surface-2', 'surface-3')),
        ]
        for theme, var_name, backgrounds in checks:
            fg = self.var(theme, var_name)
            for bg_name in backgrounds:
                ratio = contrast(fg, self.var(theme, bg_name))
                self.assertGreaterEqual(
                    ratio, self.AA,
                    f'--{var_name} ({fg}) на --{bg_name} в теме {theme}: {ratio:.2f}:1',
                )

    def test_muted_was_darkened(self):
        """Раньше --muted давал 3.5:1 на серой шапке — теперь минимум 4.5."""
        self.assertNotEqual(self.var('light', 'muted'), '#6b7a8d')
        ratio = contrast(self.var('light', 'muted'), self.var('light', 'head-grey'))
        self.assertGreaterEqual(ratio, self.AA)

    def test_head_text_is_readable(self):
        """Заголовок дня на серой заливке."""
        for theme in ('light', 'dark'):
            ratio = contrast(self.var(theme, 'text'), self.var(theme, 'head-grey'))
            self.assertGreaterEqual(ratio, self.AA, f'тема {theme}')

    def test_footer_and_placeholder_text(self):
        for theme in ('light', 'dark'):
            ratio = contrast(self.var(theme, 'muted'), self.var(theme, 'surface'))
            self.assertGreaterEqual(ratio, self.AA, f'тема {theme}')


@override_settings(SECURE_SSL_REDIRECT=False)
class MobileLayoutTests(TestCase):
    """Оформление под узкие экраны: закреплённая колонка дней, перенос шапки."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.css = css_text()

    def media_block(self, width):
        """Возвращает CSS внутри @media (max-width: Npx)."""
        marker = f'@media (max-width: {width}px)'
        start = self.css.index(marker)
        depth, i = 1, start + len(marker)
        while i < len(self.css) and depth:
            if self.css[i] == '{':
                depth += 1
            elif self.css[i] == '}':
                depth -= 1
            i += 1
        return self.css[start:i]

    def test_has_mobile_breakpoints(self):
        for width in (900, 620, 400):
            self.assertIn(f'@media (max-width: {width}px)', self.css)

    def test_day_column_is_sticky_on_mobile(self):
        block = self.media_block(620)
        self.assertIn('position: sticky', block)
        self.assertIn('left: 0', block)
        self.assertIn('.day-head', block)

    def test_table_scrolls_horizontally(self):
        block = self.media_block(620)
        self.assertIn('overflow-x: auto', block)
        self.assertIn('-webkit-overflow-scrolling: touch', block)
        # минимальная ширина, чтобы колонки не схлопнулись
        width = re.search(r'table\.sheet-table \{ min-width: (\d+)px', block)
        self.assertIsNotNone(width)
        self.assertGreaterEqual(int(width.group(1)), 560)

    def test_header_wraps_on_small_screens(self):
        block = self.media_block(900)
        self.assertIn('flex-wrap: wrap', block)
        self.assertIn('.topbar nav', block)

    def test_filter_buttons_become_full_width(self):
        block = self.media_block(620)
        self.assertIn('.menu-group .menu-btn', block)
        self.assertIn('flex: 1 1 auto', block)

    def test_picker_fills_the_screen(self):
        block = self.media_block(620)
        self.assertIn('.picker', block)
        self.assertIn('max-width: none', block)

    def test_meta_viewport_is_present(self):
        response = self.client.get(reverse('schedule:index'))
        self.assertContains(response, 'name="viewport"')
        self.assertContains(response, 'width=device-width')

    def test_touch_hover_is_disabled(self):
        block = self.css[self.css.index('@media (hover: none)'):]
        self.assertIn('.menu-btn:hover', block)


@override_settings(SECURE_SSL_REDIRECT=False)
class MobileContentTests(TestCase):
    """Таблица остаётся читаемой на мобильном: те же дни, пары, данных."""

    def setUp(self):
        self.time = LessonTime.objects.create(number=1, start='08:00', end='09:20')
        self.subject = Subject.objects.create(name='Финансы', short_name='Фин')
        self.group = Group.objects.create(name='ЭК-01', course=2)
        self.teacher = Teacher.objects.create(full_name='Петров И.Р.')
        Lesson.objects.create(
            group=self.group, subject=self.subject, teacher=self.teacher,
            day=3, lesson_time=self.time,
        )

    def test_structure_present_without_javascript(self):
        html = self.client.get(reverse('schedule:index')).content.decode()
        self.assertIn('sheet-table', html)
        self.assertIn('day-head', html)
        self.assertIn('pair-head', html)

    def test_selected_group_shows_data(self):
        html = self.client.get(
            reverse('schedule:index'), {'view': 'group', 'id': self.group.pk},
        ).content.decode()
        self.assertIn('Фин', html)
        self.assertIn('08:00', html)      # время пары в шапке
        self.assertIn('slot', html)

    def test_filter_buttons_work_without_javascript(self):
        """Ссылки ведут на /?view=… — интерфейс остаётся рабочим без JS."""
        html = self.client.get(reverse('schedule:index')).content.decode()
        self.assertIn('data-view="group"', html)
        html = self.client.get(
            reverse('schedule:index'), {'view': 'group', 'id': self.group.pk},
        ).content.decode()
        self.assertIn('data-week-nav', html)