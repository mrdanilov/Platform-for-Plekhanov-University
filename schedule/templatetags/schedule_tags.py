"""Шаблонные теги: перевод, тема и версия статических файлов.

Подключение в шаблонах:
    {% load static schedule_tags %}
"""

import os

from django import template
from django.templatetags.static import static
from django.utils.safestring import mark_safe

from schedule import i18n

register = template.Library()


@register.simple_tag
def asset(path):
    """{% asset 'schedule/style.css' %} -> /static/schedule/style.css?v=1759...

    Добавляет к адресу время изменения файла. Без этого браузер держит
    старый кэш и после правки стилей или картинок продолжает показывать
    прежнюю версию — а менять приходится вручную через «Жёсткую перезагрузку».

    Путь ищется через staticfiles finders, а не собирается вручную: иначе
    версия молча превращается в 0 и антикэш перестаёт работать.
    """
    from django.contrib.staticfiles import finders

    url = static(path)
    version = 0
    found = finders.find(path)
    if found:
        try:
            version = int(os.path.getmtime(found))
        except OSError:
            version = 0
    return f'{url}?v={version}'


@register.simple_tag(takes_context=True)
def t_(context, key, **kwargs):
    """{% t_ 'week' %} -> «Неделя» / «Hafta»."""
    return i18n.translate(context['request'], key, **kwargs)


@register.simple_tag(takes_context=True)
def day_name(context, day_number, short=False):
    """{% day_name 1 %} -> «Понедельник», {% day_name 1 True %} -> «Пн»."""
    return i18n.weekday_name(context['request'], int(day_number), short=short)


@register.simple_tag
def languages():
    """Список кодов языков: [('ru', 'RU'), ('uz', 'UZ')].

    В шаблоне нужен как {% languages as langs %} — simple_tag кладёт
    значение в контекст только с `as`.
    """
    return i18n.available_languages()


@register.simple_tag(takes_context=True)
def current_lang(context):
    return i18n.get_language(context['request'])


@register.simple_tag(takes_context=True)
def lang_url(context, code):
    """Адрес переключателя языка с возвратом на текущую страницу.

    {% lang_url 'uz' %} -> /lang/?lang=uz&next=%2Ftoday%2F
    """
    from django.utils.http import urlencode

    request = context['request']
    params = {'lang': code, 'next': request.get_full_path()}
    return f'/lang/?{urlencode(params)}'


@register.simple_tag
def theme_script():
    """Ставится в <head> до подключения стилей — тема применяется без мигания."""
    return mark_safe("""
<script>
(function () {
  try {
    var dark = localStorage.getItem('schedule_theme') === 'dark';
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  } catch (e) {}
})();
</script>
""")