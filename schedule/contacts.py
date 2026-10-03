"""Контакты университета для раздела «Информация о университете».

Данные статичные, поэтому вынесены в словарь, а не в модель: их не нужно
редактировать через админку, зато их видно в одном месте и в шаблоне, и в тестах.
"""

# Яндекс Карты: без ключа API, обычная встраиваемая карта по адресу.
# координаты центра — Шahriobod, 3 (Toshkent, Mirzo Ulug'bek tumani).
MAP_QUERY = 'Toshkent, Mirzo Ulugbek tumani, Shahriobod kochasi 3'
MAP_URL = (
    'https://yandex.ru/map-widget/v1/?ll=69.240000%2C41.335000'
    '&z=17&mode=search&text=' + MAP_QUERY.replace(' ', '%20')
)

UNIVERSITY = {
    # названия нет здесь намеренно: оно переводится через i18n (ключ 'university'),
    # чтобы «Университет» читался по-русски и по-узбекски
    'address': 'Toshkent, Mirzo Ulugʻbek tumani, Shahriobod koʻchasi, 3, Uzbekistan',
    'email': 'info@reu_plekhanov.uz',
    'webmaster_email': '12345@mail.ru',
    'phones': [
        {'number': '+998 71 262 43 50',
         'note': 'По вопросам расписания'},
        {'number': '+998 71 262 43 60',
         'note': 'По вопросам заочной учебы'},
    ],
    'map_url': MAP_URL,
}