"""Лимиты живого поиска /list/ и получение IP клиента."""

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Group, LessonTime, Teacher
from .throttle import client_ip, throttle_ok
from .views import PICKER_LIMIT, PICKER_RATE_LIMIT


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