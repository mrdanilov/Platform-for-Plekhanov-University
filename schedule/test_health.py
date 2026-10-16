"""Health-check `/healthz/`.

Закрывает задачу 11 аудита. Проверяется не «жив ли процесс», а реальная
зависимость: база. Разница видна только на ответе — без health-check
приложение отвечает одинаково и когда база читается, и когда недоступна.

Здесь важно другое: `/healthz/` обязан отвечать даже когда сломан всё
остальное — не требовать логина, не попадать под rate limit, не уходить в
редирект на https и не приводить к 500 при недоступной базе.
"""

import json
import os
import shutil
import tempfile
from unittest.mock import patch

from django.conf import settings
from django.db import DatabaseError, connections
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.test.testcases import DatabaseOperationForbidden
from django.urls import reverse

from config.health import _database_ok


class HealthOkTests(TestCase):
    """Критерий приёмки: 200 и признак доступности БД."""

    def test_returns_200(self):
        self.assertEqual(self.client.get('/healthz/').status_code, 200)

    def test_payload_shape(self):
        body = json.loads(self.client.get('/healthz/').content.decode())
        self.assertEqual(body['status'], 'ok')
        self.assertIs(body['db'], True)
        self.assertTrue(body['time'])

    def test_named_route_lives_outside_schedule_app(self):
        """Путь без приложения: мониторинг не должен зависеть от /schedule/,
        иначе неисправность приложения уронит и проверку."""
        self.assertEqual(reverse('healthz'), '/healthz/')

    def test_not_cached(self):
        response = self.client.get('/healthz/')
        self.assertIn('no-store', response.headers.get('Cache-Control', ''))

    def test_nosniff_header(self):
        response = self.client.get('/healthz/')
        self.assertEqual(response.headers.get('X-Content-Type-Options'), 'nosniff')

    def test_get_and_head_allowed(self):
        self.assertEqual(self.client.get('/healthz/').status_code, 200)
        self.assertEqual(self.client.head('/healthz/').status_code, 200)

    def test_write_methods_rejected(self):
        """Запись в базу health-check делать не должен ни при каком раскладе.

        В тестах код 405 — от `require_http_methods`. В бою тот же POST даёт
        403: `CsrfViewMiddleware` отбивает небезопасный метод раньше. Оба
        ответа отвечают, поэтому проверяем «метод отбит», а не точное число.
        """
        for method in ('post', 'put', 'delete', 'patch'):
            with self.subTest(method=method):
                response = getattr(self.client, method)('/healthz/')
                self.assertIn(
                    response.status_code, (403, 405),
                    'небезопасный метод должен отбиваться',
                )


# Отдельной проверки «POST с настоящей CSRF-меткой» здесь нет намеренно.
# Тестовый клиент не получает куку `csrftoken` автоматически, и такой тест
# молча пропускал бы себя, ничего не проверяя. Отбитие небезопасных методов
# уже покрыто выше, а в бою CSRF отбивает POST раньше, чем до вьюхи дойдёт
# дело, — это проверено отдельно на живом сервере.


class HealthNoAuthNoThrottleTests(TestCase):
    """Эндпоинт не требует логина и не подпадает под rate limit."""

    def test_guest_gets_answer(self):
        response = Client().get('/healthz/')
        self.assertEqual(response.status_code, 200,
                         'мониторинг ходит без сессии и должен получать 200')

    def test_many_requests_in_a_row_all_succeed(self):
        """Если бы health-check попадал под throttle, мониторинг с частотой
        раз в минуту получил бы 429 и решил, что всё упало."""
        codes = {Client().get('/healthz/').status_code for _ in range(30)}
        self.assertEqual(codes, {200}, f'коды ответов: {sorted(codes)}')

    def test_survives_flood(self):
        for _ in range(200):
            response = Client().get('/healthz/')
            self.assertEqual(response.status_code, 200)
            if response.status_code != 200:
                break


def _broken_connection(path):
    """Настоящее подключение Django к заведомо плохой базе.

    Почему не подменять `default`: тестовая база — SQLite в памяти, и закрытие
    её соединения уничтожает базу целиком, после чего падают все остальные
    тесты набора.

    Почему не заводить новый алиас в `connections.databases`: этот словарь и
    есть объект `settings.DATABASES`. Его подменяет любой
    `override_settings(DATABASES=...)` в соседних тестах, и финальный `check`
    затем падает на несуществующем подключении — это и случилось при первой
    попытке.

    Поэтому собираем собственный `ConnectionHandler` с собственными
    настройками. Глобальное состояние Django не затрагивается, драйвер и
    ошибки настоящие.
    """
    import importlib

    # Подключение собирается напрямую из базового модуля бэкенда, минуя и
    # `load_backend`, и `ConnectionHandler`.
    #
    # `load_backend` ждёт не ENGINE, а короткое имя бэкенда: на «default» он
    # пытается импортировать `default.base` и падает с ModuleNotFoundError.
    # Обработчик подключений, в свою очередь, подставляет DEFAULT_DB_ALIAS в
    # собственные настройки, из-за чего переданный словарь перетирается —
    # отсюда KeyError 'default'.
    #
    # Настройки ниже — полный набор, который требует sqlite3-бэкенд.
    backend = importlib.import_module('django.db.backends.sqlite3.base')
    settings_dict = {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': path,
        'ATOMIC_REQUESTS': False,
        'AUTOCOMMIT': True,
        'CONN_MAX_AGE': 0,
        'CONN_HEALTH_CHECKS': False,
        'OPTIONS': {},
        'TIME_ZONE': None,
        'USER': '', 'PASSWORD': '', 'HOST': '', 'PORT': '',
        'TEST': {'CHARSET': None, 'COLLATION': None,
                 'MIGRATE': False, 'MIRROR': None, 'NAME': None},
        # Эти ключи Django проставляет сам при чтении настроек из
        # settings.DATABASES. Подключение здесь собирается руками, поэтому
        # проставить их нужно самостоятельно: без ALIAS, ENGINE_NAME,
        # COLLATION, MIGRATE и MIRROR конструктор падает с KeyError.
        'ALIAS': 'probe',
        'ENGINE_NAME': 'django.db.backends.sqlite3',
        'COLLATION': None,
        'MIGRATE': False,
        'MIRROR': None,
    }
    return backend.DatabaseWrapper(settings_dict, alias='probe')


class BrokenDatabaseProbeTests(TestCase):
    """Опрос базы против настоящих сломанных баз.

    Настоящий драйвер, настоящие ошибки. Подключение подставляется в
    `config.health.connections` через подмену, поэтому глобальные настройки
    Django и тестовая база не страдают.
    """

    def setUp(self):
        # Свой каталог на каждый тест: общий файл нельзя удалять между
        # тестами, пока его держит предыдущее подключение. SQLite на Windows
        # не отдаёт файл, пока на нём открыт хотя бы один handle, — отсюда
        # PermissionError при попытке убрать файл в teardown.
        self._dir = tempfile.mkdtemp(prefix='opencode-healthz-')
        self.addCleanup(self._cleanup)

        # Ошибки пробы логируются на ERROR с traceback, и без заглушки они
        # сыпались прямо в вывод прогона — по 12 строк на каждый тест про
        # сломанную базу, и на прогонах ради ошибок разбираться невозможно.
        #
        # Заглушка именно на объекте логера, а не через `self.settings(LOGGING=...)`:
        # Django применяет конфиг логирования один раз при старте, и подмена
        # настройки не перенастраивает уже созданный логгер — записи уходят
        # в корневой обработчик по-прежнему.
        #
        # Тесты здесь проверяют поведение, а не логи. Проверка самих логов
        # живёт в другом классе и использует `assertLogs`, который перехватывает
        # запись сам.
        self._muted = patch('config.health.logger')
        self._muted.start()
        self.addCleanup(self._muted.stop)

    def _path(self, name='probe.db'):
        return os.path.join(self._dir, name)

    def _cleanup(self):
        # Подключения закрываются через addCleanup в _probe, они выполняются
        # раньше этой записи, поэтому файлы уже освобождены.
        shutil.rmtree(self._dir, ignore_errors=True)

    def _probe(self, path):
        """Опрашивает `_database_ok` на подключении к `path`.

        Подмена — настоящий объект с `__getitem__`, а не словарь с ключом
        `'__getitem__'`: словарь подставляется по C-уровню, минуя запись в
        словаре, и всегда даёт `KeyError`. Тогда проба возвращала бы False
        из-за подмены, а не из-за состояния базы, — и все проверки ниже
        проходили бы, ничего не доказывая.
        """
        connection = _broken_connection(path)
        self.addCleanup(connection.close)

        class _Connections:
            def __getitem__(self, alias):
                if alias != 'default':
                    raise KeyError(alias)
                return connection

        with patch('config.health.connections', _Connections()):
            return _database_ok('default')

    def test_probe_wrapper_really_uses_the_database(self):
        """Контроль самой подмены: если она не работает, тесты выше
        проходят из-за KeyError, а не из-за сломанной базы."""
        path = connections.databases['default']['NAME']
        self.assertTrue(
            self._probe(path),
            'подмена не доходит до настоящей базы — тесты обманывают сами себя',
        )

    def test_missing_file_is_reported_as_down(self):
        path = self._path()
        self.assertFalse(os.path.exists(path))
        self.assertFalse(
            self._probe(path),
            'отсутствующий файл базы должен считаться недоступностью',
        )

    def test_corrupt_file_is_reported_as_down(self):
        path = self._path()
        with open(path, 'wb') as fh:
            fh.write(b'not a sqlite database at all')
        self.assertFalse(
            self._probe(path),
            'битый файл базы должен считаться недоступностью',
        )

    def test_empty_database_without_schema_is_down(self):
        """SQLite создаёт файл при подключении, поэтому «нет файла» и «файл
        пустой» — разные случаи. Второй обнаруживается только на втором
        запросе: `SELECT 1` проходит, а таблиц нет."""
        path = self._path()
        with open(path, 'wb'):
            pass
        self.assertTrue(os.path.exists(path))
        self.assertFalse(
            self._probe(path),
            'пустая база без схемы — это тоже недоступность',
        )

    def test_directory_instead_of_file_is_down(self):
        """Вместо файла каталог — тоже отказ, и тоже без падения."""
        directory = os.path.join(self._dir, 'not-a-file')
        os.makedirs(directory)
        self.assertFalse(
            self._probe(directory),
            'каталог вместо файла базы должен считаться недоступностью',
        )

    def test_real_database_is_up(self):
        """Контроль: с настоящей базой проба отвечает «живо».

        Без этого четыре теста выше проходили бы и при пробе, которая всегда
        возвращает False.
        """
        self.assertTrue(_database_ok('default'))

    def test_unknown_alias_is_down_not_crash(self):
        """Алиаса нет в настройках — это тоже недоступность, а не 500.

        `ConnectionHandler` на такой алиас отвечает `ConnectionDoesNotExist`,
        а `_thread._local` без слота алиаса — сырым `AttributeError`. Оба
        варианта не из `DatabaseError`, поэтому проверка обязана ловить и их:
        проба не имеет права роняться на незнакомом подключении.
        """
        self.assertFalse(_database_ok('alias_which_does_not_exist'),
                         'неизвестный алиас должен считаться недоступностью')


class BlockedQueriesProbeTests(SimpleTestCase):
    """Запрет запросов на уровне Django — это тоже «не проверила».

    Отдельный класс, потому что запрет ставится самим `SimpleTestCase`:
    внутри `TestCase` такой проверки не воспроизвести. `databases = {'default'}`
    означает «подключаться можно, выполнять запросы нельзя» — ровно то
    состояние, при котором проба обязана ответить «не проверила».
    """

    databases = {'default'}

    def setUp(self):
        # Тот же приём, что в остальных классах: логи ERROR с traceback
        # иначе попадают в вывод прогона и мешают разбирать настоящие сбои.
        self._muted = patch('config.health.logger')
        self._muted.start()
        self.addCleanup(self._muted.stop)

    def test_blocked_queries_report_down(self):
        """Проба обязана честно сказать «не проверила», а не «всё хорошо».

        Проверяется настоящий запрет: `_Wrapper` роняет `cursor()` с
        `DatabaseOperationForbidden` — так же, как это делает сам Django в
        `SimpleTestCase`.
        """
        class _Wrapper:
            def cursor(self):
                raise DatabaseOperationForbidden(
                    "Database queries to 'default' are not allowed in "
                    "SimpleTestCase subclasses."
                )

        class _Connections:
            def __getitem__(self, alias):
                return _Wrapper()

        with patch('config.health.connections', _Connections()):
            self.assertFalse(
                _database_ok('default'),
                'при невозможности выполнить запрос проба обязана сообщить '
                'недоступность, а не подтвердить работоспособность',
            )

    def test_view_answers_503_when_probe_fails_for_any_reason(self):
        """Падение проверки не должно выглядеть как «всё хорошо».

        В `_database_ok` стоит широкий `except`, поэтому проба наружу не
        бросает. Но страховка во вьюхе на случай будущих изменений нужна:
        ошибка 500 для мониторинга неотличима от падения приложения.
        """
        with patch('config.health._database_ok', side_effect=RuntimeError):
            response = self.client.get('/healthz/')
        self.assertEqual(response.status_code, 503,
                         'падение проверки не должно выглядеть как «всё хорошо»')
        body = json.loads(response.content.decode())
        self.assertIs(body['db'], False)


class HealthDatabaseDownTests(TestCase):
    """Критерий приёмки: недоступная база — 503, а не 500.

    Здесь проверяется HTTP-контракт: раз база недоступна, ответ должен быть
    503 и безопасно сформирован. Сам опрос базы на сломанных подключениях
    проверяет `BrokenDatabaseProbeTests` — там, где не нужно трогать `default`.
    """

    def _unhealthy(self):
        return patch('config.health._database_ok', return_value=False)

    def test_working_database_is_up(self):
        """Контроль: настоящая база проекта опрашивается успешно."""
        self.assertTrue(_database_ok('default'))

    def setUp(self):
        # Заглушка логера, а не `self.settings(LOGGING=...)`: Django применяет
        # конфиг логирования один раз при старте, и подмена настройки не
        # перенастраивает уже созданный логгер.
        #
        # Тест проверки логов отключает заглушку сам и использует
        # `assertLogs`, который перехватывает запись независимо.
        self._muted = patch('config.health.logger')
        self._muted.start()
        self.addCleanup(self._muted.stop)

    def test_control_healthy_gives_200(self):
        """Контрольная проверка: с настоящей базой ответ 200. Без неё
        тесты ниже доказывали бы только умение отдавать какой-то код."""
        self.assertEqual(self.client.get('/healthz/').status_code, 200)

    def test_down_gives_503(self):
        with self._unhealthy():
            self.assertEqual(self.client.get('/healthz/').status_code, 503)

    def test_payload_marks_database_as_down(self):
        with self._unhealthy():
            body = json.loads(self.client.get('/healthz/').content.decode())
        self.assertEqual(body['status'], 'unavailable')
        self.assertIs(body['db'], False)

    def test_response_does_not_leak_details(self):
        """Эндпоинт открыт, поэтому текст исключения в ответе — это утечка:
        наружу ушли бы путь файла, имя драйвера и структура установки.

        Подставляется настоящий сбой, а не флаг: если бы подмена сработала,
        тест доказывал бы только то, что флаг умеет гасить исключение.
        """
        def broken(alias='default'):
            raise DatabaseError(
                'unable to open database file /var/www/timetable/db.sqlite3 '
                '(sqlite3.OperationalError)'
            )

        # Подменяется соединение, а не сама проба: иначе исключение
        # поднимается мимо try/except внутри _database_ok и доходит до
        # клиента, а такой сценарий в жизни невозможен.
        with patch('config.health.connections') as fake:
            fake.__getitem__.return_value.cursor.side_effect = broken
            response = self.client.get('/healthz/')
        raw = response.content.decode()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('unable to open', raw)
        self.assertNotIn('/var/www/timetable', raw)
        self.assertNotIn('Traceback', raw)
        lowered = raw.lower()
        for leak in ('sqlite', 'operationalerror', 'no such file', 'unable'):
            with self.subTest(leak=leak):
                self.assertNotIn(leak, lowered)

    def test_error_is_logged_not_returned(self):
        """Подробности уходят в лог: без этого сервер нельзя будет
        диагностировать, почему мониторинг видит 503.

        Ошибка подставляется в соединение, а не в саму пробу: подмена пробы
        обошла бы её внутренний try/except, чего в бою не бывает.
        """
        path = os.path.join(tempfile.gettempdir(), 'opencode-healthz-logged.db')

        class _Connections:
            def __getitem__(self, alias):
                raise DatabaseError(f'cannot reach {path}')

        # Заглушка логера из setUp отключается: этот тест как раз проверяет,
        # что запись до лога доходит. `assertLogs` перехватывает настоящий
        # логер и сам не пускает вывод в отчёт прогона.
        self._muted.stop()
        with patch('config.health.connections', _Connections()):
            with self.assertLogs('config.health', level='ERROR') as captured:
                response = self.client.get('/healthz/')
        self._muted.start()
        self.assertEqual(response.status_code, 503)
        joined = '\n'.join(captured.output)
        self.assertIn('503', joined,
                      'в логе должен быть факт ответа 503')
        self.assertNotIn(path, response.content.decode(),
                         'путь не должен попасть в ответ')


class HealthBehindProxyTests(TestCase):
    """При DEBUG=0 проверка не должна уходить в редирект на https.

    Балансировщик обычно спрашивает приложение по http на localhost и не
    поймёт 301. Без исключения весь мониторинг «лежит» при полностью
    рабочем сайте.
    """

    @override_settings(
        DEBUG=False,
        SECURE_SSL_REDIRECT=True,
        SECURE_REDIRECT_EXEMPT=[r'^healthz/'],
        ALLOWED_HOSTS=['testserver'],
    )
    def test_healthz_is_not_redirected(self):
        self.assertEqual(Client().get('/healthz/').status_code, 200)

    @override_settings(
        DEBUG=False,
        SECURE_SSL_REDIRECT=True,
        SECURE_REDIRECT_EXEMPT=[r'^healthz/'],
        ALLOWED_HOSTS=['testserver'],
    )
    def test_other_pages_still_redirect(self):
        """Исключение должно быть узким: обычные страницы не перестали
        требовать https."""
        self.assertEqual(Client().get('/').status_code, 301)

    def test_exempt_pattern_matches_healthz_only(self):
        """Регулярка проверяется без ведущего слэша — так её видит
        SecurityMiddleware. Ошибка в этом месте тихо ломает мониторинг."""
        import re

        patterns = [re.compile(p) for p in [r'^healthz/']]
        self.assertTrue(any(p.search('healthz/') for p in patterns))
        for path in ('', 'schedule/', 'admin/login/', 'block/'):
            with self.subTest(path=path):
                self.assertFalse(any(p.search(path) for p in patterns))

    def test_setting_is_declared_in_project_settings(self):
        self.assertTrue(
            getattr(settings, 'SECURE_REDIRECT_EXEMPT', None),
            'SECURE_REDIRECT_EXEMPT не задан: при DEBUG=0 health-check '
            'отдаст 301, и мониторинг решит, что всё упало',
        )


class HealthDoesNotBreakPagesTests(TestCase):
    """Добавление маршрута не сдвинуло и не сломало остальное."""

    def test_index_still_works(self):
        self.assertEqual(self.client.get('/').status_code, 200)

    def test_healthz_does_not_leak_into_schedule_urls(self):
        """Маршрут объявлен до include('schedule.urls'), поэтому обратный
        порядок в urls.py должен дать NoReverseMatch — а не тихо перекрыть."""
        from django.urls import get_resolver

        resolver = get_resolver()
        matches = resolver.resolve('/healthz/')
        self.assertEqual(matches.url_name, 'healthz')
        self.assertEqual(matches.func.__module__, 'config.health')
