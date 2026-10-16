"""Гарантии редактирования расписания проверяются кодом, а не документом.

Раньше здесь лежала проверка инструкции для сотрудницы: она требовала, чтобы
каждое утверждение из `OFFICER.md` дословно совпадало с поведением системы. Сам
документ уехал из публичного репозитория — он написан для сотрудниц вуза и
посторонним читателям ничего не объясняет, — но проверять поведение он помогал
прежде всего.

Поэтому файл переписан: остались проверки того, что система действительно
делает, а проверки текста документа удалены. Что было ценного и сохранено:

* какие справочники защищены от удаления, а какие удаляются свободно;
* какие колонки правятся прямо в списке, а какие заблокированы;
* какие массовые действия есть в админке;
* что сообщения об ошибках написаны по-человечески и объясняют, что делать.

Что исчезло и почему: сверка цитат из инструкции, её разделы и аккуратность
таблиц. Сверять больше не с чем — документ живёт рядом с рабочей базой.
"""

import re

from django.apps import apps
from django.contrib import admin as dj_admin
from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.db.models.deletion import CASCADE, PROTECT, SET_NULL
from django.test import RequestFactory, SimpleTestCase, TestCase


class DeletionRulesTests(SimpleTestCase):
    """Что удаляется, а что защищено.

    Это самая дорогая часть редактирования: если предмет можно удалить, пока
    на него ссылается занятие, расписание рассыпается без всякого предупреждения.
    """

    def test_subject_is_protected(self):
        from schedule.models import Lesson
        self.assertIs(
            Lesson._meta.get_field('subject').remote_field.on_delete, PROTECT,
            'предмет должен быть защищён: на него ссылаются занятия',
        )

    def test_lesson_time_is_protected(self):
        from schedule.models import Lesson
        self.assertIs(
            Lesson._meta.get_field('lesson_time').remote_field.on_delete, PROTECT,
            'пара защищена: удаление сломало бы сетку расписания',
        )

    def test_group_is_cascaded(self):
        from schedule.models import Lesson
        self.assertIs(
            Lesson._meta.get_field('group').remote_field.on_delete, CASCADE,
            'группа удаляется вместе со своими занятиями',
        )

    def test_teacher_type_room_can_be_deleted(self):
        """Эти справочники удаляются свободно, занятия остаются живыми."""
        from schedule.models import Lesson
        for name in ('teacher', 'lesson_type', 'room'):
            self.assertIs(
                Lesson._meta.get_field(name).remote_field.on_delete, SET_NULL,
                f'{name}: удаление должно оставлять занятие живым',
            )

    def test_every_protected_reference_is_known(self):
        """Новая защита не должна появиться незамеченной.

        Если завтра появится модель под PROTECT, её нельзя молча пропустить:
        перед этим стоит подумать, что скажет об этом пользователь.
        """
        expected = {'Subject', 'LessonTime', 'LessonDraft', 'DraftPublication',
                    'Lesson'}
        found = set()
        for model in apps.get_app_config('schedule').get_models():
            for field in model._meta.get_fields():
                if field.many_to_one or field.one_to_one:
                    if getattr(field.remote_field, 'on_delete', None) is PROTECT:
                        found.add(field.remote_field.model.__name__)
        self.assertTrue(found, 'защит не осталось — тест устарел')
        self.assertFalse(
            found - expected,
            f'новая защита без разбора: {sorted(found - expected)}',
        )


class AdminEditingTests(SimpleTestCase):
    """Что можно править прямо в списке, не открывая занятие."""

    def setUp(self):
        self.factory = RequestFactory()
        self.request = self.factory.get('/')
        self.request.user = User(is_superuser=True, is_staff=True, is_active=True)
        self.request.session = {}
        self.request._messages = FallbackStorage(self.request)

    def admin_for(self, model_name):
        return dj_admin.site._registry[apps.get_model('schedule', model_name)]

    def test_lessons_can_be_deleted_in_bulk(self):
        self.assertTrue(
            self.admin_for('Lesson').has_delete_permission(self.request),
            'без права на удаление в списке нельзя убрать лишнее',
        )

    def test_shift_day_actions_exist_with_readable_names(self):
        """Сдвиг занятия на день вперёд и назад — с подписями, понятными в списке."""
        model_admin = self.admin_for('Lesson')
        self.assertIn('shift_day_forward', model_admin.actions)
        self.assertIn('shift_day_back', model_admin.actions)
        self.assertIn('+1', model_admin.shift_day_forward.short_description)
        self.assertIn('-1', model_admin.shift_day_back.short_description)

    def test_lesson_list_editable_columns(self):
        """Эти три правятся в таблице, остальные — нет."""
        model_admin = self.admin_for('Lesson')
        for field in ('teacher', 'lesson_type', 'room'):
            self.assertIn(field, model_admin.list_editable,
                          f'{field} должен правиться прямо в списке')
        for locked in ('group', 'subject', 'day', 'lesson_time'):
            self.assertNotIn(
                locked, model_admin.list_editable,
                f'{locked} стал редактируемым в списке: правка на две вкладки',
            )

    def test_pair_time_is_editable_in_the_list(self):
        model_admin = self.admin_for('LessonTime')
        self.assertIn('start', model_admin.list_editable)
        self.assertIn('end', model_admin.list_editable)

    def test_teacher_active_toggle_is_editable_in_the_list(self):
        self.assertIn('active', self.admin_for('Teacher').list_editable)

    def test_substitution_can_be_deleted(self):
        self.assertTrue(
            self.admin_for('Substitution').has_delete_permission(self.request))


class MessagesExplainWhatToDoTests(TestCase):
    """Сообщение об ошибке должно объяснять, что произошло.

    Класс — TestCase, а не SimpleTestCase: проверка полного заполнения полей
    уходит в базу за проверкой уникальности, и простым тестам такой запрос
    запрещён.

    Пользователь видит текст в браузере и больше нигде его не ищет. Сообщение
    вида «Нарушение уникальности» не говорит ничего: нужно сказать, какое
    именно совпадение мешает — регистр, буква «ё» или та же пара в тот же день.
    """

    @staticmethod
    def violation_message(model_name, constraint_name):
        model = apps.get_model('schedule', model_name)
        constraint = next(c for c in model._meta.constraints
                          if c.name == constraint_name)
        return ' '.join(constraint.violation_error_message.split())

    def test_duplicate_messages_say_what_already_exists(self):
        cases = (
            ('Subject', 'uniq_subject_norm_name', 'предмет с таким названием'),
            ('Teacher', 'uniq_teacher_norm_name', 'преподаватель с таким фио'),
            ('Group', 'uniq_group_norm_name_course', 'группа с таким названием'),
            ('Lesson', 'uniq_lesson_slot', 'это занятие уже есть'),
        )
        for model_name, constraint, expected in cases:
            with self.subTest(model=model_name):
                message = self.violation_message(model_name, constraint).lower()
                self.assertIn(expected, message,
                              f'{model_name}: сообщение не называет, что '
                              'именно совпало')

    def test_lookup_keys_explain_the_difference_is_only_spelling(self):
        """Про дубли по ключу нужно сказать, что разница только в написании.

        Иначе сотрудница решит, что система ошиблась: для неё два одинаковых
        названия выглядят одинаково, а сообщение молчит про регистр и «ё».
        """
        for model_name, constraint in (
                ('Subject', 'uniq_subject_norm_name'),
                ('Teacher', 'uniq_teacher_norm_name'),
                ('Group', 'uniq_group_norm_name_course')):
            with self.subTest(model=model_name):
                message = self.violation_message(model_name, constraint).lower()
                self.assertIn('регистр', message)
                self.assertIn('ё', message)

    def test_busy_slot_message_names_the_day_and_the_pair(self):
        """Самое обидное сообщение — «занято» без объяснения.

        Здесь сказано, что у группы та же пара в этот день с тем же
        предметом: этого достаточно, чтобы понять, что именно ищешь.
        """
        message = self.violation_message('Lesson', 'uniq_lesson_slot').lower()
        for part in ('пара', 'день', 'предмет'):
            self.assertIn(part, message,
                          f'в сообщении о занятой паре нет слова «{part}»')

    def test_messages_are_written_for_a_person(self):
        """Тексты ошибок — из словаря Django, а не имена полей и моделей."""
        for model_name, constraint in (
                ('Subject', 'uniq_subject_norm_name'),
                ('Lesson', 'uniq_lesson_slot')):
            with self.subTest(model=model_name):
                message = self.violation_message(model_name, constraint)
                for technical in ('unique', 'violation', 'schedule_',
                                  'IntegrityError', 'UNIQUE constraint'):
                    self.assertNotIn(technical, message,
                                     f'{model_name}: в сообщении видны '
                                     f'внутренние слова: {technical!r}')

    def test_colour_format_message_shows_a_sample(self):
        """Подсказка про цвет должна содержать пример, иначе её не выполнить."""
        from schedule.models import Subject
        with self.assertRaises(Exception) as caught:
            Subject(name='Философия', color='синий').full_clean(
                exclude=['norm_name'])
        text = ' '.join(str(item)
                        for items in caught.exception.message_dict.values()
                        for item in items)
        self.assertIn('#dcea9e', text, 'в подсказке про цвет нет образца')
        self.assertIn('пипеткой', text.lower(),
                      'подсказка не говорит, как проще всего')

    def test_time_order_message_names_the_rule(self):
        """Про время: сообщение должно называть правило, а не код ошибки."""
        from schedule.models import LessonTime
        with self.assertRaises(Exception) as caught:
            LessonTime(number=1, start='18:00', end='09:00').full_clean()
        text = ' '.join(str(item)
                        for items in caught.exception.message_dict.values()
                        for item in items)
        self.assertEqual(
            text, 'Время окончания должно быть позже времени начала.',
            'сообщение о неверном времени не совпадает с ожидаемым')


class RemovedDocNotReadTests(SimpleTestCase):
    """Снятая инструкция не должна остаться в проверках.

    Иначе сборка на сервере падала бы с «файл не найден» ровно там, где его
    и не должно быть.
    """

    def test_no_test_reads_the_removed_instruction(self):
        """Ищется не слово «OFFICER», а обращение к файлу.

        В пояснениях имя может упоминаться как пример разбора схемы папок —
        это ничего не читает и ломать сборку не может.
        """
        import pathlib

        reading = re.compile(r"OFFICER_MD|['\"]OFFICER\.md['\"]")
        offenders = []
        for path in pathlib.Path(__file__).resolve().parent.glob('*.py'):
            if path.name == 'test_edit_safety.py':
                continue
            if reading.search(path.read_text(encoding='utf-8')):
                offenders.append(path.name)
        self.assertFalse(
            offenders,
            f'проверки всё ещё читают снятую инструкцию: {offenders}')

    def test_no_markdown_links_to_the_removed_instruction(self):
        """В документации репозитория на неё больше не ссылаются."""
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent
        docs = [root / 'README.md', root / 'docs' / 'HANDBOOK.md']
        stale = [doc.relative_to(root).as_posix() for doc in docs
                 if doc.exists() and '](OFFICER.md)' in doc.read_text(
                     encoding='utf-8')]
        self.assertFalse(stale, f'ссылки на снятую инструкцию: {stale}')

