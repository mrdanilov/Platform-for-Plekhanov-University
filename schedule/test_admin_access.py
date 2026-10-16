"""Вход в админку: из интернета его нет.

Сайт с расписанием открыт всем, и это правильно: расписание и так висит
на бумаге в коридоре. А вот форма входа в интернете — это приглашение
подбирать пароль. Поэтому по умолчанию админка видна только из локальной
сети, а посторонний получает 404, и по 404 не видно, что вход вообще есть.
"""

from django.contrib.auth.models import User
from django.test import TestCase, override_settings


class AdminReachabilityTests(TestCase):
    """Кто вообще может открыть /admin/."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('a', 'a@b.c', 'pw')

    def setUp(self):
        self.client.force_login(self.admin)

    def test_local_network_can_open_admin(self):
        """Рабочий компьютер внутри вуза админку видит."""
        for address in ('127.0.0.1', '10.10.3.8', '172.20.1.5', '192.168.1.10'):
            with self.subTest(address=address):
                resp = self.client.get('/admin/', REMOTE_ADDR=address)
                self.assertEqual(resp.status_code, 200)

    def test_internet_gets_404(self):
        """Снаружи админки нет — и не 403, а 404."""
        for address in ('8.8.8.8', '185.60.216.15', '203.0.113.7'):
            with self.subTest(address=address):
                resp = self.client.get('/admin/', REMOTE_ADDR=address)
                self.assertEqual(resp.status_code, 404)

    def test_404_does_not_leak_that_admin_exists(self):
        """Тело ответа должно быть обычной страницей «не найдено».

        Если в тексте есть «вход» или «пароль», посторонний понимает, что
        админка существует, и понимает, что подбирать.
        """
        resp = self.client.get('/admin/', REMOTE_ADDR='8.8.8.8')
        body = resp.content.decode().lower()
        self.assertNotIn('type password', body)
        self.assertNotIn('password', body[:2000])


class AllowedIpsSettingTests(TestCase):
    """Перечень разрешённых адресов."""

    def setUp(self):
        self.client.force_login(
            User.objects.create_superuser('b', 'b@b.c', 'pw'))

    def test_explicit_list_wins(self):
        """Заданный адрес открывает вход, локальная сеть — нет."""
        with override_settings(ADMIN_ALLOWED_IPS='203.0.113.9'):
            ok = self.client.get('/admin/', REMOTE_ADDR='203.0.113.9')
            closed = self.client.get('/admin/', REMOTE_ADDR='10.0.0.5')
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(closed.status_code, 404)

    def test_network_can_be_given_in_cidr(self):
        with override_settings(ADMIN_ALLOWED_IPS='198.51.100.0/24'):
            inside = self.client.get('/admin/', REMOTE_ADDR='198.51.100.7')
            outside = self.client.get('/admin/', REMOTE_ADDR='198.51.101.7')
        self.assertEqual(inside.status_code, 200)
        self.assertEqual(outside.status_code, 404)

    def test_several_addresses_comma_separated(self):
        with override_settings(ADMIN_ALLOWED_IPS='203.0.113.9, 203.0.113.10'):
            for address in ('203.0.113.9', '203.0.113.10'):
                with self.subTest(address=address):
                    resp = self.client.get('/admin/', REMOTE_ADDR=address)
                    self.assertEqual(resp.status_code, 200)

    def test_mistyped_address_does_not_open_admin_for_everyone(self):
        """Опечатка в настройке не должна открыть вход наружу.

        Плохой адрес пропускается, а не трактуется как «разрешено всё».
        """
        with override_settings(ADMIN_ALLOWED_IPS='не адрес'):
            resp = self.client.get('/admin/', REMOTE_ADDR='8.8.8.8')
            self.assertEqual(resp.status_code, 404)

    def test_empty_setting_means_local_network_only(self):
        with override_settings(ADMIN_ALLOWED_IPS=''):
            local = self.client.get('/admin/', REMOTE_ADDR='10.0.0.5')
            outside = self.client.get('/admin/', REMOTE_ADDR='8.8.8.8')
        self.assertEqual(local.status_code, 200)
        self.assertEqual(outside.status_code, 404)


class LoginLinkTests(TestCase):
    """Кнопка «Войти» показывается только там, где вход есть."""

    def test_link_is_hidden_from_the_internet(self):
        """Иначе посетитель видит «Войти» и попадает в 404 — как на поломку."""
        resp = self.client.get('/', REMOTE_ADDR='8.8.8.8')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('login-btn', resp.content.decode())

    def test_link_is_visible_inside_the_network(self):
        resp = self.client.get('/', REMOTE_ADDR='10.10.3.8')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('login-btn', resp.content.decode())


class UnparseableAddressTests(TestCase):
    """Адрес, который не разобрался, считаем чужим."""

    def setUp(self):
        self.client.force_login(
            User.objects.create_superuser('c', 'c@c.c', 'pw'))

    def test_garbage_remote_addr_is_refused(self):
        resp = self.client.get('/admin/', REMOTE_ADDR='не адрес')
        self.assertEqual(resp.status_code, 404)

    def test_missing_remote_addr_is_refused(self):
        resp = self.client.get('/admin/', REMOTE_ADDR='')
        self.assertEqual(resp.status_code, 404)

    def test_forwarded_header_cannot_walk_past_the_check(self):
        """Заголовок присылает клиент, ему верить нельзя.

        Если бы адрес брался из ``X-Forwarded-For``, то ограничение
        обходилось бы добавлением одной строки в запрос.
        """
        resp = self.client.get('/admin/', REMOTE_ADDR='8.8.8.8',
                               HTTP_X_FORWARDED_FOR='10.0.0.5')
        self.assertEqual(resp.status_code, 404)

    def test_forwarded_header_is_used_when_proxy_is_trusted(self):
        """За nginx REMOTE_ADDR — это 127.0.0.1, и проверять надо другое.

        Без доверия к заголовку админка была бы открыта всему интернету:
        приложение видело бы адрес nginx и считало запрос локальным.
        """
        with override_settings(ADMIN_ALLOWED_IPS='10.0.0.0/8',
                               TRUST_X_FORWARDED_FOR_IP=True):
            allowed = self.client.get(
                '/admin/', REMOTE_ADDR='127.0.0.1',
                HTTP_X_FORWARDED_FOR='10.10.3.8')
            outside = self.client.get(
                '/admin/', REMOTE_ADDR='127.0.0.1',
                HTTP_X_FORWARDED_FOR='8.8.8.8')
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(outside.status_code, 404)

    def test_proxy_without_trust_setting_opens_admin_to_all(self):
        """Документируем опасность настройки, а не молчим о ней.

        Если сайт поставили за nginx и забыли ``DJANGO_TRUST_XFF=1``, то
        приложение видит 127.0.0.1 и открывает админку всему интернету.
        Именно поэтому в конфигурации nginx ограничение продублировано, и
        поэтому про это написано в инструкции по установке.
        """
        with override_settings(ADMIN_ALLOWED_IPS='',
                               TRUST_X_FORWARDED_FOR_IP=False):
            resp = self.client.get('/admin/', REMOTE_ADDR='127.0.0.1',
                                   HTTP_X_FORWARDED_FOR='8.8.8.8')
        self.assertEqual(resp.status_code, 200)


class PublicPagesStayOpenTests(TestCase):
    """Закрытая админка не должна закрыть само расписание."""

    @classmethod
    def setUpTestData(cls):
        from .models import Group

        cls.group = Group.objects.create(name='6301', course=1)

    def test_timetable_is_still_public(self):
        from .models import Group

        paths = ('/', '/list/?kind=group', '/today/',
                 f'/block/?view=group&id={Group.objects.first().pk}',
                 '/healthz/')
        for path in paths:
            with self.subTest(path=path):
                resp = self.client.get(path, REMOTE_ADDR='8.8.8.8')
                self.assertEqual(resp.status_code, 200,
                                 f'{path} не должен быть закрыт админкой')

    def test_export_is_still_public(self):
        resp = self.client.get('/export/timetable.csv', REMOTE_ADDR='8.8.8.8')
        self.assertEqual(resp.status_code, 200)
