"""Health-check для балансировщика и для человека.

Зачем он. Приложение без health-check отвечает одинаково в двух разных
ситуациях: «процесс жив и база читается» и «процесс жив, но база
недоступна». Разница видна только внутри, а снаружи сайт отдаёт 500 и
одновременно числится исправным. Оркестратор, который не может отличить
одно от другого, не уберёт под из балансировки — и перезапускать будет
живой процесс.

Что проверяется и почему именно это. Проверяется не «жив ли процесс», а
реальная зависимость: соединение с базой. Порядок такой:

1. ``SELECT 1`` — самый дешёвый запрос, доказывает, что соединение
   установилось и драйвер отвечает;
2. чтение одной строки из служебной таблицы ``django_migrations`` — доказывает,
   что база не просто открылась, а отдаёт данные. Таблица маленькая и всегда
   есть, в отличие от таблиц расписания, которые могут быть пустыми или
   большими.

Чего проверка НЕ делает и не может. Она не пишет в базу, а значит не
замечает состояние «залочено на запись». Для SQLite это основной сбой при
импорте (см. задачи `05` и `10`). Обнаружить его можно только попыткой
записи, а health-check, который пишет, способен заблокировать сайт сам —
значит, ловить эту ситуацию надо в логах и в мониторинге ошибок, а не здесь.

Про безопасность. Эндпоинт открыт: никакой авторизации и никакого rate limit,
иначе мониторинг будет получать 429 и считать всё упавшим. Поэтому в ответе
нет ни текста исключения, ни путей файлов — только признак «база отвечает».
Подробности уходят в серверный лог через ``logger.exception``.
"""

import json
import logging

from django.db import DatabaseError, connections
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

logger = logging.getLogger(__name__)


def _database_ok(alias='default'):
    """Два дешёвых запроса. True — база отвечает, False — нет.

    Подробность сбоя логируется здесь же, внутри ``except``: к моменту, когда
    функция вернула ``False``, активного исключения уже нет, и ``exc_info``
    во вьюхе не дал бы ничего — в лог ушёл бы голый факт без причины.
    """
    try:
        connection = connections[alias]
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            if cursor.fetchone()[0] != 1:
                logger.error('healthz: SELECT 1 вернул неожиданный результат')
                return False
            # Служебная таблица всегда есть и почти всегда маленькая.
            cursor.execute('SELECT 1 FROM django_migrations LIMIT 1')
            cursor.fetchall()
    except DatabaseError:
        logger.error(
            'healthz: база недоступна (ошибка драйвера)', exc_info=True,
        )
        return False
    except Exception:                       # noqa: BLE001 - health-check не падает
        # Драйвер может поднять что-то не из DatabaseError: например,
        # отсутствующий файл БД даёт ошибку на этапе подключения, а
        # запрет запросов в тестах — вообще не из django.db.
        logger.error(
            'healthz: база недоступна (непредвиденная ошибка)', exc_info=True,
        )
        return False
    return True


@require_http_methods(['GET', 'HEAD'])
def healthz(request):
    """``GET /healthz/`` — 200, если приложение и база в порядке.

    Без авторизации, без rate limit, без кэширования.

    HEAD разрешён наравне с GET: он так же безопасен (тело не отдаётся), а
    часть систем мониторинга по умолчанию шлёт именно его. Ответ 405 на HEAD
    система мониторинга истолкует как «сервис упал».
    """
    # Страховка вокруг пробы. `_database_ok` глушит исключения сам, но если
    # он когда-нибудь изменится или появятся новые кодовые пути — health-check
    # обязан ответить 503, а не 500. Ошибка 500 для мониторинга неотличима от
    # падения приложения и приводит к решению «перезапустить процесс».
    try:
        db_ok = _database_ok()
    except Exception:                       # noqa: BLE001 - health-check не падает
        logger.error('healthz: проба базы упала', exc_info=True)
        db_ok = False

    payload = {
        'status': 'ok' if db_ok else 'unavailable',
        'db': db_ok,
        'time': timezone.now().isoformat(timespec='seconds'),
    }
    if not db_ok:
        # Причина уже залогирована в _database_ok — там, где исключение ещё
        # было активным. Здесь только факт для сводки.
        logger.error('healthz: отвечаю 503, база недоступна')
    response = JsonResponse(payload, status=200 if db_ok else 503)
    # Мониторинг может опрашивать часто, кэш здесь только мешает.
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
