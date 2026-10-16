"""Страница «Информация о университете» и форма обратной связи."""

import re
from urllib.parse import unquote

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from schedule.contacts import (
    MAP_CENTER,
    MAP_LATITUDE,
    MAP_LONGITUDE,
    MAP_QUERY,
    MAP_URL,
    MAP_ZOOM,
    UNIVERSITY,
)
from schedule.models import ContactMessage


class UniversityPageTests(TestCase):
    """Раздел должен быть отдельной страницей, а не окном поверх другой."""

    def test_nav_links_to_page(self):
        html = self.client.get('/').content.decode()
        self.assertIn(reverse('schedule:university'), html)
        self.assertIn('Университет', html)

    def test_no_modal_anywhere(self):
        """Оверлея и модального окна быть не должно ни на одной странице.

        `role="dialog"` и `aria-modal` проверять нельзя: такой же диалог есть
        у пикера групп на главной, он к этому разделу отношения не имеет.
        """
        for url in ('/', '/today/', '/university/'):
            html = self.client.get(url).content.decode()
            with self.subTest(url=url):
                for dead in ('uni-overlay', 'uni-modal', 'uni-close',
                             'uni-col-info', 'uni-col-form'):
                    self.assertNotIn(dead, html, f'осталось окно: {dead}')

    def test_no_university_script(self):
        """JS-обработчиков окна быть не должно: страница обычная."""
        html = self.client.get(reverse('schedule:university')).content.decode()
        self.assertNotIn('university.js', html)
        self.assertNotIn('preventDefault', html)
        self.assertNotIn('XMLHttpRequest', html)

    def test_page_extends_base(self):
        """Страница наследует общий каркас: шапка, тема, язык, футер."""
        html = self.client.get(reverse('schedule:university')).content.decode()
        self.assertIn('class="topbar"', html)
        self.assertIn('id="theme-toggle"', html)
        self.assertIn('lang-switch', html)
        self.assertIn('<footer>', html)

    def test_page_is_its_own_template(self):
        response = self.client.get(reverse('schedule:university'))
        self.assertTemplateUsed(response, 'schedule/university.html')
        self.assertEqual(response.status_code, 200)

    def test_nav_item_marked_active(self):
        html = self.client.get(reverse('schedule:university')).content.decode()
        nav = html[html.index('<nav>'):html.index('</nav>')]
        self.assertRegex(nav, r'href="/university/"[^>]*class="[^"]*active',
                         'активный пункт должен подсветиться')

    def test_other_pages_have_no_university_content(self):
        """Контакты не должны протекать на другие страницы."""
        html = self.client.get('/').content.decode()
        self.assertNotIn(UNIVERSITY['email'], html)
        self.assertNotIn(UNIVERSITY['address'], html)
        self.assertNotIn('uni-form', html)
        self.assertNotIn('<iframe', html)

    def test_two_columns_layout(self):
        html = self.client.get(reverse('schedule:university')).content.decode()
        self.assertIn('class="uni-grid"', html)
        self.assertEqual(html.count('class="card uni-panel"'), 2)
        # контакты раньше формы: слева контакты и карта, справа форма
        self.assertLess(html.index('uni-contacts'), html.index('uni-form'))

    def test_contacts_shown(self):
        html = self.client.get(reverse('schedule:university')).content.decode()
        self.assertIn('Плеханова', html)          # заголовок h1
        self.assertIn(UNIVERSITY['address'], html)
        self.assertIn(UNIVERSITY['email'], html)
        for phone in UNIVERSITY['phones']:
            self.assertIn(phone['number'], html)
            self.assertIn(phone['note'], html)

    def test_phone_and_mail_are_links(self):
        html = self.client.get(reverse('schedule:university')).content.decode()
        self.assertEqual(html.count('href="tel:'), 2)
        self.assertEqual(html.count('href="mailto:'), 1)

    def test_webmaster_is_gone(self):
        """Веб-мастер удалён: и из данных, и из разметки."""
        self.assertNotIn('webmaster_email', UNIVERSITY)
        html = self.client.get(reverse('schedule:university')).content.decode()
        self.assertNotIn('12345@mail.ru', html)
        self.assertNotIn('uni-webmaster', html)
        from schedule.i18n import RU, UZ
        for table in (RU, UZ):
            self.assertNotIn(
                'university_webmaster', table,
                'подпись веб-мастера больше не используется — ключ можно убрать',
            )

    def test_address_is_the_current_one(self):
        """Адрес приведён к виду РЭУ: индекс, «массив Ялангач», дом 3.

        Проверяется по существу, а не сравнением с константой: правка
        адреса в contacts.py и проверка, импортирующая ту же константу,
        проходят вместе с любой опечаткой внутри неё.
        """
        address = UNIVERSITY['address']
        for part in ('100164', 'Республика Узбекистан', 'Ташкент',
                     'Мирзо Улугбекский', 'Ялангач', 'Шахриобод'):
            with self.subTest(part=part):
                self.assertIn(part, address)
        self.assertNotIn('koʻchasi', address)
        self.assertNotIn('Mirzo Ulug', address)

    def test_email_is_the_current_one(self):
        self.assertEqual(UNIVERSITY['email'], 'info@reu.uz')
        self.assertNotIn('reu_plekhanov', UNIVERSITY['email'])
        html = self.client.get(reverse('schedule:university')).content.decode()
        self.assertIn('href="mailto:info@reu.uz"', html)
        self.assertNotIn('info@reu_plekhanov.uz', html)

    def test_map_query_names_the_branch_and_district(self):
        """Запрос остаётся осмысленным текстом, хотя в URL он больше не
        используется: хранится для людей, читающих код, и на случай
        возврата к поиску по адресу.

        Апостроф в «koʻchasi» поиском по карте не распознаётся, поэтому
        запрос переписан латиницей без диакритики.
        """
        self.assertIn('Plekhanova', MAP_QUERY)
        self.assertIn('Yalangach', MAP_QUERY)
        self.assertIn("ko'chasi", MAP_QUERY)
        for bad in ('ʻ', 'ʼ', 'koʻchasi'):
            with self.subTest(bad=bad):
                self.assertNotIn(bad, MAP_QUERY)

    def test_map_url_is_clean(self):
        """В URL не должно быть символов, ломающих атрибут src."""
        for bad in ("'", '"', '<', '>', ' '):
            with self.subTest(bad=bad):
                self.assertNotIn(bad, MAP_URL)

    def test_map_has_explicit_marker_not_search(self):
        """Метка должна ставиться координатой, а не поиском по адресу.

        При mode=search булавка появляется только если поиск что-то нашёл.
        У неё не нашлось — карта открывалась без метки. Поэтому заданы
        координаты центра и явная точка pt.
        """
        self.assertNotIn('mode=search', MAP_URL,
                         'поиск по адресу не даёт гарантированной метки')
        self.assertIn('pt=', MAP_URL, 'не задана метка на карте')
        self.assertIn('%2Cpm2rdm', MAP_URL,
                      'метка должна быть полноразмерной (не пунктирной)')
        self.assertIn(f'll={MAP_CENTER}', MAP_URL)
        self.assertIn(f'z={MAP_ZOOM}', MAP_URL)

    def test_map_markers_are_inside_tashkent(self):
        """Координаты не должны уехать за пределы Ташкента."""
        lon, lat = (float(v) for v in unquote(MAP_CENTER).split(','))
        self.assertTrue(41.0 < lat < 41.6, f'широта {lat} вне Ташкента')
        self.assertTrue(68.5 < lon < 70.0, f'долгота {lon} вне Ташкента')

    def test_map_points_at_the_university_building(self):
        """Метка должна стоять на корпусе Плеханова.

        Координаты заданы заказчиком: 41.349544, 69.336402. Проверяется
        именно это здание — прошлый адрес ставил метку в стороне от
        корпуса, потому что опирался на текстовый поиск.

        Порядок важен: в Python широта идёт первой, в параметре Яндекса
        `ll` первой идёт долгота. Перестановка даёт точку в 200 км.
        """
        self.assertEqual(MAP_LATITUDE, 41.349544)
        self.assertEqual(MAP_LONGITUDE, 69.336402)

        point = unquote(MAP_CENTER).split(',')
        self.assertEqual(float(point[0]), MAP_LONGITUDE,
                         'в ll= первой должна идти долгота')
        self.assertEqual(float(point[1]), MAP_LATITUDE,
                         'в ll= второй должна идти широта')

        self.assertIn(f'll={MAP_LONGITUDE}%2C{MAP_LATITUDE}', MAP_URL)
        self.assertIn(f'pt={MAP_LONGITUDE}%2C{MAP_LATITUDE}%2Cpm2rdm', MAP_URL)

    def test_map_is_responsive_iframe(self):
        html = self.client.get(reverse('schedule:university')).content.decode()
        self.assertIn('<iframe', html)
        self.assertIn('class="uni-map"', html)
        self.assertIn('loading="lazy"', html)

    def test_form_fields(self):
        html = self.client.get(reverse('schedule:university')).content.decode()
        self.assertIn('type="text"', html)
        self.assertIn('type="email"', html)
        self.assertIn('<textarea', html)
        self.assertGreaterEqual(html.count('placeholder='), 3)
        self.assertIn('name="consent"', html)
        self.assertEqual(html.count('type="checkbox"'), 1, 'чекбокс один — согласие')
        self.assertIn('csrfmiddlewaretoken', html)
        # метки связаны с полями
        for field in ('uni-name', 'uni-email', 'uni-message'):
            self.assertIn(f'for="{field}"', html)

    def test_captcha_image_shown(self):
        """Проверка — картинка с примером, а не чекбокс-заглушка."""
        html = self.client.get(reverse('schedule:university')).content.decode()
        self.assertIn(reverse('schedule:university_captcha'), html)
        self.assertIn('class="uni-captcha-img"', html)
        self.assertIn('name="captcha"', html)
        self.assertIn('name="captcha_token"', html)
        self.assertNotIn('not_robot', html)
        self.assertNotIn("I'm not a robot", html)
        # у картинки есть размеры, иначе при загрузке прыгает вёрстка
        self.assertIn('width="350"', html)
        self.assertIn('height="62"', html)

    def test_captcha_refresh_link(self):
        html = self.client.get(reverse('schedule:university')).content.decode()
        self.assertIn('?refresh=1', html)
        self.assertIn('Другой пример', html)

    def test_captcha_endpoint_returns_png(self):
        response = self.client.get(reverse('schedule:university_captcha'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/png')
        self.assertTrue(response.content.startswith(b'\x89PNG\r\n\x1a\n'),
                        'ответ должен быть настоящим PNG')
        self.assertIn('no-store', response['Cache-Control'])

    def test_answer_never_in_html(self):
        """Ответ не должен утекать в разметку: ни в alt, ни в скрытые поля."""
        from schedule import captcha as cap

        self.client.get(reverse('schedule:university'))
        answer = self.client.session[cap.SESSION_KEY]['answer']
        html = self.client.get(reverse('schedule:university')).content.decode()

        # смотрим только alt: в src лежит токен из hex, где цифры встречаются
        alt = re.search(r'<img[^>]*uni-captcha-img[^>]*alt="([^"]*)"', html)
        self.assertIsNotNone(alt)
        self.assertNotIn(answer, alt.group(1), 'ответ не должен быть в alt')

        for value in re.findall(r'<input[^>]*type="hidden"[^>]*>', html):
            self.assertNotIn(f'value="{answer}"', value,
                             'ответ не должен быть в скрытом поле')

    def test_submit_is_real_post(self):
        """Без JS отправка — обычный POST с переходом обратно."""
        html = self.client.get(reverse('schedule:university')).content.decode()
        form = html[html.index('<form'):html.index('</form>')]
        self.assertIn('method="post"', form)
        self.assertIn(reverse('schedule:university_contact'), form)


class UniversityFormTests(TestCase):
    def setUp(self):
        # Счётчик отправок живёт в кэше и общий для всех тестов: без очистки
        # шестой POST в процессе падает с 429, и тесты формы ломаются о лимит,
        # который проверяется отдельным тестом ниже.
        cache.clear()
        self.addCleanup(cache.clear)
        self.url = reverse('schedule:university_contact')
        self.data = {
            'name': 'Иван Иванов',
            'email': 'ivan@example.com',
            'message': 'Когда начинается сессия?',
            'consent': '1',
        }

    def _solve_captcha(self, answer=None):
        """Пройти проверку: взять ответ из сессии и подставить его в POST.

        Сессию нужно сохранить вручную: тестовый клиент не делает этого сам,
        иначе сервер при POST увидит пустую сессию и ответ не совпадёт.
        """
        from schedule import captcha as cap

        session = self.client.session
        data = session.get(cap.SESSION_KEY)
        if data is None:
            cap.issue(session)
            session.save()
            data = session[cap.SESSION_KEY]
        if answer is None:
            answer = data['answer']
        return data['token'], answer

    def test_message_saved_and_redirected(self):
        token, answer = self._solve_captcha()
        response = self.client.post(self.url, dict(self.data,
                                                    captcha=answer,
                                                    captcha_token=token))
        self.assertRedirects(response, reverse('schedule:university') + '?sent=1')
        self.assertEqual(ContactMessage.objects.count(), 1)
        saved = ContactMessage.objects.get()
        self.assertEqual(saved.name, 'Иван Иванов')
        self.assertEqual(saved.email, 'ivan@example.com')
        self.assertEqual(saved.message, 'Когда начинается сессия?')
        self.assertTrue(saved.consent)

    def test_confirmation_on_page(self):
        html = self.client.get(
            reverse('schedule:university') + '?sent=1').content.decode()
        self.assertIn('Спасибо', html)
        self.assertIn('class="uni-status"', html)
        self.assertNotIn('is-error', html)

    def test_wrong_captcha_rejected(self):
        token, answer = self._solve_captcha()
        wrong = '9999' if answer != '9999' else '8888'
        response = self.client.post(self.url, dict(self.data,
                                                    captcha=wrong,
                                                    captcha_token=token))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ContactMessage.objects.count(), 0)
        self.assertIn('is-error', response.content.decode())

    def test_captcha_missing_rejected(self):
        response = self.client.post(self.url, self.data)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ContactMessage.objects.count(), 0)

    def test_captcha_token_must_match(self):
        """Подставленный чужой токен не проходит, даже если ответ верный."""
        token, answer = self._solve_captcha()
        response = self.client.post(self.url, dict(self.data,
                                                    captcha=answer,
                                                    captcha_token='подделка'))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ContactMessage.objects.count(), 0)

    def test_captcha_is_single_use(self):
        """Один и тот же пример нельзя переиспользовать."""
        token, answer = self._solve_captcha()
        payload = dict(self.data, captcha=answer, captcha_token=token)
        first = self.client.post(self.url, payload)
        self.assertEqual(first.status_code, 302)

        second = self.client.post(reverse('schedule:university_contact'), payload)
        self.assertEqual(second.status_code, 400,
                         'второй раз тот же пример приниматься не должен')
        self.assertEqual(ContactMessage.objects.count(), 1)

    def test_new_captcha_after_failure(self):
        token, answer = self._solve_captcha()
        self.client.post(self.url, dict(self.data, captcha='нет',
                                        captcha_token=token))
        from schedule import captcha as cap
        fresh = self.client.session.get(cap.SESSION_KEY)
        self.assertIsNotNone(fresh, 'после ошибки должен выдаться новый пример')
        # Проверяем смену токена, а не отличие ответа: генератор случайный,
        # и новый пример вполне может совпасть со старым.
        self.assertNotEqual(fresh['token'], token)

    def test_missing_consent_shows_error(self):
        token, answer = self._solve_captcha()
        payload = dict(self.data, captcha=answer, captcha_token=token)
        payload.pop('consent')
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ContactMessage.objects.count(), 0)
        self.assertIn('is-error', response.content.decode())

    def test_bad_email_rejected(self):
        token, answer = self._solve_captcha()
        response = self.client.post(self.url, dict(self.data, email='не-почта',
                                                    captcha=answer,
                                                    captcha_token=token))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ContactMessage.objects.count(), 0)

    def test_empty_fields_rejected(self):
        token, answer = self._solve_captcha()
        response = self.client.post(self.url, {'name': '', 'email': '', 'message': '',
                                               'captcha': answer,
                                               'captcha_token': token})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ContactMessage.objects.count(), 0)

    def test_long_values_truncated_not_crashed(self):
        token, answer = self._solve_captcha()
        response = self.client.post(self.url, dict(
            self.data, name='A' * 500, message='B' * 9000,
            captcha=answer, captcha_token=token))
        self.assertRedirects(response, reverse('schedule:university') + '?sent=1')
        saved = ContactMessage.objects.get()
        self.assertEqual(len(saved.name), 120)
        self.assertEqual(len(saved.message), 4000)

    def test_get_redirects_to_page(self):
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse('schedule:university'))

    def test_csrf_is_enforced(self):
        """Без токена POST отклоняется — защита от подделки формы."""
        client = self.client_class(enforce_csrf_checks=True)
        self.assertEqual(client.post(self.url, self.data).status_code, 403)


class UniversityContactsContrastTests(TestCase):
    """Читаемость блока контактов.

    Панель полупрозрачная, и под ссылками видна фотография. Проверяется
    худший случай: подложка не белая, а тонированная multiply фотография.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from django.contrib.staticfiles import finders
        with open(finders.find('schedule/style.css'), encoding='utf-8') as fh:
            cls.css = fh.read()
        from schedule.test_ui import css_variables
        cls.themes = css_variables(cls.css)

    def contrast(self, fg, bg):
        from schedule.test_ui import contrast
        return contrast(fg, bg)

    def rule(self, selector):
        match = re.search(r'(?m)^' + re.escape(selector) + r'\s*\{([^}]*)\}',
                          self.css)
        self.assertIsNotNone(match, f'нет правила {selector}')
        return match.group(1)

    def resolve(self, rule, prop, theme):
        match = re.search(prop + r':\s*var\((--[\w-]+)\)', rule)
        self.assertIsNotNone(match, f'в правиле нет {prop} через переменную')
        return self.themes[theme][match.group(1)[2:]]

    def worst_case_background(self, theme):
        """Панель .card = --surface-glass (0.95 белого) поверх --bg."""
        return self.themes[theme]['bg']

    def test_phone_and_email_links_are_readable(self):
        """Ссылки были 3.9:1 — ниже порога. Цифры телефона тонули."""
        rule = self.rule('.uni-contact-link')
        for theme in ('light', 'dark'):
            fg = self.resolve(rule, 'color', theme)
            bg = self.worst_case_background(theme)
            ratio = self.contrast(fg, bg)
            with self.subTest(theme=theme):
                self.assertGreaterEqual(
                    ratio, 4.5,
                    f'ссылка контакта в теме {theme}: {fg} на {bg} '
                    f'= {ratio:.2f}:1',
                )

    def test_link_colour_is_not_the_pale_default_blue(self):
        """Конкретная причина: --blue на стеклянной панели даёт 3.9:1."""
        rule = self.rule('.uni-contact-link')
        self.assertNotIn('var(--blue)', rule.replace('var(--blue-deep)', '')
                         .replace('var(--blue-dark)', ''),
                         'ссылка контакта снова на основном --blue')

    def test_labels_are_dark_not_muted(self):
        for selector in ('.uni-contact-label', '.uni-contact-note'):
            rule = self.rule(selector)
            for theme in ('light', 'dark'):
                fg = self.resolve(rule, 'color', theme)
                bg = self.worst_case_background(theme)
                with self.subTest(selector=selector, theme=theme):
                    self.assertGreaterEqual(
                        self.contrast(fg, bg), 4.5,
                        f'{selector} в теме {theme}: {fg} на {bg} '
                        f'= {self.contrast(fg, bg):.2f}:1',
                    )

    def test_address_is_readable(self):
        rule = self.rule('.uni-address')
        for theme in ('light', 'dark'):
            fg = self.resolve(rule, 'color', theme)
            bg = self.worst_case_background(theme)
            with self.subTest(theme=theme):
                self.assertGreaterEqual(self.contrast(fg, bg), 4.5)


class UniversityStyleTests(TestCase):
    def test_css_has_no_modal_rules(self):
        """Стилей оверлея и модального окна в таблице быть не должно."""
        from django.contrib.staticfiles import finders

        with open(finders.find('schedule/style.css'), encoding='utf-8') as fh:
            css = fh.read()
        for dead in ('.uni-overlay', '.uni-modal', '.uni-close', '.uni-col',
                     'uni-locked', 'is-open'):
            self.assertNotIn(dead, css, f'остался стиль окна: {dead}')

    def test_css_grid_and_responsive(self):
        from django.contrib.staticfiles import finders

        with open(finders.find('schedule/style.css'), encoding='utf-8') as fh:
            css = fh.read()

        grid = re.search(r'\.uni-grid\s*\{([^}]*)\}', css).group(1)
        self.assertIn('1fr 1fr', grid, 'на десктопе должно быть две колонки')

        start = css.index('@media (max-width: 900px)', css.index('.uni-grid'))
        block = css[start:css.index('@media (max-width: 400px)', start)]
        self.assertRegex(block, r'\.uni-grid\s*\{[^}]*grid-template-columns:\s*1fr',
                         'на мобильных колонки должны сложиться в одну')

    def test_form_styles_present(self):
        from django.contrib.staticfiles import finders

        with open(finders.find('schedule/style.css'), encoding='utf-8') as fh:
            css = fh.read()

        for selector in ('.uni-input', '.uni-check', '.uni-check-box',
                         '.uni-submit', '.uni-map', '.uni-contact-row'):
            self.assertIn(selector, css, f'нет стилей {selector}')
        self.assertIn('.uni-submit:hover', css)
        self.assertIn('.uni-input:focus', css)

    def test_modal_assets_deleted(self):
        from django.contrib.staticfiles import finders

        self.assertIsNone(finders.find('schedule/university.js'),
                          'JS модального окна должен быть удалён')