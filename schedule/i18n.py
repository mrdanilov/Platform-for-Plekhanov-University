"""Переводы интерфейса: RU и UZ.

Без Django i18n — нам нужен переключатель «RU / UZ» в шапке, который
переживает перезагрузку и запоминается в cookie. Словари лежат здесь.
"""

RU = {
    'brand': 'Расписание',
    'nav_home': 'Главная',
    'nav_today': 'На сегодня',
    'login': 'Войти',
    'logout': 'Выйти',
    'menu_groups': 'Группы',
    'menu_pairs': 'Пары',
    'menu_teachers': 'Преподаватели',
    'placeholder': 'Выберите группу или преподавателя, чтобы посмотреть расписание',
    'week': 'Неделя',
    'now': 'текущая',
    'prev_week': 'Предыдущая неделя',
    'next_week': 'Следующая неделя',
    'excel': 'Excel',
    'print': 'Печать',
    'choose': 'Выбор',
    'search': 'Поиск…',
    'nothing_found': 'Ничего не найдено',
    'no_lessons': 'Занятий пока нет',
    'pair': 'пара',
    'pair_of': '-я пара',
    'room': 'Ауд.',
    'substitution': 'замена',
    'subject': 'Предмет',
    'group': 'Группа',
    'teacher': 'Преподаватель',
    'type': 'Тип',
    'course': 'курс',
    'lesson': 'Пара',
    'time': 'Время',
    'day': 'День',
    'total': 'Всего занятий',
    'formed': 'Расписание сформировано',
    'import': 'Импорт',
    'export': 'Выгрузка',
    'admin': 'Админка',
    'no_lessons_today': 'На этот день занятий нет.',
    'schedule_for': 'Расписание на',
    'theme_light': 'Светлая',
    'theme_dark': 'Тёмная',
    'close': 'Закрыть',
    # раздел «Информация о университете»
    'nav_university': 'Университет',
    'university': 'Российский экономический университет имени Г. В. Плеханова',
    'university_contacts': 'Контакты',
    'university_address': 'Адрес',
    'university_phone': 'Телефоны',
    'university_email': 'Почта',
    'university_webmaster': 'Веб-мастер',
    'university_schedule_phone': 'По вопросам расписания',
    'university_extra_phone': 'По вопросам заочной учёбы',
    'university_form': 'Связаться с нами',
    'university_name': 'Имя Фамилия',
    'university_email_ph': 'Ваша почта',
    'university_message': 'Вопросы или пожелания',
    'university_captcha': 'Ответ на пример',
    'university_captcha_prompt': 'Проверка: решите пример на картинке и введите ответ',
    'university_captcha_refresh': 'Другой пример',
    'university_consent': 'Я согласен на обработку персональных данных',
    'university_send': 'Отправить',
    'university_sent': 'Спасибо! Сообщение отправлено, мы ответим на указанную почту.',
    'university_send_error': 'Проверьте заполнение полей и согласие на обработку данных.',
    'university_map': 'Карта проезда',
    # дни недели, коротко и полно
    'day_1': 'Понедельник', 'day_2': 'Вторник', 'day_3': 'Среда',
    'day_4': 'Четверг', 'day_5': 'Пятница', 'day_6': 'Суббота',
    'dow_1': 'Пн', 'dow_2': 'Вт', 'dow_3': 'Ср',
    'dow_4': 'Чт', 'dow_5': 'Пт', 'dow_6': 'Сб',
}

UZ = {
    'brand': 'Jadval',
    'nav_home': 'Bosh sahifa',
    'nav_today': 'Bugungi kun',
    'login': 'Kirish',
    'logout': 'Chiqish',
    'menu_groups': 'Guruhlar',
    'menu_pairs': 'Darslar',
    'menu_teachers': "O'qituvchilar",
    'placeholder': "Guruh yoki o'qituvchini tanlang va jadvalni ko'ring",
    'week': 'Hafta',
    'now': 'joriy',
    'prev_week': 'Oldingi hafta',
    'next_week': 'Keyingi hafta',
    'excel': 'Excel',
    'print': 'Chop etish',
    'choose': 'Tanlash',
    'search': 'Qidirish…',
    'nothing_found': 'Hech narsa topilmadi',
    'no_lessons': "Hali darslar yo'q",
    'pair': 'dars',
    'pair_of': '-dars',
    'room': 'Xona',
    'substitution': 'almashuv',
    'subject': 'Fan',
    'group': 'Guruh',
    'teacher': "O'qituvchi",
    'type': 'Turi',
    'course': 'kurs',
    'lesson': 'Dars',
    'time': 'Vaqt',
    'day': 'Kun',
    'total': 'Jami darslar',
    'formed': 'Jadval tuzilgan',
    'import': 'Import',
    'export': 'Eksport',
    'admin': 'Admin',
    'no_lessons_today': 'Bu kunga darslar yo\'q.',
    'schedule_for': 'Jadval',
    'theme_light': 'Yorug‘',
    'theme_dark': 'Qorong‘i',
    'close': 'Yopish',
    # bo‘lim «Universitet haqida ma’lumot»
    'nav_university': 'Universitet',
    'university': 'Rossiya iqtisodiy universiteti nomidan G. V. Plehanov',
    'university_contacts': 'Kontaktlar',
    'university_address': 'Manzil',
    'university_phone': 'Telefonlar',
    'university_email': 'Pochta',
    'university_webmaster': 'Veb-master',
    'university_schedule_phone': 'Jadval bo‘yicha savollar',
    'university_extra_phone': 'Kechqurun o‘qish bo‘yicha savollar',
    'university_form': 'Biz bilan bog‘laning',
    'university_name': 'Ism va familiya',
    'university_email_ph': 'Sizning pochtangiz',
    'university_message': 'Savollar yoki takliflar',
    'university_captcha': 'Javob',
    'university_captcha_prompt': 'Tekshiruv: rasmda ifodani yechib, javobni kiriting',
    'university_captcha_refresh': 'Boshqa ifoda',
    'university_consent': 'Shaxsiy ma’lumotlarni qayta ishlashga roziman',
    'university_send': 'Yuborish',
    'university_sent': 'Rahmat! Xabaringiz yuborildi, ko‘rsatilgan pochta orqali javob beramiz.',
    'university_send_error': 'Maydonlarni va ma’lumotlarni qayta ishlashga rozini tekshiring.',
    'university_map': 'Xarita',
    'day_1': 'Dushanba', 'day_2': 'Seshanba', 'day_3': 'Chorshanba',
    'day_4': 'Payshanba', 'day_5': 'Juma', 'day_6': 'Shanba',
    'dow_1': 'Du', 'dow_2': 'Se', 'dow_3': 'Ch',
    'dow_4': 'Pa', 'dow_5': 'Ju', 'dow_6': 'Sh',
}

LANGS = {'ru': RU, 'uz': UZ}
DEFAULT_LANG = 'ru'
COOKIE_NAME = 'schedule_lang'


def available_languages():
    return [('ru', 'RU'), ('uz', 'UZ')]


def get_language(request):
    lang = request.COOKIES.get(COOKIE_NAME, DEFAULT_LANG)
    return lang if lang in LANGS else DEFAULT_LANG


def set_language_cookie(response, lang):
    response.set_cookie(COOKIE_NAME, lang, max_age=365 * 24 * 3600, samesite='Lax')
    return response


def translate(request, key, **kwargs):
    """Перевод строки по языку из cookie. Неизвестный ключ — ключ как есть."""
    lang = get_language(request)
    text = LANGS[lang].get(key, RU.get(key, key))
    return text.format(**kwargs) if kwargs else text


def weekday_name(request, day_number, short=False):
    return translate(request, f'dow_{day_number}' if short else f'day_{day_number}')


class Trans:
    """Обёртка, чтобы в шаблоне писать t.week, t.day_1 и т.п."""

    def __init__(self, request):
        self.request = request
        self.lang = get_language(request)

    def __getattr__(self, key):
        if key.startswith('_'):
            raise AttributeError(key)
        text = LANGS[self.lang].get(key, RU.get(key, key))
        return lambda **kwargs: text.format(**kwargs) if kwargs else text

    def __getitem__(self, key):
        return getattr(self, key)

    def day(self, day_number, short=False):
        return weekday_name(self.request, day_number, short)