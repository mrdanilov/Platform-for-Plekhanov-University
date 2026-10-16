"""Инструкция для сотрудницы должна совпадать с реальным поведением.

Документ для сотрудниц — это не пересказ: если в нём написано «не даст
удалить», а система молча удалит, сотрудница потеряет расписание. Если
написано «система не даст», а она не даст — сотрудница потратит время и
подумает, что что-то сломано. Поэтому каждое утверждение проверяется кодом.

Проверяются не тексты, а предпосылки: реальные on_delete в моделях,
наличие запретов в админке, списки действий. Если в коде появится новое
ограничение, тест упадёт и на него посмотрят, а не молча расходятся с
инструкцией.
"""

import pathlib
import re

from django.apps import apps
from django.contrib import admin as dj_admin
from django.contrib.auth.models import User
from django.db.models.deletion import CASCADE, PROTECT, SET_NULL
from django.test import RequestFactory, SimpleTestCase
from django.contrib.messages.storage.fallback import FallbackStorage

OFFICER_MD = pathlib.Path(__file__).resolve().parent.parent / 'OFFICER.md'


def officer_text():
    return OFFICER_MD.read_text(encoding='utf-8')


def squashed(text):
    """Текст с одной строкой вместо переносов.

    В инструкции длинные сообщения об ошибках разбиты на несколько строк —
    так их читать удобнее, но дословной подстроки в файле уже не остаётся.
    Проверка цитируемости работает со схлопнутым текстом: переносы не
    должны мешать сверке с кодом, а сами формулировки — обязаны.
    """
    return re.sub(r'\s+', ' ', text)


class ProtectionMatchesDocTests(SimpleTestCase):
    """Таблица ограничений в инструкции — это правда про модели."""

    def test_subject_is_protected(self):
        """Предмет удаляем только когда на него никто не ссылается."""
        from schedule.models import Lesson
        self.assertIs(
            Lesson._meta.get_field('subject').remote_field.on_delete, PROTECT,
            'Инструкция обещает защиту предмета, а её в модели нет',
        )

    def test_lesson_time_is_protected(self):
        """Пара защищена: удаление сломало бы расписание."""
        from schedule.models import Lesson
        self.assertIs(
            Lesson._meta.get_field('lesson_time').remote_field.on_delete, PROTECT,
        )

    def test_group_is_cascaded(self):
        """Группа удаляется вместе с её занятиями."""
        from schedule.models import Lesson
        self.assertIs(
            Lesson._meta.get_field('group').remote_field.on_delete, CASCADE,
            'инструкция говорит, что удаление группы уносит её занятия',
        )

    def test_teacher_type_room_can_be_deleted(self):
        """Справочники без защиты: удаляются, занятия остаются живыми."""
        from schedule.models import Lesson
        for name in ('teacher', 'lesson_type', 'room'):
            self.assertIs(
                Lesson._meta.get_field(name).remote_field.on_delete, SET_NULL,
                f'{name}: инструкция говорит, что удаляется свободно',
            )

    def test_every_protected_model_is_mentioned_in_the_table(self):
        """Каждая реально защищённая модель описана в инструкции.

        Регресс на будущее: если завтра появится новая PROTECT-модель и её
        не вписать в таблицу, сотрудница столкнётся с запретом, о котором
        инструкция молчит.
        """
        text = officer_text()
        protected = set()
        for model in apps.get_app_config('schedule').get_models():
            for field in model._meta.get_fields():
                if field.many_to_one or field.one_to_one:
                    on_delete = getattr(field.remote_field, 'on_delete', None)
                    if on_delete is PROTECT:
                        protected.add(field.remote_field.model.__name__)
        self.assertTrue(protected, 'защит не осталось — тест устарел')
        # Человеческие названия в инструкции, а не имена классов.
        human = {
            'Subject': 'Предмет',
            'LessonTime': 'Времена пар',
            'Group': 'Группы',
            'Lesson': 'Занятия',
            'Room': 'Аудитории',
            'Teacher': 'Преподаватели',
            'LessonType': 'Типы занятий',
            'Substitution': 'Замены',
        }
        for model_name in protected:
            label = human.get(model_name)
            self.assertIsNotNone(label, f'{model_name}: нет названия в тесте')
            self.assertIn(
                label, text,
                f'{label} защищено моделью, но в инструкции не упомянуто',
            )


class AdminCapabilitiesMatchDocTests(SimpleTestCase):
    """Возможности админки, описанные в инструкции, должны существовать."""

    def setUp(self):
        self.factory = RequestFactory()
        self.request = self.factory.get('/')
        self.request.user = User(is_superuser=True, is_staff=True,
                                 is_active=True)
        self.request.session = {}
        self.request._messages = FallbackStorage(self.request)

    def test_lesson_admin_has_bulk_delete(self):
        """Инструкция советует «выбрать все» и удалить разом."""
        model_admin = dj_admin.site._registry[apps.get_model('schedule', 'Lesson')]
        self.assertTrue(
            model_admin.has_delete_permission(self.request),
            'в инструкции описан массовый выбор и удаление, а права нет',
        )

    def test_shift_day_actions_exist_with_promised_names(self):
        """Обещанные в инструкции действия сдвига дня должны быть."""
        model_admin = dj_admin.site._registry[apps.get_model('schedule', 'Lesson')]
        self.assertIn('shift_day_forward', model_admin.actions)
        self.assertIn('shift_day_back', model_admin.actions)
        forward = getattr(model_admin, 'shift_day_forward').short_description
        self.assertIn('+1', forward)
        back = getattr(model_admin, 'shift_day_back').short_description
        self.assertIn('-1', back)

    def test_lesson_list_editable_columns_are_editable_in_place(self):
        """Инструкция говорит: тип, преподаватель, аудитория правятся в таблице."""
        model_admin = dj_admin.site._registry[apps.get_model('schedule', 'Lesson')]
        self.assertIn('teacher', model_admin.list_editable)
        self.assertIn('lesson_type', model_admin.list_editable)
        self.assertIn('room', model_admin.list_editable)
        # А вот эти в списке менять нельзя — инструкция так и пишет.
        for locked in ('group', 'subject', 'day', 'lesson_time'):
            self.assertNotIn(
                locked, model_admin.list_editable,
                f'{locked} стал редактируемым в списке — инструкцию надо поправить',
            )

    def test_lesson_time_time_is_editable_in_the_list(self):
        """Инструкция говорит: время пары правится прямо в строке."""
        model_admin = dj_admin.site._registry[apps.get_model('schedule', 'LessonTime')]
        self.assertIn('start', model_admin.list_editable)
        self.assertIn('end', model_admin.list_editable)

    def test_teacher_active_toggle_is_editable_in_the_list(self):
        """Инструкция говорит: галочку «Активен» можно снять в списке."""
        model_admin = dj_admin.site._registry[apps.get_model('schedule', 'Teacher')]
        self.assertIn('active', model_admin.list_editable)

    def test_substitution_can_be_deleted(self):
        """Инструкция говорит: замену можно отменить удалением."""
        model_admin = dj_admin.site._registry[apps.get_model('schedule', 'Substitution')]
        self.assertTrue(model_admin.has_delete_permission(self.request))


class DocMentionsRealMessagesTests(SimpleTestCase):
    """Тексты ошибок в инструкции должны совпадать с текстами в коде.

    Смысл проверки: сотрудница получит сообщение системы и пойдёт искать его
    в инструкции. Если инструкция его пересказывает своими словами, поиск
    ничего не найдёт, и человек решит, что правки в системе не документированы.
    Поэтому цитата должна быть дословной.
    """

    def test_duplicate_subject_message_quoted_verbatim(self):
        from schedule.models import Subject
        constraint = next(
            c for c in Subject._meta.constraints
            if c.name == 'uniq_subject_norm_name'
        )
        self.assertIn(
            squashed(constraint.violation_error_message), squashed(officer_text()),
        )

    def test_duplicate_teacher_message_quoted_verbatim(self):
        from schedule.models import Teacher
        constraint = next(
            c for c in Teacher._meta.constraints
            if c.name == 'uniq_teacher_norm_name'
        )
        self.assertIn(
            squashed(constraint.violation_error_message), squashed(officer_text()),
        )

    def test_duplicate_group_message_quoted_verbatim(self):
        from schedule.models import Group
        constraint = next(
            c for c in Group._meta.constraints
            if c.name == 'uniq_group_norm_name_course'
        )
        self.assertIn(
            squashed(constraint.violation_error_message), squashed(officer_text()),
        )

    def test_duplicate_lesson_message_quoted_verbatim(self):
        from schedule.models import Lesson
        constraint = next(
            c for c in Lesson._meta.constraints if c.name == 'uniq_lesson_slot'
        )
        self.assertIn(
            squashed(constraint.violation_error_message), squashed(officer_text()),
        )

    def test_color_format_message_quoted(self):
        """Инструкция обещает конкретную подсказку про цвет."""
        self.assertIn('#dcea9e', officer_text())

    def test_time_order_message_quoted_verbatim(self):
        msg = 'Время окончания должно быть позже времени начала.'
        self.assertIn(msg, squashed(officer_text()))

    def test_quoted_message_still_explains_what_to_do(self):
        """Цитата ошибки должна быть рядом с советом.

        Одной цитаты мало: сотрудница узнает, что не так, но не узнает, что
        делать. Проверяется, что в инструкции есть и то, и другое.
        """
        text = squashed(officer_text())
        self.assertIn(
            'уже есть', text,
            'нет цитаты сообщения о дубликате',
        )
        self.assertIn(
            'выберите существующий', text.lower(),
            'цитата есть, но нет совета, что делать',
        )


class DocStructureTests(SimpleTestCase):
    """Инструкция должна быть читаемой: есть разделы, нет воды."""

    def setUp(self):
        self.text = officer_text()

    def test_has_required_sections(self):
        for heading in (
            '# Инструкция для сотрудницы',
            '## Что где лежит',
            '## Порядок работы',
            '## Что нельзя удалить и почему',
            '## Правка уже внесённого расписания',
            '## Частые ошибки',
            '## Если что-то не получается',
        ):
            self.assertIn(heading, self.text, f'нет раздела {heading}')

    def test_unreadable_section_comes_before_faq(self):
        """Ответ на «почему не могу удалить» должен быть до списка ошибок."""
        self.assertLess(
            self.text.index('## Что нельзя удалить и почему'),
            self.text.index('## Частые ошибки'),
        )

    def test_has_step_order_for_blocked_deletion(self):
        """Главная просьба — порядок действий при запрете удаления."""
        start = self.text.index('### Порядок действий, когда система не даёт удалить')
        section = self.text[start:self.text.index('###', start + 10)]
        steps = re.findall(r'^\d+\.', section, re.M)
        self.assertGreaterEqual(
            len(steps), 4,
            'порядок действий должен быть пронумерованным шагами',
        )

    def test_explains_bulk_delete_not_one_by_one(self):
        """Самая частая ошибка — удалять по одному. Это надо предотвратить."""
        start = self.text.index('### Порядок действий, когда система не даёт удалить')
        section = self.text[start:self.text.index('###', start + 10)]
        self.assertIn('Выбрать все', section)

    def test_no_time_estimates(self):
        """Просьба заказчика: без оценок времени."""
        text = self.text
        for pattern in (
            r'\d+\s*(минут|минуты|минуту)',
            r'\d+\s*(часа|часов|час)',
            r'\d+\s*(дн[яей]|дней)',
            r'занимает\s+\d+',
        ):
            self.assertIsNone(
                re.search(pattern, text, re.I),
                f'в инструкции появилась оценка времени: {pattern}',
            )

    def test_markdown_tables_are_well_formed(self):
        """Таблицы в документе не должны разъезжаться.

        Проверяется число столбцов в строках таблиц: если в строке
        данных столько же вертикальных чёрточек, сколько в заголовке, — всё
        в порядке.
        """
        block = []
        for line in self.text.splitlines() + ['']:
            if line.startswith('|'):
                block.append(line)
                continue
            if len(block) >= 2:
                widths = {len(r.strip().strip('|').split('|')) for r in block}
                self.assertEqual(
                    len(widths), 1,
                    f'таблица с разным числом столбцов: {block[0][:60]}',
                )
            block = []

    def test_no_placeholders_left(self):
        for bad in ('TODO', 'FIXME', '...вставить', 'Lorem'):
            self.assertNotIn(bad, self.text, f'остался заглушечный текст {bad}')