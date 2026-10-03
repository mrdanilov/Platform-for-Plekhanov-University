"""Страница «Информация о университете» и форма обратной связи."""

import re

from django.test import TestCase
from django.urls import reverse

from schedule.contacts import UNIVERSITY
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
        self.assertNotIn(UNIVERSITY['webmaster_email'], html)
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
        self.assertIn(UNIVERSITY['webmaster_email'], html)
        for phone in UNIVERSITY['phones']:
            self.assertIn(phone['number'], html)
            self.assertIn(phone['note'], html)

    def test_phone_and_mail_are_links(self):
        html = self.client.get(reverse('schedule:university')).content.decode()
        self.assertEqual(html.count('href="tel:'), 2)
        self.assertEqual(html.count('href="mailto:'), 2)

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