"""Кому открыт вход в админку.

Публичный сайт смотрит расписание — это и есть его задача. А вот вход в
админку в интернете опасен: пароль можно подобрать, страницу можно
перебрать, а получивший доступ меняет расписание у всех сотрудниц сразу.

Поэтому по умолчанию админка открыта только из локальной сети, а из
интернета на неё просто не попасть: страница не открывается вовсе, а не
показывает форму входа. Так администратор не выдаёт себя, и подбор пароля
не начинается.

Перечень разрешённых адресов задаётся переменной окружения
``DJANGO_ADMIN_ALLOWED_IPS``. Пустая или не заданная — значит «только
локальная сеть».
"""

import ipaddress

from django.conf import settings
from django.http import Http404


def _client_ip(request):
    """Адрес, с которого пришёл запрос.

    По умолчанию берётся из ``REMOTE_ADDR`` — его задаёт сервер, клиент его
    подделать не может.

    За nginx на том же сервере ``REMOTE_ADDR`` — это всегда 127.0.0.1, и
    ограничение по нему бессмысленно: админка была бы открыта всему интернету.
    Тогда адрес берётся из ``X-Forwarded-For``, но только когда в настройках
    явно сказано ``DJANGO_TRUST_XFF=1``, а nginx при этом переписывает
    заголовок целиком (``proxy_set_header X-Forwarded-For $remote_addr``).
    Если nginx дописывает в конец, не переписывая, заголовком можно
    подделаться — поэтому в конфигурации nginx он именно переписывается.

    Брать самый первый адрес списка нельзя: это делает тот, кто список
    прислал. Поэтому при нескольких значениях берётся последнее — его
    дописал ближайший к серверу прокси.
    """
    if getattr(settings, 'TRUST_X_FORWARDED_FOR_IP', False):
        forwarded = (request.META.get('HTTP_X_FORWARDED_FOR') or '').strip()
        if forwarded:
            parts = [part.strip() for part in forwarded.split(',') if part.strip()]
            if parts:
                return parts[-1]
    return (request.META.get('REMOTE_ADDR') or '').strip()


# Сети, из которых вход в админку разрешён по умолчанию: компьютеры внутри
# вуза. 127.0.0.0/8 — сам компьютер, 10/8 и 172.16/12 и 192.168/16 — локальная
# сеть, ::1 — то же, что 127.0.0.1 для IPv6.
LOCAL_NETWORKS = (
    '127.0.0.0/8',
    '10.0.0.0/8',
    '172.16.0.0/12',
    '192.168.0.0/16',
    '::1/128',
)


def _allowed_networks():
    """Сети, из которых вход в админку разрешён.

    Пустая настройка — не «закрыть всех», а «только локальная сеть»: под
    отменой обычной работы внутри вуза администратор потерял бы вход в
    админку, и это заметят сразу. Обратная ошибка опаснее: пустая настройка,
    прочитанная как «разрешено всё», открыла бы вход наружу.
    """
    raw = getattr(settings, 'ADMIN_ALLOWED_IPS', '')
    if isinstance(raw, (list, tuple, set)):
        entries = [str(item) for item in raw]
    else:
        entries = [part for part in str(raw or '').replace(';', ',').split(',')]

    entries = [entry.strip() for entry in entries if entry and entry.strip()]
    if not entries:
        entries = list(LOCAL_NETWORKS)

    networks = []
    for value in entries:
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            # Опечатка в настройке не должна закрыть админку у того, кто её
            # настраивал: молчаливый 404 администратор ищет часами. Поэтому
            # неверный адрес пропускаем, но заметим его в логах.
            import logging

            logging.getLogger(__name__).warning(
                'DJANGO_ADMIN_ALLOWED_IPS: не похож на адрес или сеть: %r',
                value)
    return networks


def admin_allowed_for(request):
    """Можно ли с этого адреса открывать админку."""
    address = _client_ip(request)
    if not address:
        return False
    try:
        client = ipaddress.ip_address(address)
    except ValueError:
        # Адрес не разобрался (бывает за кривым прокси). Такой запрос
        # считаем внешним: молча открывать админку неизвестному адресу
        # нельзя, а закрывать постороннего — можно.
        return False

    for network in _allowed_networks():
        if client in network:
            return True
    return False


def admin_gate(request):
    """Точка входа в админку: снаружи её просто нет.

    Ставится в middleware, потому что админка — это не одна функция, а
    подключение с десятками адресов. Оборачивать каждую по очереди нельзя,
    а закрыть их все одной проверкой — можно.
    """
    raise Http404


class AdminGateMiddleware:
    """Не пускает к админке с адресов, которым вход не разрешён.

    Ответ — 404, а не 403: страница с кодом 403 сама сообщает постороннему,
    что вход существует и стоит подбирать пароль.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info or ''
        if (path == '/admin' or path.startswith('/admin/')) \
                and not admin_allowed_for(request):
            return admin_gate(request)
        return self.get_response(request)


def admin_link_context(request):
    """Кнопка «Войти» в шапке.

    Ссылки на несуществующую страницу быть не должно: иначе посетитель
    сайта из интернета видит «Войти», нажимает и получает «страница не
    найдена» — выглядит как поломка сайта.
    """
    return {'admin_link_visible': admin_allowed_for(request)}
