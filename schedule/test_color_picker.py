"""Выбор цвета предмета в админке.

Сотрудницы не программисты, поэтому HEX в текстовом поле для них — шум.
Виджет даёт образцы палитры, пипетку браузера и живой пример. Поле в модели
осталось CharField, поэтому миграции нет и старые значения открываются как есть.
"""

import os
import datetime as dt

import datetime as dt

from django.contrib.admin.sites import AdminSite
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase
from django.urls import reverse

from schedule.exports import SUBJECT_COLORS
from schedule.models import (Group, Lesson, LessonTime, LessonType, Subject,
                             Teacher, lesson_type_color)
from schedule.widgets import HEX_RE, ColorPickerWidget


def split_blocks(body):
    """Разрезает HTML формы на куски по полям ввода.

    Нужно, чтобы проверить «пипетка есть только у поля цвета» — иначе
    придётся доверять глазу.
    """
    blocks = {}
    for field in ('id_name', 'id_short_name', 'id_color'):
        i = body.find(f'for="{field}"')
        if i < 0:
            blocks[field] = ''
            continue
        seg = body[i:i + 4000]
        j = seg.find('for="id_', 5)
        blocks[field] = seg[:j] if j > 0 else seg
    return blocks


class WidgetRenderingTests(TestCase):
    """Виджет рисуется и не ломает другие поля."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('cp_admin', 'a@b.c', 'pw')
        cls.subject = Subject.objects.create(name='Философия', color='#dcea9e')

    def setUp(self):
        self.client.force_login(self.admin)

    def test_picker_is_on_the_color_field(self):
        body = self.client.get(
            reverse('admin:schedule_subject_add')).content.decode()
        self.assertIn('data-color-picker', body)
        self.assertIn('data-color-input', body)

    def test_whole_palette_is_shown(self):
        """Каждый цвет из палитры должен быть кликабельным образцом."""
        body = self.client.get(
            reverse('admin:schedule_subject_add')).content.decode()
        for color in SUBJECT_COLORS:
            self.assertIn(f'data-color="{color}"', body,
                          f'образца {color} нет в палитре')

    def test_picker_does_not_leak_to_other_fields(self):
        """Ключевая проверка.

        ``formfield_overrides`` здесь не годится: ключом в ней стоит класс
        поля, а color — тот же CharField, что и name/short_name, и пипетка
        прилипла бы ко всем трём. Тест ловит именно эту ошибку.
        """
        body = self.client.get(
            reverse('admin:schedule_subject_add')).content.decode()
        blocks = split_blocks(body)
        for field in ('id_name', 'id_short_name'):
            self.assertNotIn(
                'data-color-input', blocks[field],
                f'пипетка попала в поле {field} — виджет фильтруется по имени')
        self.assertIn('data-color-input', blocks['id_color'])

    def test_existing_value_opens_as_is(self):
        """Старое значение из базы должно открыться и отметиться."""
        body = self.client.get(
            reverse('admin:schedule_subject_change', args=[self.subject.pk])
        ).content.decode()
        self.assertIn('value="#dcea9e"', body)
        self.assertIn('data-color="#dcea9e"', body)

    def test_stylesheet_is_loaded(self):
        body = self.client.get(
            reverse('admin:schedule_subject_add')).content.decode()
        self.assertIn('admin/color_picker.css', body)

    def test_warning_about_duplicate_subject(self):
        """Напоминание выбрать существующий предмет, а не вписать новый."""
        body = self.client.get(
            reverse('admin:schedule_subject_add')).content.decode()
        self.assertIn('Предмет должен существовать ровно один раз', body)


class ColorValidationTests(TestCase):
    """Мусор в поле цвета не должен попасть в инлайн-стиль."""

    def test_valid_hex_passes(self):
        Subject(name='Философия', color='#dcea9e').full_clean(exclude=['norm_name'])

    def test_words_are_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            Subject(name='Философия', color='синий').full_clean(exclude=['norm_name'])
        self.assertIn('color', ctx.exception.error_dict)

    def test_style_breaking_value_is_rejected(self):
        """Значение с «}» закрыло бы атрибут style в шаблоне."""
        with self.assertRaises(ValidationError):
            Subject(name='Ф', color='#fff;} body{display:none'
                    ).full_clean(exclude=['norm_name'])

    def test_short_hex_is_rejected(self):
        """Трёхзначный #abc пипетка не покажет, а подложка станет другой."""
        with self.assertRaises(ValidationError):
            Subject(name='Ф', color='#abc').full_clean(exclude=['norm_name'])

    def test_message_is_human(self):
        """Сообщение должно говорить, что делать, а не «invalid»."""
        with self.assertRaises(ValidationError) as ctx:
            Subject(name='Ф', color='синий').full_clean(exclude=['norm_name'])
        text = str(ctx.exception.error_dict['color'][0])
        self.assertIn('#dcea9e', text)
        self.assertIn('пипеткой', text)

    def test_empty_color_is_rejected_by_the_field_itself(self):
        """Пустой цвет недопустим, но виновато поле, а не проверка формата.

        У ``Subject.color`` стоит ``blank=False``, поэтому пустое значение
        отсекает сама модель с текстом «Это поле обязательно». Проверка
        формата (см. ``test_words_are_rejected``) тут ни при чём — важно не
        запутать, читая сообщение.
        """
        with self.assertRaises(ValidationError) as ctx:
            Subject(name='Философия', color='').full_clean(
                exclude=['norm_name'], validate_unique=False)
        self.assertIn('color', ctx.exception.error_dict)
        message = str(ctx.exception.error_dict['color'][0])
        self.assertNotIn('#dcea9e', message,
                         'сообщение про формат тут ни при чём — это текст поля')
        self.assertNotIn('пипеткой', message)


class WidgetUnitTests(TestCase):
    """Сам виджет: битые значения не должны ронять страницу."""

    def test_broken_value_falls_back_for_picker(self):
        """Пипетка требует настоящий #rrggbb.

        В базе цвета всегда валидные, но значение может прийти из формы
        или старой копии. Виджет не имеет права падать на этом — иначе
        страница редактирования предмета просто не откроется.
        """
        widget = ColorPickerWidget()
        for bad in ('', 'синий', '#abc', '#fff;}body{', None):
            ctx = widget.get_context('color', bad, {})
            self.assertTrue(HEX_RE.match(ctx['widget']['picker_value']),
                            f'пипетка получила негодный цвет для {bad!r}')

    def test_valid_value_goes_to_picker_as_is(self):
        ctx = ColorPickerWidget().get_context('color', '#dcea9e', {})
        self.assertEqual(ctx['widget']['picker_value'], '#dcea9e')

    def test_widget_is_reachable_through_admin(self):
        """Проверка на реальной форме: хук formfield_for_dbfield."""
        site = AdminSite()
        from schedule.admin import SubjectAdmin
        form = SubjectAdmin(Subject, site).get_form(
            RequestFactory().get('/'), None)
        self.assertIsInstance(form.base_fields['color'].widget, ColorPickerWidget)
        self.assertNotIsInstance(form.base_fields['name'].widget, ColorPickerWidget)


class ColorReachesTemplateTests(TestCase):
    """Выбранный цвет должен реально доезжать до расписания."""

    def test_color_is_used_as_inline_background(self):
        """Связь «цвет предмета -> style в таблице».

        Виджет меняет только форму, но если бы шаблон не подставлял цвет,
        сотрудница бы выбрала образец и ничего бы не заметила.
        """
        g = Group.objects.create(name='G1')
        t = Teacher.objects.create(full_name='Иванов И.И.')
        lt = LessonTime.objects.create(number=1, start=dt.time(8, 0),
                                       end=dt.time(9, 30))
        s = Subject.objects.create(name='Философия', color='#dcea9e')
        Lesson.objects.create(group=g, day=1, lesson_time=lt, subject=s,
                              teacher=t)
        # Главная без выбранной группы ничего не рисует — расписание
        # появляется только после выбора, поэтому и view, и id обязательны.
        resp = self.client.get(f'/?view=group&id={g.pk}')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('background: #dcea9e;', resp.content.decode())


class LessonTypeColorTests(TestCase):
    """Цвет типа занятия: подложка вместо серого текста.

    Метка «Практика» брала цвет фона таблицы, и в строке из двадцати занятий
    её можно было не заметить. Теперь цвет задаёт сам тип и едет в стиль.
    """

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('lt_admin', 'a@b.c', 'pw')

    def test_known_types_get_their_own_color(self):
        self.assertEqual(lesson_type_color('Лекция'), '#b8d4f2')
        self.assertEqual(lesson_type_color('Практика'), '#bfe3bc')
        self.assertEqual(lesson_type_color('Лабораторная работа'), '#d6c4ee')

    def test_unknown_type_still_gets_a_color(self):
        """Новый тип из файла не должен остаться серым на сергом."""
        color = lesson_type_color('Контрольная работа')
        self.assertRegex(color, r'^#[0-9a-f]{6}$')

    def test_color_survives_restart(self):
        """Цвет типа не должен меняться при перезапуске сервера.

        Встроенный ``hash()`` в Python случайный при каждом запуске, и новый
        тип занятия молча перекрашивался бы после перезапуска: карточка у
        преподавателя меняет оттенок без всякой видимой причины. Поэтому
        проверка считается в отдельном процессе, как это и происходит на
        сервере.
        """
        import subprocess
        import sys

        code = (
            'import sys; sys.path.insert(0, %r);'
            "import django; django.setup();"
            'from schedule.models import lesson_type_color as c;'
            "print(c('Контрольная работа'))" % str(settings.BASE_DIR)
        )
        seen = set()
        for _ in range(3):
            result = subprocess.run(
                [sys.executable, '-c', code], capture_output=True, text=True,
                cwd=str(settings.BASE_DIR), env={**os.environ,
                'DJANGO_SETTINGS_MODULE': 'config.settings'})
            self.assertTrue(result.stdout.strip(),
                            f'не удалось получить цвет: {result.stderr[:300]}')
            seen.add(result.stdout.strip())
        self.assertEqual(len(seen), 1,
                         f'цвет типа меняется между запусками: {seen}')

    def test_color_follows_the_name(self):
        self.assertEqual(LessonType(name='Лекция').color, '#b8d4f2')
        self.assertEqual(LessonType(name='ЛЕКЦИЯ').color, '#b8d4f2')

    def test_explicit_color_wins(self):
        self.assertEqual(LessonType(name='Лекция', color='#123456').color,
                         '#123456')

    def test_broken_color_is_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            LessonType(name='Лекция', color='синий').full_clean(
                exclude=['norm_name'])
        self.assertIn('color', ctx.exception.error_dict)

    def test_picker_is_on_the_type_color_field_only(self):
        """Пипетка должна попасть только на цвет, а не на название типа."""
        from schedule.admin import LessonTypeAdmin
        site = AdminSite()
        form = LessonTypeAdmin(LessonType, site).get_form(
            RequestFactory().get('/'), None)
        self.assertIsInstance(form.base_fields['color'].widget,
                              ColorPickerWidget)
        self.assertNotIsInstance(form.base_fields['name'].widget,
                                 ColorPickerWidget)

    def test_admin_page_opens_with_picker(self):
        self.client.force_login(self.admin)
        body = self.client.get(
            reverse('admin:schedule_lessontype_add')).content.decode()
        self.assertIn('data-color-picker', body)

    def test_type_color_reaches_the_day_table(self):
        """Связь «цвет типа -> стиль метки» в списке «Сегодня»."""
        g = Group.objects.create(name='G1')
        lt = LessonTime.objects.create(number=1, start=dt.time(8, 0),
                                       end=dt.time(9, 30))
        s = Subject.objects.create(name='Философия', color='#dcea9e')
        kind = LessonType.objects.create(name='Практика', color='#bfe3bc')
        Lesson.objects.create(group=g, day=1, lesson_time=lt, subject=s,
                              lesson_type=kind)
        resp = self.client.get(f'/today/?day={g.course or 1}')
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('tag tag-type', body)
        self.assertIn('background: #bfe3bc;', body)