"""Ограничение частоты запросов и получение адреса клиента.

Против простого перебора: без счётчика бот за пару минут выкачивает
перебором букв весь список групп, преподавателей и пар.

ВАЖНО про хранилище счётчиков. По умолчанию Django использует LocMemCache —
он живёт в памяти одного процесса. Пока воркер один, всё работает. Как только
появятся несколько (gunicorn/uwsgi, несколько контейнеров), каждый посчитает
запросы отдельно и лимит перестанет действовать: реальный трафик умножится
на число воркеров.

Что делать при нескольких воркерах:
  * Redis/Memcached в CACHES (Django умеет из коробки через django-redis);
  * django-ratelimit — та же идея, но готовая и с ключами по IP;
  * либо ограничение на уровне nginx: `limit_req` в секции `http`.
Любой из трёх надёжнее кэша Django, потому что живёт не в приложении.
"""

from django.conf import settings
from django.core.cache import cache


def client_ip(request):
    """Адрес клиента для ключа лимита.

    За прокси и nginx REMOTE_ADDR у всех один, поэтому берём
    X-Forwarded-For — но только если это явно разрешено настройкой
    TRUST_X_FORWARDED_FOR_IP. Слепо доверять заголовку нельзя: его
    присылает клиент, и без проверки он позволял бы обходить лимит,
    просто подставляя случайный адрес в каждый запрос.
    """
    if getattr(settings, 'TRUST_X_FORWARDED_FOR_IP', False):
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if forwarded:
            # первый адрес в цепочке — исходный клиент
            candidate = forwarded.split(',')[0].strip()
            if candidate:
                return candidate
    return request.META.get('REMOTE_ADDR', '') or 'unknown'


def throttle_ok(key, limit, window=60):
    """True, если запрос укладывается в лимит.

    Счётчик фиксированное окно: окно сдвигается вместе со счётчиком, поэтому
    на границе возможен небольшой «прыжок» — для защиты от перебора это
    несущественно, а точность sliding window тут стоила бы запроса в БД
    на каждый клик поиска.
    """
    if limit <= 0:
        return True
    if cache.add(key, 1, window):
        return True          # первый запрос в этом окне
    return cache.incr(key) <= limit