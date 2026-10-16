"""Виджет выбора цвета для админки.

Сотрудницы не программисты, поэтому HEX вида ``#dcea9e`` в поле ввода для
них — просто шум. Виджет даёт три понятных способа:

* клик по готовому образцу из палитры — самый частый случай;
* стандартная пипетка браузера (``<input type="color">``) — любой свой цвет;
* текстовое поле остаётся, но уже с готовым значением и подсказкой.

Само поле ``Subject.color`` не меняется: это обычный ``CharField``, и
виджет лишь рисует его по-другому. Поэтому миграция не нужна, а старое
значение из базы открывается как есть.
"""

import re

from django import forms
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe

from .exports import SUBJECT_COLORS

# Проверка значения для виджета, а не проверка формы. Это сознательно
# re.compile, а не forms.RegexField: виджет должен лишь решить, показывать
# ли значение пипетке, и не имеет права отказывать в рендере.
HEX_RE = re.compile(r'^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$')


class ColorPickerWidget(forms.TextInput):
    """Образцы палитры + пипетка + текстовое поле.

    Значение поля остаётся обычной строкой ``#rrggbb``, поэтому виджет
    взаимозаменяем с ``forms.TextInput`` и не влияет на модель.
    """

    template_name = 'admin/widgets/color_picker.html'

    def __init__(self, attrs=None, palette=None):
        self.palette = list(palette if palette is not None else SUBJECT_COLORS)
        super().__init__(attrs)

    def get_context(self, name, value, attrs):
        ctx = super().get_context(name, value, attrs)
        value = ctx['widget']['value'] or ''
        # Значение из базы может быть в любом регистре или вовсе пустым.
        # Для пипетки нужен корректный #rrggbb, иначе браузер откажется
        # его показывать.
        ctx['widget']['picker_value'] = value if HEX_RE.match(value) else '#e3f2fd'
        ctx['widget']['palette'] = self.palette
        ctx['widget']['selected'] = value.lower()
        return ctx


def render_picker(name, value, palette=None):
    """Отдельная функция для рендера в списке и в подсказках."""
    return mark_safe(render_to_string('admin/widgets/color_picker.html', {
        'widget': {
            'name': name,
            'value': value or '',
            'picker_value': value if HEX_RE.match(value or '') else '#e3f2fd',
            'palette': list(palette or SUBJECT_COLORS),
            'selected': (value or '').lower(),
            'attrs': {'id': f'id_{name}'},
        },
    }))