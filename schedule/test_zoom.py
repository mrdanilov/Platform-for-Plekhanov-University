"""Кнопки масштаба таблицы.

Ctrl +/− знают немногие, а расписание на 1920 пикселях иначе не прочитать.
Две кнопки рядом с «Выгрузкой» решают это без горячих клавиш.

Проверяется, что масштабируется только содержимое расписания (иначе кнопки
выросли бы вместе с таблицей и их стало бы не нажать), что выбор
запоминается, границы не пропускают и на печати масштаб всегда 100%.
"""

import re

from django.contrib.staticfiles import finders
from django.test import TestCase


def css_text():
    with open(finders.find('schedule/style.css'), encoding='utf-8') as fh:
        return fh.read()


class ZoomMarkupTests(TestCase):
    def html(self):
        return self.client.get('/').content.decode()

    def test_buttons_next_to_export(self):
        html = self.html()
        self.assertIn('id="zoom-in"', html)
        self.assertIn('id="zoom-out"', html)

    def test_readable_labels(self):
        html = self.html()
        self.assertIn('Приблизить', html)
        self.assertIn('Отдалить', html)

    def test_percentage_placeholder(self):
        self.assertIn('id="zoom-reset"', self.html())

    def test_menu_still_has_all_buttons(self):
        html = self.html()
        for label in ('Пары', 'Преподаватели', 'Кабинеты', 'Группы'):
            self.assertIn(label, html)


class ZoomScriptTests(TestCase):
    def setUp(self):
        html = self.client.get('/').content.decode()
        # Скриптов на странице два: тема в base.html и масштаб в index.html.
        # Берём оба — иначе проверка смотрела бы только на первый.
        self.js = '\n'.join(re.findall(r'<script>(.*?)</script>', html, re.S))

    def test_scale_applied_to_schedule_only(self):
        """Масштабируется таблица и карточки, но не меню с кнопками."""
        self.assertIn("'.tt-wrap, .cards-day'", self.js)
        self.assertIn('el.style.zoom', self.js)

    def test_menu_is_not_scaled(self):
        block = re.search(r'const block = .*?\n', self.js).group(0)
        self.assertNotIn('zoom', block)

    def test_bounds(self):
        self.assertIn('ZOOM_MIN = 80', self.js)
        self.assertIn('ZOOM_MAX = 160', self.js)
        self.assertIn('ZOOM_STEP = 10', self.js)

    def test_clamps_instead_of_overflow(self):
        self.assertIn('Math.min(ZOOM_MAX, Math.max(ZOOM_MIN', self.js)

    def test_setting_survives_reload(self):
        self.assertIn("localStorage.setItem(ZOOM_KEY", self.js)
        self.assertIn("localStorage.getItem(ZOOM_KEY)", self.js)

    def test_junk_in_storage_is_ignored(self):
        self.assertIn('raw >= ZOOM_MIN && raw <= ZOOM_MAX', self.js)

    def test_reapplied_after_block_reload(self):
        """Блок расписания подгружается без перезагрузки страницы."""
        self.assertIn('applyZoom();       // блок перезагрузили', self.js)

    def test_works_without_buttons(self):
        """Скрипт общий, а блока масштаба может не быть — падать не должен."""
        self.assertIn('if (!zoomValue) return;', self.js)
        self.assertIn('if (zoomValue && zoomOut && zoomIn)', self.js)


class ZoomCssTests(TestCase):
    def setUp(self):
        self.css = css_text()

    def test_buttons_styled(self):
        self.assertIn('.zoom-btn', self.css)
        self.assertIn('.zoom-ctl', self.css)

    def test_print_always_hundred_percent(self):
        """Принтер масштабирует сам; увеличенная таблица не влезла бы."""
        block = self.css[self.css.rfind('@media print'):]
        self.assertIn('zoom: 1 !important', block)
        self.assertIn('.tt-wrap', block)

    def test_only_one_print_block(self):
        """Два блока @media print сбивают проверки, ищущие первый."""
        self.assertEqual(self.css.count('@media print'), 1)

    def test_print_hides_menu_with_zoom(self):
        block = self.css[self.css.rfind('@media print'):]
        self.assertIn('.menu-bar', block)
        self.assertIn('display: none !important', block)