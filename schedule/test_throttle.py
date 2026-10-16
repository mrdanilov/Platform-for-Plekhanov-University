"""Лимиты живого поиска /list/ и получение IP клиента."""

from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from . import captcha as cap
from . import throttle
from .models import ContactMessage, Group, LessonTime, Teacher
from .throttle import (ADMIN_LOGIN_RATE_LIMIT, client_ip, throttle_ok)
from .views import CONTACT_RATE_LIMIT, PICKER_LIMIT, PICKER_RATE_LIMIT


class ThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_first_request_passes(self):
        self.assertTrue(throttle_ok('t1', 3, 60))

    def test_limit_is_respected(self):
        self.assertTrue(throttle_ok('t2', 3, 60))
        self.assertTrue(throttle_ok('t2', 3, 60))
        self.assertTrue(throttle_ok('t2', 3, 60))
        self.assertFalse(throttle_ok('t2', 3, 60), 'четвёртый должен не пройти')

    def test_keys_are_independent(self):
        throttle_ok('t3', 1, 60)
        self.assertFalse(throttle_ok('t3', 1, 60))
        self.assertTrue(throttle_ok('t4', 1, 60), 'другой ключ не затронут')

    def test_zero_limit_disables_throttling(self):
        self.assertTrue(throttle_ok('t5', 0, 60))


class ClientIpTests(TestCase):
    def _get(self, **headers):
        from django.test import RequestFactory

        request = RequestFactory().get('/list/', REMOTE_ADDR='10.0.0.5')
        for key, value in headers.items():
            request.META[key] = value
        return request

    @override_settings(TRUST_X_FORWARDED_FOR_IP=False)
    def test_uses_remote_addr_by_default(self):
        request = self._get(HTTP_X_FORWARDED_FOR='1.2.3.4')
        self.assertEqual(client_ip(request), '10.0.0.5',
                         'заголовку нельзя доверять без явного разрешения')

    @override_settings(TRUST_X_FORWARDED_FOR_IP=True)
    def test_forwarded_used_when_trusted(self):
        request = self._get(HTTP_X_FORWARDED_FOR='1.2.3.4, 10.0.0.9')
        self.assertEqual(client_ip(request), '1.2.3.4',
                         'берётся первый адрес цепочки — исходный клиент')

    @override_settings(TRUST_X_FORWARDED_FOR_IP=True)
    def test_falls_back_to_remote_addr(self):
        self.assertEqual(client_ip(self._get()), '10.0.0.5')

    @override_settings(TRUST_X_FORWARDED_FOR_IP=False)
    def test_header_cannot_rotate_limit_key(self):
        """С подменой заголовка лимит не обходится — это и есть смысл опции."""
        request = self._get(HTTP_X_FORWARDED_FOR='9.9.9.9')
        self.assertEqual(client_ip(request), '10.0.0.5')


class PickerLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_limit_constant_is_sane(self):
        self.assertEqual(PICKER_LIMIT, 50)
        self.assertEqual(PICKER_RATE_LIMIT, 30)

    def test_results_are_capped(self):
        for i in range(PICKER_LIMIT + 20):
            Group.objects.create(name=f'ЭК-{i:03d}', course=1)
        html = self.client.get(reverse('schedule:picker_list'),
                               {'kind': 'group'}).content.decode()
        self.assertEqual(html.count('data-id='), PICKER_LIMIT)

    def test_search_still_works_after_cap(self):
        for i in range(PICKER_LIMIT + 20):
            Group.objects.create(name=f'ЭК-{i:03d}', course=1)
        html = self.client.get(reverse('schedule:picker_list'),
                               {'kind': 'group', 'q': 'ЭК-005'}
                               ).content.decode()
        self.assertIn('ЭК-005', html)

    def test_rate_limit_returns_429(self):
        for _ in range(PICKER_RATE_LIMIT):
            self.assertEqual(
                self.client.get(reverse('schedule:picker_list'),
                                {'kind': 'group'}).status_code, 200)
        blocked = self.client.get(reverse('schedule:picker_list'),
                                   {'kind': 'group'})
        self.assertEqual(blocked.status_code, 429,
                         'превышение лимита должно быть 429, а не 404')
        self.assertIn('Слишком много запросов', blocked.content.decode())

    def test_429_body_is_readable_fragment(self):
        for _ in range(PICKER_RATE_LIMIT + 1):
            response = self.client.get(reverse('schedule:picker_list'),
                                       {'kind': 'group'})
        self.assertNotIn('<html', response.content.decode().lower())
        self.assertIn('picker-empty', response.content.decode())

    def test_picker_list_is_functional_after_changes(self):
        Teacher.objects.create(full_name='Петров П.П.', position='доцент')
        response = self.client.get(reverse('schedule:picker_list'),
                                   {'kind': 'teacher'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('Петров П.П.', response.content.decode())

    def test_pairs_still_listed(self):
        LessonTime.objects.create(number=1, start='08:00', end='09:20')
        response = self.client.get(reverse('schedule:picker_list'),
                                   {'kind': 'pair'})
        self.assertEqual(response.status_code, 200)


class TeacherFieldsTest(TestCase):
    """Список преподавателей не должен тянуть из базы контакты."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_only_four_fields_are_loaded(self):
        """Проверяем сам запрос, а не только результат: ленивая подгрузка
        contacts не должна срабатывать при переборе списка."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        Teacher.objects.create(
            full_name='Иванов И.И.', phone='+998 71 000 00 01',
            email='ivan@example.uz', position='профессор',
            department='Кафедра',
        )
        with CaptureQueriesContext(connection) as captured:
            self.client.get(reverse('schedule:picker_list'), {'kind': 'teacher'})
        joined = ' '.join(q['sql'].lower() for q in captured.captured_queries)
        self.assertNotIn('phone', joined,
                         'телефон не должен попадать в SELECT списка')
        self.assertNotIn('email', joined,
                         'почта не должна попадать в SELECT списка')

    def test_contacts_still_available_in_admin(self):
        """В админке контакты нужны — это другой запрос, он не тронут."""
        teacher = Teacher.objects.create(full_name='Сидоров С.С.',
                                         phone='+998 71 000 00 02')
        teacher.refresh_from_db()
        self.assertEqual(teacher.phone, '+998 71 000 00 02')


class ContactFormRateLimitTests(TestCase):
    """Ограничение отправок формы «Связаться с нами».

    Captcha от спама не спасает: пример арифметический, ответы перебираются
    за секунды, а каждая удачная отправка — новая строка в ContactMessage.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.url = reverse('schedule:university_contact')
        self.data = {
            'name': 'Иван Иванов',
            'email': 'ivan@example.com',
            'message': 'Когда начинается сессия?',
            'consent': '1',
        }

    def _post(self, **extra):
        """POST с верным ответом на пример: нужно, чтобы лимит был единственной
        причиной отказа, а не captcha."""
        session = self.client.session
        if session.get(cap.SESSION_KEY) is None:
            cap.issue(session)
            session.save()
        data = session[cap.SESSION_KEY]
        return self.client.post(self.url, dict(
            self.data, captcha=data['answer'],
            captcha_token=data['token'], **extra))

    def test_limit_is_small_but_usable(self):
        """Пять обращений в час — нормальный сотрудник столько не напишет,
        а бот упирается в стену почти сразу."""
        self.assertGreaterEqual(CONTACT_RATE_LIMIT, 3)
        self.assertLessEqual(CONTACT_RATE_LIMIT, 20)

    def test_posts_below_limit_are_accepted(self):
        for _ in range(CONTACT_RATE_LIMIT):
            self.assertEqual(self._post().status_code, 302)
        self.assertEqual(ContactMessage.objects.count(), CONTACT_RATE_LIMIT)

    def test_over_limit_is_refused(self):
        for _ in range(CONTACT_RATE_LIMIT):
            self._post()
        blocked = self._post()
        self.assertEqual(blocked.status_code, 429)

    def test_blocked_request_writes_nothing(self):
        """Главное: лимит защищает таблицу, а не только показывает страницу."""
        for _ in range(CONTACT_RATE_LIMIT):
            self._post()
        self._post()
        self.assertEqual(ContactMessage.objects.count(), CONTACT_RATE_LIMIT)

    def test_blocked_page_explains_why(self):
        for _ in range(CONTACT_RATE_LIMIT):
            self._post()
        html = self._post().content.decode()
        self.assertIn('uni-status is-error', html)
        self.assertIn('Слишком много обращений', html)

    def test_captcha_untouched_by_refusal(self):
        """Отказ по лимиту не съедает пример: иначе после лимита форму
        нельзя было бы заполнить даже вручную."""
        for _ in range(CONTACT_RATE_LIMIT):
            self._post()
        token = self._post().wsgi_request.session.get(cap.SESSION_KEY)
        self.assertIsNotNone(token)

    def test_limit_counts_before_field_check(self):
        """Пустая форма тоже тратит квоту: иначе бот перебирает примеры
        и набивает базу, отправляя заведомо невалидные данные."""
        url = reverse('schedule:university_contact')
        for _ in range(CONTACT_RATE_LIMIT):
            self.client.post(url, {})
        self.assertEqual(self.client.post(url, {}).status_code, 429)

    def test_other_address_is_not_limited(self):
        """Лимит на адрес, а не на весь сайт: за одним роутером может
        сидеть весь деканат."""
        for _ in range(CONTACT_RATE_LIMIT):
            self._post()
        response = self.client.post(self.url, dict(self.data),
                                    REMOTE_ADDR='10.0.0.9')
        self.assertNotEqual(response.status_code, 429)


class AdminLoginRateLimitTests(TestCase):
    """Перебор пароля к админке ограничен."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.url = '/admin/login/'
        # Настоящий лимит (30 в час) проверяется константой, а циклы ниже
        # идут на нём же — каждый рендер страницы админки небыстрый, и 60
        # запросов в тесте занимают полминуты.
        patcher = mock.patch.object(throttle, 'ADMIN_LOGIN_RATE_LIMIT', 3)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _post(self, **extra):
        return self.client.post(self.url, {
            'username': 'admin', 'password': 'wrong', 'next': '/admin/',
        }, **extra)

    def test_limit_constant(self):
        self.assertGreaterEqual(ADMIN_LOGIN_RATE_LIMIT, 10)
        self.assertLessEqual(ADMIN_LOGIN_RATE_LIMIT, 100)

    def test_login_page_stays_open(self):
        """Сама форма не ограничивается: после лимита администратор
        должен хотя бы видеть её."""
        for _ in range(throttle.ADMIN_LOGIN_RATE_LIMIT + 5):
            self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_brute_force_is_cut_off(self):
        for _ in range(throttle.ADMIN_LOGIN_RATE_LIMIT):
            self.assertEqual(self._post().status_code, 200)
        blocked = self._post()
        self.assertEqual(blocked.status_code, 429)
        self.assertIn('Слишком много попыток входа',
                      blocked.content.decode('utf-8'))

    def test_limit_is_per_address(self):
        """Соседний кабинет за другим адресом не должен попадать под лимит."""
        for _ in range(throttle.ADMIN_LOGIN_RATE_LIMIT):
            self._post(REMOTE_ADDR='10.0.0.5')
        self.assertNotEqual(self._post(REMOTE_ADDR='10.0.0.6').status_code,
                            429)