"""Тесты шапки: логотип, «Войти», язык, тема, тени — и пустая сетка по умолчанию."""

import re
import struct
import zlib
from urllib.parse import quote

from django.test import TestCase, override_settings
from django.urls import reverse


def png_size(path):
    """Размер PNG из заголовка файла — чтобы не тянуть Pillow ради двух чисел."""
    with open(path, 'rb') as fh:
        header = fh.read(24)
    if header[:8] != b'\x89PNG\r\n\x1a\n':
        raise AssertionError(f'{path} не похож на PNG')
    width, height = struct.unpack('>II', header[16:24])
    return width, height


def png_pixels(path):
    """Пиксели RGBA-картинки без Pillow: тот же приём, что в captcha.py.

    Раскладка одна — 8 бит на канал, цветовой тип 6, — её и ждём от логотипа.
    """
    with open(path, 'rb') as fh:
        data = fh.read()
    if data[25] != 6:
        raise AssertionError(f'{path}: ожидался RGBA (тип 6), а не {data[25]}')
    width, height = struct.unpack('>II', data[16:24])

    # IDAT собираем из всех кусков: картинка может быть порезана
    stream, offset = bytearray(), 8
    while offset < len(data):
        (length,) = struct.unpack('>I', data[offset:offset + 4])
        kind = data[offset + 4:offset + 8]
        if kind == b'IDAT':
            stream += data[offset + 8:offset + 8 + length]
        elif kind == b'IEND':
            break
        offset += 12 + length

    raw, step = zlib.decompress(bytes(stream)), width * 4
    pixels, previous = [], bytearray(step)
    for row in range(height):
        start = row * (step + 1)
        # первый байт строки — способ восстановления, а не пиксель
        filter_type = raw[start]
        line = bytearray(raw[start + 1:start + 1 + step])
        # каждая строка хранит свой способ сжатия соседей — восстанавливаем
        for i in range(step):
            left = line[i - 4] if i >= 4 else 0
            up = previous[i]
            if filter_type == 0:
                pass
            elif filter_type == 1:
                line[i] = (line[i] + left) & 0xFF
            elif filter_type == 2:
                line[i] = (line[i] + up) & 0xFF
            elif filter_type == 3:
                line[i] = (line[i] + (left + up) // 2) & 0xFF
            else:                              # 4 — Paeth
                upper_left = previous[i - 4] if i >= 4 else 0
                # pa сравнивает с левым, pb — с верхним, pc — с верхним левым
                near = abs(up - upper_left)
                far = abs(left - upper_left)
                across = abs(left + up - 2 * upper_left)
                nearest = left if near <= far and near <= across else (
                    up if far <= across else upper_left)
                line[i] = (line[i] + nearest) & 0xFF
        for column in range(0, step, 4):
            pixels.append(tuple(line[column:column + 4]))
        previous = line
    return pixels


from schedule import i18n
from schedule.models import Group, Lesson, LessonTime, Subject, Teacher


@override_settings(SECURE_SSL_REDIRECT=False)
class HeaderTests(TestCase):
    def setUp(self):
        self.time = LessonTime.objects.create(number=1, start='08:00', end='09:20')
        self.subject = Subject.objects.create(name='Финансы', short_name='Фин')
        self.group = Group.objects.create(name='ЭК-01', course=2)
        self.teacher = Teacher.objects.create(full_name='Петров И.Р.')
        Lesson.objects.create(
            group=self.group, subject=self.subject, teacher=self.teacher,
            day=1, lesson_time=self.time,
        )

    def test_language_switcher_is_rendered(self):
        html = self.client.get(reverse('schedule:index')).content.decode()
        self.assertIn('lang-switch', html)
        self.assertIn('>RU<', html)
        self.assertIn('>UZ<', html)
        # ссылка ведёт через /lang/, а не просто ?lang=uz на этой же странице
        self.assertIn('href="/lang/?lang=uz&amp;next=%2F"', html)

    def test_language_link_keeps_subpage(self):
        html = self.client.get(reverse('schedule:day')).content.decode()
        self.assertIn('/lang/?lang=uz&amp;next=%2Ftoday%2F', html)

    def test_switching_language_actually_changes_page(self):
        """Клик по ссылке из шапки должен и переключать язык, и оставаться на странице."""
        html = self.client.get(reverse('schedule:day')).content.decode()
        target = html.split('href="/lang/?lang=uz&amp;next=')[1].split('"')[0]
        response = self.client.get(f'/lang/?lang=uz&next={target}')
        self.assertRedirects(response, '/today/')
        after = self.client.get(reverse('schedule:day')).content.decode()
        self.assertIn('Bugungi kun', after)
        self.assertNotIn('На сегодня', after)

    def test_switching_language_keeps_selected_group(self):
        """Выбор живёт в адресе — при смене языка он должен сохраниться."""
        page = self.client.get(
            reverse('schedule:index'), {'view': 'group', 'id': self.group.pk},
        ).content.decode()
        self.assertIn('ЭК-01', page)
        target = page.split('href="/lang/?lang=uz&amp;next=')[1].split('"')[0]
        self.assertEqual(target, '%2F%3Fview%3Dgroup%26id%3D' + str(self.group.pk))

        response = self.client.get(f'/lang/?lang=uz&next={target}')
        self.assertRedirects(
            response, f'/?view=group&id={self.group.pk}',
        )
        html = self.client.get(
            reverse('schedule:index'), {'view': 'group', 'id': self.group.pk},
        ).content.decode()
        self.assertIn('Guruhlar', html)          # язык сменился
        self.assertIn('ЭК-01', html)             # выбор сохранился

    def test_current_language_is_active(self):
        self.client.get('/lang/?lang=uz')
        html = self.client.get(reverse('schedule:index')).content.decode()
        self.assertRegex(html, r'lang-opt active"[^>]*href="/lang/\?lang=uz')

    def test_logo_is_in_header(self):
        response = self.client.get(reverse('schedule:index'))
        self.assertContains(response, 'img/logo.png')
        self.assertContains(response, 'class="logo-full"')

    def test_logo_has_no_plate(self):
        """Подложка превращала логотип в кнопку с картинкой внутри."""
        from django.contrib.staticfiles import finders

        with open(finders.find('schedule/style.css'), encoding='utf-8') as fh:
            css = fh.read()
        self.assertNotIn('logo-plate', css)
        html = self.client.get(reverse('schedule:index')).content.decode()
        self.assertNotIn('logo-plate', html)
        # у логотипа не должно быть рамки, заливки и тени
        block = re.search(r'\.topbar \.logo\s*\{([^}]*)\}', css).group(1)
        for forbidden in ('background', 'box-shadow', 'border'):
            self.assertNotIn(forbidden, block)

    def test_logo_on_blue_is_white(self):
        """Тёмные буквы на синей полосе не видны: в шапке нужен белый лок-ап.

        Проверяем по пикселям, а не по имени файла: перекрашенная картинка
        называлась бы так же, а читалась бы хуже.
        """
        import os

        from django.contrib.staticfiles import finders

        path = finders.find('schedule/img/logo.png')
        self.assertIsNotNone(path)
        self.assertGreater(os.path.getsize(path), 1000)

        visible = {pixel[:3] for pixel in png_pixels(path) if pixel[3] > 200}
        self.assertTrue(visible, 'логотип пустой — в шапке будет пустое место')
        self.assertEqual(visible, {(255, 255, 255)},
                         'в шапке логотип должен быть белым')

    def test_logo_asset_exists_and_is_cropped(self):
        """Файл логотипа лежит в static и обрезан по контенту.

        Исходник обычно приходит с пустыми полями: на шапке высотой 82px они
        съедают почти всю картинку, и логотип превращается в неразличимую полосу.
        """
        import os

        from django.contrib.staticfiles import finders

        path = finders.find('schedule/img/logo.png')
        self.assertIsNotNone(path)
        self.assertGreater(os.path.getsize(path), 1000)

        width, height = png_size(path)
        pixels = png_pixels(path)
        columns = {index % width for index, pixel in enumerate(pixels)
                   if pixel[3] > 200}
        rows = {index // width for index, pixel in enumerate(pixels)
                if pixel[3] > 200}
        # у самого края альфа чуть ниже порога, поэтому сверяем поля,
        # а не отдельные пиксели: пустые поля были по 15–20% от картинки
        margins = (min(columns), width - 1 - max(columns),
                   min(rows), height - 1 - max(rows))
        self.assertLessEqual(max(margins[:2]), width * 0.02, 'пустые поля сбоку')
        self.assertLessEqual(max(margins[2:]), height * 0.02, 'пустые поля сверху')

    def test_logo_asset_has_transparent_background(self):
        """Фон должен быть прозрачным, иначе в шапке будет белый прямоугольник."""
        from django.contrib.staticfiles import finders

        with open(finders.find('schedule/img/logo.png'), 'rb') as fh:
            data = fh.read()
        # в PNG есть цветовой тип 6 (RGBA) или 4 (оттенки серого + альфа)
        self.assertIn(data[25], (4, 6), 'нет альфа-канала')

    def test_logo_is_big_enough_to_read(self):
        """Лок-ап должен оставаться заметно крупнее «микроскопических» 34px."""
        from django.contrib.staticfiles import finders

        with open(finders.find('schedule/style.css'), encoding='utf-8') as fh:
            css = fh.read()

        def box(selector):
            return re.search(re.escape(selector) + r'\s*\{([^}]*)\}', css).group(1)

        logo_h = int(re.search(r'height:\s*(\d+)px', box('.logo-full')).group(1))
        self.assertGreaterEqual(logo_h, 44, 'логотип должен быть не меньше 44px')

    def test_logo_boxes_are_fixed_in_every_breakpoint(self):
        """Ни в одном брейкпоинте width/height не должны расходиться с min/max.

        Иначе на узком экране сработает min-width из базового правила и картинка
        увеличится по ширине, сохранив высоту — это и есть сплющивание.
        """
        from django.contrib.staticfiles import finders

        with open(finders.find('schedule/style.css'), encoding='utf-8') as fh:
            css = fh.read()

        blocks = re.findall(r'\.logo-full\s*\{([^}]*)\}', css)
        self.assertGreaterEqual(len(blocks), 3, 'ожидались базовое правило и два медиазапроса')

        for body in blocks:
            sizes = dict(re.findall(r'(width|height|min-width|min-height|'
                                    r'max-width|max-height):\s*(\d+)px', body))
            with self.subTest(selector='.logo-full'):
                for prop in ('width', 'height', 'min-width', 'min-height',
                             'max-width', 'max-height'):
                    self.assertIn(prop, sizes, f'.logo-full: нет {prop}')
                self.assertEqual(sizes['width'], sizes['min-width'],
                                 f'.logo-full: min-width расходится с width')
                self.assertEqual(sizes['width'], sizes['max-width'],
                                 f'.logo-full: max-width расходится с width')
                self.assertEqual(sizes['height'], sizes['min-height'],
                                 f'.logo-full: min-height расходится с height')
                self.assertEqual(sizes['height'], sizes['max-height'],
                                 f'.logo-full: max-height расходится с height')

    def test_logo_keeps_proportions(self):
        """Ширина и высота должны держать пропорцию исходной картинки.

        Иначе «120 лет» растягивается по горизонтали и цифры вытягиваются.
        """
        from django.contrib.staticfiles import finders

        with open(finders.find('schedule/style.css'), encoding='utf-8') as fh:
            css = fh.read()

        ratio = png_size(finders.find('schedule/img/logo.png'))
        expected = ratio[0] / ratio[1]
        for body in re.findall(r'\.logo-full\s*\{([^}]*)\}', css):
            sizes = dict(re.findall(r'(width|height):\s*(\d+)px', body))
            with self.subTest(box=f'{sizes["width"]}x{sizes["height"]}'):
                self.assertLessEqual(
                    abs(int(sizes['width']) / int(sizes['height']) - expected),
                    0.05, 'пропорции логотипа нарушены')

    def test_static_files_are_cache_busted(self):
        """Стили и картинки должны иметь ненулевую версию в адресе.

        Иначе браузер годами показывает старый CSS: URL не меняется, а файл
        перезаписается — и правки стилей «не применяются» без жёсткой перезагрузки.
        Версия 0 означает, что файл не нашёлся и антикэш не работает.
        """
        html = self.client.get('/').content.decode()
        for pattern in (r'/static/schedule/style\.css\?v=[1-9]\d*',
                        r'/static/schedule/img/logo\.png\?v=[1-9]\d*'):
            self.assertRegex(html, pattern, f'нет рабочей версии в адресе: {pattern}')
        self.assertNotRegex(html, r'\?v=0\b',
                            'версия 0 — файл не найден, антикэш не работает')
        self.assertNotRegex(html, r'/static/schedule/style\.css["\']',
                            'стили подключены без версии — будет старый кэш')

    def test_asset_version_changes_with_file(self):
        """Версия обязана зависеть от файла, иначе кэш не сбрасывается."""
        import os

        from django.contrib.staticfiles import finders
        from schedule.templatetags.schedule_tags import asset

        before = asset('schedule/style.css')
        css_path = finders.find('schedule/style.css')
        stamp = int(os.path.getmtime(css_path))
        self.assertIn(f'?v={stamp}', before)

        os.utime(css_path, (stamp + 100, stamp + 100))
        try:
            after = asset('schedule/style.css')
        finally:
            os.utime(css_path, (stamp, stamp))
        self.assertNotEqual(before, after, 'версия не меняется при правке файла')

    def test_logo_fits_topbar_height(self):
        """Логотип по высоте должен помещаться в полосу, а не вылезать из неё."""
        from django.contrib.staticfiles import finders

        with open(finders.find('schedule/style.css'), encoding='utf-8') as fh:
            css = fh.read()

        logo_h = int(re.search(r'height:\s*(\d+)px',
                               re.search(r'\.logo-full\s*\{([^}]*)\}', css).group(1)).group(1))
        bar_h = int(re.search(r'min-height:\s*(\d+)px',
                              re.search(r'\.topbar \.container\s*\{([^}]*)\}', css).group(1)).group(1))
        self.assertLess(logo_h, bar_h,
                        'логотип выше полосы — будет вылезать за шапку')

    def test_dark_theme_keeps_logo_readable(self):
        """Подложки нет — надпись белая, поэтому тёмная тема её не ломает."""
        from django.contrib.staticfiles import finders

        with open(finders.find('schedule/style.css'), encoding='utf-8') as fh:
            css = fh.read()
        self.assertNotIn('filter: brightness', css)

    def test_logo_precedes_navigation(self):
        html = self.client.get(reverse('schedule:index')).content.decode()
        logo = html.index('logo.png')
        nav = html.index('<nav>')
        self.assertLess(logo, nav, 'логотип должен быть левее навигации')

    def test_login_button_points_to_admin(self):
        response = self.client.get(reverse('schedule:index'))
        self.assertContains(response, 'class="login-btn" href="/admin/"')
        self.assertContains(response, 'Войти')

    def test_theme_toggle_present(self):
        response = self.client.get(reverse('schedule:index'))
        self.assertContains(response, 'id="theme-toggle"')
        self.assertContains(response, 'schedule_theme')

    def test_theme_applied_before_paint(self):
        html = self.client.get(reverse('schedule:index')).content.decode()
        # скрипт темы стоит раньше подключения стилей — чтобы не мигало
        self.assertLess(html.index('schedule_theme'), html.index('style.css'))

    def test_shadows_defined_for_blocks(self):
        from django.contrib.staticfiles import finders

        path = finders.find('schedule/style.css')
        self.assertIsNotNone(path, 'не найден schedule/style.css')
        with open(path, encoding='utf-8') as fh:
            css = fh.read()
        self.assertIn('--shadow-lg', css)
        self.assertIn('box-shadow: var(--shadow-lg)', css)      # блок расписания
        self.assertIn('box-shadow: var(--shadow-md)', css)      # кнопки, карточки
        self.assertIn('box-shadow: var(--shadow-xl)', css)      # всплывающее окно
        self.assertIn('html[data-theme="dark"]', css)
        # у тёмной темы есть свой набор переменных
        dark = css.split('html[data-theme="dark"]', 1)[1]
        self.assertIn('--surface:', dark)
        self.assertIn('--text:', dark)

    # ------------------------------------------------------ язык RU / UZ

    def test_default_language_is_russian(self):
        response = self.client.get(reverse('schedule:index'))
        self.assertContains(response, 'Группы')
        self.assertContains(response, 'Преподаватели')

    def test_language_switch_sets_cookie(self):
        response = self.client.get('/lang/?lang=uz')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.cookies[i18n.COOKIE_NAME].value, 'uz')

    def test_uz_translates_ui(self):
        self.client.get('/lang/?lang=uz')
        response = self.client.get(reverse('schedule:index'))
        self.assertContains(response, 'Guruhlar')
        self.assertContains(response, "O&#x27;qituvchilar")

    def test_uz_translates_weekdays(self):
        self.client.get('/lang/?lang=uz')
        html = self.client.get(
            reverse('schedule:block'), {'view': 'group', 'id': self.group.pk},
        ).content.decode()
        self.assertIn('Dushanba', html)
        self.assertIn('Shanba', html)
        self.assertNotIn('Понедельник', html)

    def test_language_switch_back_to_russian(self):
        self.client.get('/lang/?lang=uz')
        self.client.get('/lang/?lang=ru')
        response = self.client.get(reverse('schedule:index'))
        self.assertContains(response, 'Группы')

    def test_switch_ignores_unknown_language(self):
        response = self.client.get('/lang/?lang=zz')
        self.assertEqual(response.cookies[i18n.COOKIE_NAME].value, 'ru')

    def test_language_link_keeps_current_page(self):
        response = self.client.get('/lang/?lang=uz&next=/today/')
        self.assertEqual(response['Location'], '/today/')

    def test_language_switch_blocks_offsite_next(self):
        """Внешний адрес в next игнорируем — открытый редирект недопустим."""
        response = self.client.get('/lang/?lang=uz&next=https://evil.example/')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith('/'))
        response = self.client.get('/lang/?lang=uz&next=//evil.example/')
        self.assertTrue(response['Location'].startswith('/'))

    def test_language_switch_never_redirects_offsite(self):
        """Ни next, ни Referer не должны увести пользователя на чужой сайт.

        Значение вида //evil.example проходит наивную проверку startswith('/'),
        но браузер читает его как protocol-relative URL.
        """
        host = self.client.get(reverse('schedule:index')).wsgi_request.get_host()
        # (next, referer) -> ожидаемый Location
        cases = [
            ('', '//evil.example'),                    # referer как источник уязвимости
            ('', '//evil.example/phish'),
            ('', r'/\evil.example'),                   # Chrome считает \ тем же, что /
            ('', '///evil.example'),
            ('', 'https://evil.example'),
            ('', 'http://evil.example'),
            ('', 'javascript:alert(1)'),
            ('', 'https://evil.example\t/today/'),
            ('//evil.example', ''),                    # тот же обход через сам next
            (r'/\evil.example', ''),
            ('///evil.example', ''),
            ('https://evil.example/', ''),
            ('javascript:alert(1)', ''),
            ('/today/', '//evil.example'),             # валидный next сильнее referer
            ('', '//'),                                # «//» — это корень, не хост
            ('', ''),
            ('', f'https://{host}/today/'),            # свой хот — можно
            ('', '/today/'),
        ]
        for nxt, referer in cases:
            with self.subTest(next=nxt, referer=referer):
                response = self.client.get(
                    '/lang/?lang=uz',
                    {'next': nxt} if nxt else {},
                    HTTP_REFERER=referer,
                )
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.cookies[i18n.COOKIE_NAME].value, 'uz')
                location = response['Location']
                on_site = (
                    location == '/' or location.startswith(f'https://{host}/')
                    or (location.startswith('/') and not location.startswith('//'))
                )
                self.assertTrue(on_site, f'{nxt!r} / {referer!r} -> {location!r}')

    def test_language_switch_keeps_valid_next_with_hostile_referer(self):
        """Плохой Referer не ломает переход на валидный next."""
        for nxt, expected in [('/today/', '/today/'), ('/', '/'),
                              ('/list/?view=group', '/list/?view=group')]:
            with self.subTest(next=nxt):
                response = self.client.get(
                    '/lang/?lang=uz&next=' + quote(nxt, safe=''),
                    HTTP_REFERER='//evil.example',
                )
                self.assertEqual(response['Location'], expected)

    # ------------------------------- пустая сетка до выбора фильтра

    def test_grid_is_shown_but_empty_by_default(self):
        response = self.client.get(reverse('schedule:index'))
        html = response.content.decode()
        # сетка есть: дни в строках, пары в колонках
        self.assertContains(response, 'sheet-table')
        self.assertEqual(len(response.context['grid']['rows']), 6)
        self.assertEqual(len(response.context['grid']['lesson_times']), 1)
        # но ячейки пустые — ни одного предмета
        self.assertNotContains(response, 'Фин')
        self.assertNotContains(response, 'Петров')
        self.assertIn('slot-empty', html)

    def test_grid_cells_are_empty_lists(self):
        response = self.client.get(reverse('schedule:index'))
        for row in response.context['grid']['rows']:
            for lesson_time in response.context['grid']['lesson_times']:
                self.assertEqual(row['cells'][lesson_time.pk], [])

    def test_structure_survives_without_any_lesson_times(self):
        # сначала убираем занятия — иначе PROTECT на внешнем ключе
        Lesson.objects.all().delete()
        LessonTime.objects.all().delete()
        response = self.client.get(reverse('schedule:index'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['empty'])

    def test_empty_grid_has_no_toolbar(self):
        """Пока ничего не выбрано — заголовка и стрелок недель в блоке нет."""
        html = self.client.get(reverse('schedule:index')).content.decode()
        block = html[html.index('id="schedule-block"'):html.index('picker-backdrop')]
        self.assertNotIn('data-week-nav', block)
        self.assertNotIn('block-title', block)
        self.assertNotIn('/export/timetable.xlsx', block)

    def test_placeholder_hint_present(self):
        response = self.client.get(reverse('schedule:index'))
        self.assertContains(response, 'Выберите группу, кабинет или преподавателя')

    # ------------------------------------------- после выбора фильтра

    def test_room_prefix_is_rendered(self):
        from schedule.models import Room

        room = Room.objects.create(name='305')
        Lesson.objects.filter(group=self.group).update(room=room)
        html = self.client.get(
            reverse('schedule:block'), {'view': 'group', 'id': self.group.pk},
        ).content.decode()
        self.assertIn('Ауд.', html)
        self.client.get('/lang/?lang=uz')
        html = self.client.get(
            reverse('schedule:block'), {'view': 'group', 'id': self.group.pk},
        ).content.decode()
        self.assertIn('Xona', html)

    def test_group_filter_fills_cells(self):
        response = self.client.get(
            reverse('schedule:index'), {'view': 'group', 'id': self.group.pk},
        )
        self.assertFalse(response.context['empty'])
        self.assertContains(response, 'Фин')

    def test_teacher_filter_fills_cells(self):
        response = self.client.get(
            reverse('schedule:index'), {'view': 'teacher', 'id': self.teacher.pk},
        )
        self.assertContains(response, 'Фин')

    def test_block_endpoint_still_404_without_selection(self):
        self.assertEqual(self.client.get(reverse('schedule:block')).status_code, 404)


@override_settings(SECURE_SSL_REDIRECT=False)
class AdminTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User

        self.password = 'admin-test-pass-123'
        self.admin = User.objects.create_superuser(
            'tester', 't@example.com', self.password,
        )

    def test_login_button_target_exists(self):
        response = self.client.get(reverse('admin:index'))
        self.assertEqual(response.status_code, 302)

    def test_all_schedules_reachable_in_admin(self):
        self.client.login(username='tester', password=self.password)
        for model in ('lesson', 'group', 'teacher', 'subject', 'room',
                      'lessontime', 'lessontype', 'substitution'):
            url = reverse(f'admin:schedule_{model}_changelist')
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)

    def test_admin_can_create_group(self):
        self.client.login(username='tester', password=self.password)
        response = self.client.post(
            reverse('admin:schedule_group_add'),
            {'name': 'ЭК-99', 'course': 3, 'specialty': ''},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Group.objects.filter(name='ЭК-99').exists())

    def test_admin_can_create_teacher(self):
        self.client.login(username='tester', password=self.password)
        response = self.client.post(
            reverse('admin:schedule_teacher_add'),
            {'full_name': 'Новиков Н.Н.', 'position': '', 'department': '',
             'phone': '', 'email': '', 'active': 'on'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Teacher.objects.filter(full_name='Новиков Н.Н.').exists())

    def test_admin_can_delete_group(self):
        group = Group.objects.create(name='ЭК-77', course=1)
        self.client.login(username='tester', password=self.password)
        response = self.client.post(
            reverse('admin:schedule_group_changelist'),
            {'action': 'delete_selected', '_selected_action': [str(group.pk)],
             'post': 'yes'},
        )
        self.assertIn(response.status_code, (200, 302))
        self.assertFalse(Group.objects.filter(pk=group.pk).exists())

    def test_admin_can_create_and_delete_lesson(self):
        time = LessonTime.objects.create(number=1, start='08:00', end='09:20')
        group = Group.objects.create(name='ЭК-55', course=1)
        subject = Subject.objects.create(name='Финансы')
        teacher = Teacher.objects.create(full_name='Петров И.Р.')
        self.client.login(username='tester', password=self.password)
        response = self.client.post(
            reverse('admin:schedule_lesson_add'),
            {'group': group.pk, 'subject': subject.pk, 'day': 1,
             'lesson_time': time.pk, 'week_parity': 0, 'note': '', 'raw_text': ''},
        )
        self.assertEqual(response.status_code, 302)
        lesson = Lesson.objects.get(group=group)
        self.client.post(
            reverse('admin:schedule_lesson_changelist'),
            {'action': 'delete_selected', '_selected_action': [str(lesson.pk)],
             'post': 'yes'},
        )
        self.assertFalse(Lesson.objects.filter(group=group).exists())

    def test_back_to_schedule_route(self):
        response = self.client.get('/admin/schedule/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/')

    def test_delete_selected_redirects(self):
        group = Group.objects.create(name='ЭК-66', course=1)
        self.client.login(username='tester', password=self.password)
        response = self.client.post(
            reverse('admin:schedule_group_changelist'),
            {'action': 'delete_selected', '_selected_action': [str(group.pk)],
             'post': 'yes'},
        )
        self.assertIn(response.status_code, (200, 302))
        self.assertFalse(Group.objects.filter(pk=group.pk).exists())