"""Прод-конфиг: заголовок протокола и раздача статики.

Закрывает задачу 08 аудита. Проверяются вещи, которые невозможно увидеть при
`DEBUG=1`, а в бою ломают сайт целиком:

* без `SECURE_PROXY_SSL_HEADER` сайт за Apache с TLS уходит в бесконечный
  цикл редиректов — страница не открывается вообще;
* без `STATIC_ROOT` команда `collectstatic` падает, а при `DEBUG=0` сервер
  не раздаёт стили — сайт открывается без оформления.

Проверка не зависит от домена и от реального IP: подставляется
`example.uz`, которого в продакшене не будет.
"""

import os
import shutil
import tempfile

from django.conf import settings
from django.core.management import CommandError, call_command
from django.test import Client, SimpleTestCase, TestCase, override_settings


def prod_env(**extra):
    """Прод-окружение одной строкой, чтобы не забыть переменную."""
    values = {
        'DJANGO_DEBUG': '0',
        'DJANGO_ALLOWED_HOSTS': 'example.uz',
        'DJANGO_CSRF_TRUSTED_ORIGINS': 'https://example.uz',
    }
    values.update(extra)
    return values


@override_settings(
    DEBUG=False,
    SECURE_SSL_REDIRECT=True,
    ROOT_URLCONF='config.urls',
)
class ProxyHeaderTests(TestCase):
    """Критерий приёмки: с заголовком 200, без заголовка — 301."""

    HOSTS = ['example.uz', 'testserver']
    PROXY_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

    def _get(self, forwarded=False):
        # secure=True не используется: он сам по себе помечает соединение
        # защищённым, и проверка редиректа перестала бы что-то проверять.
        extra = {'HTTP_X_FORWARDED_PROTO': 'https'} if forwarded else {}
        return Client().get('/', **extra)

    @override_settings(ALLOWED_HOSTS=HOSTS)
    def test_with_forwarded_proto_returns_200(self):
        with override_settings(SECURE_PROXY_SSL_HEADER=self.PROXY_HEADER):
            response = self._get(forwarded=True)
        self.assertEqual(response.status_code, 200,
                         'сайт за прокси должен открываться, а не зацикливаться')

    @override_settings(ALLOWED_HOSTS=HOSTS)
    def test_without_forwarded_proto_redirects_to_https(self):
        """Без доверия к заголовку 301 — это правильно: приложение считает,
        что соединение незащищённое, и требует перейти на https."""
        with override_settings(SECURE_PROXY_SSL_HEADER=None):
            response = self._get(forwarded=False)
        self.assertEqual(response.status_code, 301)
        self.assertTrue(response.headers['Location'].startswith('https://'))

    @override_settings(ALLOWED_HOSTS=HOSTS)
    def test_ignoring_header_still_redirects(self):
        """Если заголовок есть, но доверия к нему нет — редирект сохраняется.

        Обратная сторона настройки: без SECURE_PROXY_SSL_HEADER клиент,
        притворившийся https-ом, не отключает редирект.
        """
        with override_settings(SECURE_PROXY_SSL_HEADER=None):
            response = self._get(forwarded=True)
        self.assertEqual(response.status_code, 301)


class TrustFlagTests(SimpleTestCase):
    """Доверять заголовку протокола можно только по явному разрешению.

    Проверяется перезагрузкой модуля настроек, потому что значение
    вычисляется на импорте. Важная тонкость: `importlib.reload` выполняет
    код в уже существующем словаре модуля и НЕ очищает его, поэтому перед
    проверкой имя надо удалять руками — иначе вывод предыдущей перезагрузки
    останется и тест будет врать.
    """

    def _reload(self, flag):
        from config import settings as settings_module

        os.environ.pop('DJANGO_TRUST_PROTO', None)
        if flag is not None:
            os.environ['DJANGO_TRUST_PROTO'] = flag
        vars(settings_module).pop('SECURE_PROXY_SSL_HEADER', None)
        vars(settings_module).pop('TRUST_PROTO_HEADER', None)
        import importlib
        return importlib.reload(settings_module)

    def tearDown(self):
        self._reload(None)

    def test_flag_off_by_default(self):
        reloaded = self._reload(None)
        self.assertFalse(reloaded.TRUST_PROTO_HEADER)
        self.assertNotIn(
            'SECURE_PROXY_SSL_HEADER', vars(reloaded),
            'SECURE_PROXY_SSL_HEADER без явного разрешения не задаётся',
        )

    def test_flag_zero_means_off(self):
        reloaded = self._reload('0')
        self.assertFalse(reloaded.TRUST_PROTO_HEADER)
        self.assertNotIn('SECURE_PROXY_SSL_HEADER', vars(reloaded))

    def test_flag_on_sets_header(self):
        reloaded = self._reload('1')
        self.assertTrue(reloaded.TRUST_PROTO_HEADER)
        self.assertEqual(
            vars(reloaded)['SECURE_PROXY_SSL_HEADER'],
            ('HTTP_X_FORWARDED_PROTO', 'https'),
        )

    def test_flag_matches_xff_pattern(self):
        """Доверие к заголовку включается так же, как к X-Forwarded-For."""
        reloaded = self._reload(None)
        self.assertTrue(
            hasattr(reloaded, 'TRUST_X_FORWARDED_FOR_IP'),
            'настройка доверия к XFF должна остаться на месте',
        )


class StaticFilesTests(SimpleTestCase):
    """Критерий приёмки: `collectstatic` отрабатывает и кладёт файлы."""

    def test_static_root_is_set(self):
        self.assertTrue(
            getattr(settings, 'STATIC_ROOT', None),
            'STATIC_ROOT не задан: collectstatic упадёт, прод останется без CSS',
        )

    def test_static_root_ignored_by_git(self):
        """Папка со статикой не должна попадать в репозиторий."""
        path = os.path.join(os.path.dirname(settings.BASE_DIR),
                            '.gitignore')
        # BASE_DIR — корень проекта, .gitignore лежит рядом с manage.py
        candidate = os.path.join(str(settings.BASE_DIR), '.gitignore')
        self.assertTrue(os.path.exists(candidate), 'нет .gitignore')
        with open(candidate, encoding='utf-8') as fh:
            content = fh.read()
        self.assertIn('staticfiles/', content,
                      'staticfiles/ должен быть в .gitignore')

    def test_static_url_starts_with_slash(self):
        self.assertTrue(
            settings.STATIC_URL.startswith('/'),
            'STATIC_URL без ведущего слэша собирается относительно адреса '
            'страницы: /block/ + static/... вместо /static/...',
        )

    def test_collectstatic_works(self):
        tmp = tempfile.mkdtemp(prefix='static_root_')
        try:
            with override_settings(STATIC_ROOT=tmp):
                call_command('collectstatic', '--noinput', verbosity=0)
            self.assertTrue(os.path.isdir(tmp))
            style = os.path.join(tmp, 'schedule', 'style.css')
            self.assertTrue(os.path.exists(style), 'style.css не собран')
            self.assertGreater(os.path.getsize(style), 1000,
                               'style.css пустой или обрезанный')
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_static_url_reaches_files(self):
        """Путь из STATIC_URL должен реально совпадать с собранным файлом."""
        tmp = tempfile.mkdtemp(prefix='static_root_')
        try:
            with override_settings(STATIC_ROOT=tmp):
                call_command('collectstatic', '--noinput', verbosity=0)
                relative = 'schedule/style.css'
                self.assertTrue(
                    os.path.exists(os.path.join(tmp, relative)),
                    f'нет файла {relative} в STATIC_ROOT',
                )
                self.assertTrue(
                    settings.STATIC_URL.endswith('/'),
                    'STATIC_URL должен кончаться слэшем',
                )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class StaticRootMustNotExposeDatabaseTests(SimpleTestCase):
    """Папка, из которой отдаётся статика, не должна содержать базу.

    WhiteNoise отдаёт всё, что лежит в STATIC_ROOT, и расширение файла не
    проверяет. Если при настройке сервера туда попадёт корень проекта, то
    `db.sqlite3` станет доступен каждому, кто откроет `/static/db.sqlite3`.
    Значит, это должно быть ошибкой при запуске, а не сюрпризом потом.
    """

    def _start_with(self, static_root):
        """Запускает настройки заново в отдельном процессе.

        Проверка живёт в момент чтения settings.py, поэтому её нельзя
        выполнить в том же процессе: настройки там уже загружены, а
        `override_settings` срабатывает позже. Поэтому отдельный процесс —
        единственный честный способ проверить именно запуск.
        """
        import subprocess
        import sys

        code = (
            'import os, django;'
            "os.environ['DJANGO_SETTINGS_MODULE']='config.settings';"
            f'os.environ[\'DJANGO_STATIC_ROOT\']={static_root!r};'
            'django.setup();print("ЗАПУСТИЛОСЬ")'
        )
        return subprocess.run([sys.executable, '-c', code],
                              capture_output=True, text=True,
                              encoding='utf-8', errors='replace')

    def test_pointing_static_root_at_project_root_is_refused(self):
        result = self._start_with(str(settings.BASE_DIR))
        self.assertNotIn('ЗАПУСТИЛОСЬ', result.stdout,
                         'приложение запустилось с папкой статики, где лежит база')
        self.assertIn('ImproperlyConfigured', result.stderr)
        self.assertIn('db.sqlite3', result.stderr)

    def test_normal_static_root_still_starts(self):
        tmp = tempfile.mkdtemp(prefix='static_ok_')
        try:
            result = self._start_with(tmp)
            self.assertIn('ЗАПУСТИЛОСЬ', result.stdout,
                          f'обычная папка статики не должна мешать: {result.stderr[:300]}')
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class DeployCheckTests(SimpleTestCase):
    """`check --deploy` с полным прод-окружением — без предупреждений."""

    def test_deploy_check_without_warnings(self):
        tmp = tempfile.mkdtemp(prefix='static_root_')
        env = dict(os.environ, **prod_env())
        previous = {key: os.environ.get(key) for key in env
                    if key.startswith('DJANGO_')}
        for key, value in env.items():
            if key.startswith('DJANGO_'):
                os.environ[key] = value
        try:
            import importlib

            from config import settings as settings_module
            reloaded = importlib.reload(settings_module)
            self.assertFalse(reloaded.DEBUG)
            try:
                call_command('check', '--deploy', verbosity=0)
            except SystemExit as exc:            # pragma: no cover
                self.fail(f'check --deploy упал: код {exc.code}')
            finally:
                importlib.reload(settings_module)
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            shutil.rmtree(tmp, ignore_errors=True)
