"""Разбор файла вуза для команды ``load_week_schedule``.

Сама загрузка проверяется на реальном файле вручную, а вот чистые
функции разбора — на строках из него: в файле полно опечаток, и именно
на них ломается тихая загрузка.
"""

from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from schedule.management.commands.load_week_schedule import (
    parity_of, parse_room, parse_time, read_file, read_sheet, split_cell,
    tidy_name,
)
from schedule.models import Group, Lesson, LessonTime, Subject, Teacher


class SplitCellTests(TestCase):
    """Ячейка -> предмет, тип, преподаватель, должность."""

    def test_lecture_with_doctor(self):
        self.assertEqual(
            split_cell('Микроэкономика (лекц.)  доц.Соколова С'),
            ('Микроэкономика', 'Лекция', 'Соколова С.', 'Доцент'),
        )

    def test_practical_without_position(self):
        subject, kind, teacher, position = split_cell('Право  (лекц.)')
        self.assertEqual((subject, kind, teacher, position),
                         ('Право', 'Лекция', None, None))

    def test_type_after_qualifier_in_parentheses(self):
        # Первая скобка — не тип, из-за неё тип не находился вообще.
        subject, kind, teacher, _ = split_cell(
            'История экономических учений (на английском языке) (лекц.) '
            'ст. преп. Гаврилова А.Н.')
        self.assertEqual(subject, 'История экономических учений (на английском языке)')
        self.assertEqual(kind, 'Лекция')
        self.assertEqual(teacher, 'Гаврилова А.Н.')

    def test_glued_type_without_opening_bracket(self):
        # В файле есть «бизнеселекц.)» — склейка без скобки.
        subject, kind, _, _ = split_cell('Концептуальные решения в ресторанном бизнеселекц.)')
        self.assertEqual((subject, kind), ('Концептуальные решения в ресторанном бизнесе', 'Лекция'))

    def test_position_typo_st_perp(self):
        # «ст перп.» — в файле потеряна буква, обычный шаблон не ловит.
        subject, kind, teacher, position = split_cell(
            'Математический анализ (лекц.) ст перп. Машарипов С.И.')
        self.assertEqual((subject, kind, teacher, position),
                         ('Математический анализ', 'Лекция', 'Машарипов С.И.',
                          'Старший преподаватель'))

    def test_name_without_position(self):
        subject, _, teacher, position = split_cell(
            'Инновационная стратегия и политика (лекц.) Саттарова Б.Ш.')
        self.assertEqual(subject, 'Инновационная стратегия и политика')
        self.assertEqual((teacher, position), ('Саттарова Б.Ш.', None))

    def test_single_word_subject_is_not_a_name(self):
        # «Право» — предмет, а не фамилия: одно слово именем не считаем.
        subject, _, teacher, _ = split_cell('Право (лекц.) доц. Афанасьева Е.Г.')
        self.assertEqual((subject, teacher), ('Право', 'Афанасьева Е.Г.'))

    def test_multiline_cell(self):
        subject, kind, teacher, _ = split_cell(
            'Физическая культура (практ.зан.)\n  PhD')
        self.assertEqual((subject, kind, teacher),
                         ('Физическая культура', 'Практика', None))

    def test_typo_pakt(self):
        # «пакт.зан.» вместо «практ.зан.» — есть в файле.
        _, kind, _, _ = split_cell('Прогнозирование (пакт.зан.)')
        self.assertEqual(kind, 'Практика')

    def test_empty_cell(self):
        self.assertEqual(split_cell('   ')[0], None)


class HelperTests(TestCase):
    def test_tidy_name_adds_missing_dots(self):
        self.assertEqual(tidy_name('Соколова С'), 'Соколова С.')
        self.assertEqual(tidy_name('Машарипов С.И'), 'Машарипов С.И.')

    def test_tidy_name_drops_leftover_position_dot(self):
        # После «ст. преп.» остаётся точка — в имя она попадать не должна.
        self.assertEqual(tidy_name('. Шахназарова Н.В.'), 'Шахназарова Н.В.')

    def test_tidy_name_splits_glued_initials(self):
        self.assertEqual(tidy_name('АрхиповаЗ.З.'), 'Архипова З.З.')

    def test_parse_room_splits_building(self):
        self.assertEqual(parse_room('3/308'), ('308', '3'))
        self.assertEqual(parse_room(' 1 / 104 '), ('104', '1'))

    def test_parse_room_keeps_words(self):
        self.assertEqual(parse_room('спорт зал'), ('спорт зал', None))

    def test_parse_room_empty(self):
        self.assertEqual(parse_room(None), (None, None))

    def test_parse_time_accepts_dot_and_colon(self):
        self.assertEqual(parse_time('9.00-10.20'), parse_time('09:00-10:20'))
        self.assertIsNotNone(parse_time('15:30-16:50'))

    def test_parse_time_rejects_garbage(self):
        self.assertIsNone(parse_time('кураторский час'))


class ParityTests(TestCase):
    """Чётность недели: сначала номер недели, потом дата, потом семестр."""

    def workbook_with_header(self, header, date='12.10.2026 г.'):
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws['B2'] = header
        ws['D4'] = 'Дата'
        ws['F4'] = 'Пара'
        ws['G4'] = 'Время'
        ws['H4'] = 'профиль Экономика'
        ws['H5'] = '6301 (23 ст.)'
        ws['I5'] = 'ауд'
        if date:
            ws['D6'] = date
        ws['F6'] = 1
        ws['G6'] = '9.00-10.20'
        ws['H6'] = 'Финансы (лекц.) доц. Ершова М.Э.'
        ws['I6'] = '1/201'
        return ws

    def test_odd_semester_gives_odd_weeks(self):
        # Нет ни номера недели, ни даты — остаётся последний источник, семестр.
        ws = self.workbook_with_header('на 3 семестр', date=None)
        self.assertEqual(parity_of(ws), 1)

    def test_even_semester_gives_even_weeks(self):
        ws = self.workbook_with_header('на 4 семестр', date=None)
        self.assertEqual(parity_of(ws), 2)

    def test_mixed_semesters_give_every_week(self):
        # ОЗФ: «на 1,3,5,7,9 семестр» — нечётные, но смешать с чётными нельзя.
        ws = self.workbook_with_header('на 1,2,3,5 семестр', date=None)
        self.assertEqual(parity_of(ws), 0)

    def test_no_semester_gives_every_week(self):
        ws = self.workbook_with_header('расписание', date=None)
        self.assertEqual(parity_of(ws), 0)

    def test_week_number_beats_semester_line(self):
        # Строка «на 1 семестр» одинакова в файлах обеих недель, а «8 неделя»
        # стоит рядом с ней и говорит, что неделя чётная.
        ws = self.workbook_with_header(
            'на 1 семестр для студентов 1 курса 8 неделя с 19 октября',
            date='19.10.2026 г.')
        self.assertEqual(parity_of(ws), 2)

    def test_odd_week_number_gives_odd_weeks(self):
        ws = self.workbook_with_header('на 1 семестр 7 неделя',
                                       date='12.10.2026 г.')
        self.assertEqual(parity_of(ws), 1)

    def test_date_beats_semester_line(self):
        # ОЗФ: в шапке «на 1,3,5,7,9 семестр», а даты в таблице — 19 октября.
        # Студенты приходят в эти даты, значит показывать надо по ним.
        ws = self.workbook_with_header('на 1,3,5,7,9 семестр',
                                       date='19.10.2026 г.')
        self.assertEqual(parity_of(ws), 2)

    def test_conflicting_week_number_and_date_is_refused(self):
        from django.core.management.base import CommandError

        ws = self.workbook_with_header('на 1 семестр 8 неделя',
                                       date='12.10.2026 г.')
        with self.assertRaises(CommandError) as caught:
            parity_of(ws)
        self.assertIn('Не сходится чётность', str(caught.exception))

    def test_records_take_parity_from_sheet(self):
        ws = self.workbook_with_header(
            'на 1 семестр для студентов 1 курса 8 неделя с 19 октября',
            date='19.10.2026 г.')
        records, _ = read_sheet(ws)
        self.assertEqual([r['parity'] for r in records], [2])

    def test_loaded_lessons_keep_parity(self):
        import tempfile
        from pathlib import Path

        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws['B2'] = 'на 5 семестр'
        ws['D4'] = 'Дата'
        ws['F4'] = 'Пара'
        ws['G4'] = 'Время'
        ws['H5'] = '6301 (23 ст.)'
        ws['I5'] = 'ауд'
        ws['D6'] = '12.10.2026 г.'
        ws['F6'] = 1
        ws['G6'] = '9.00-10.20'
        ws['H6'] = 'Математический анализ (лекц.) доц. Камбаров И.О.'
        ws['I6'] = '3/307'
        tmp = tempfile.mkdtemp()
        path = str(Path(tmp) / 'odd.xlsx')
        wb.save(path)

        call_command('load_week_schedule', path, stdout=None)
        self.assertEqual(Lesson.objects.get().week_parity, 1)


def make_workbook(path, cells):
    """Мини-файл: один лист, одна группа, строки как в расписании вуза."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws['D4'] = 'Дата'
    ws['E4'] = 'Дни недели'
    ws['F4'] = 'Пара'
    ws['G4'] = 'Время'
    ws['H4'] = 'профиль Экономика предприятий'
    ws['H5'] = '6301 (23 ст.)'
    ws['I5'] = 'ауд'
    row = 6
    for day, pair, time, subject, room in cells:
        ws.cell(row, 4, f'{day:02d}.10.2026 г.')
        ws.cell(row, 5, 'ДЕНЬ')
        ws.cell(row, 6, pair)
        ws.cell(row, 7, time)
        ws.cell(row, 8, subject)
        ws.cell(row, 9, room)
        row += 1
    wb.save(path)
    return path


class LoadCommandTests(TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self.tmp = tempfile.mkdtemp()
        self.path = str(Path(self.tmp) / 'raspisanie.xlsx')
        make_workbook(self.path, [
            (12, 1, '9.00-10.20', 'Микроэкономика (лекц.) доц. Соколова С.', '3/308'),
            (12, 2, '10.30-11.50', 'Право (лекц.) ст. преп. Аксенов У.Т.', '1/402'),
            (13, 1, '9.00-10.20', 'Финансы (лекц.) доц. Ершова М.Э.', '2/105'),
            (14, 2, '10.25-11.45', 'Кураторский час Крылова Г.Ш.', '1/104'),
        ])

    def test_read_file_skips_curator_hours(self):
        records, skipped, pair_times = read_file(self.path)
        self.assertEqual(len(records), 3)
        self.assertEqual(skipped['кураторский час'], 1)
        self.assertEqual(sorted(pair_times), [1, 2])

    def test_dry_run_writes_nothing(self):
        call_command('load_week_schedule', self.path, dry_run=True)
        self.assertEqual(Lesson.objects.count(), 0)
        self.assertEqual(Subject.objects.count(), 0)

    def test_load_creates_everything(self):
        call_command('load_week_schedule', self.path, stdout=None)
        lesson = Lesson.objects.get(
            group__name='6301', day=1, lesson_time__number=1)
        self.assertEqual(lesson.subject.name, 'Микроэкономика')
        self.assertEqual(lesson.lesson_type.name, 'Лекция')
        self.assertEqual(lesson.teacher.full_name, 'Соколова С.')
        self.assertEqual(lesson.teacher.position, 'Доцент')
        self.assertEqual(lesson.room.name, '308')
        self.assertEqual(lesson.room.building, '3')
        self.assertEqual(lesson.lesson_time.number, 1)
        self.assertEqual(Group.objects.get().course, 1)
        self.assertEqual(
            Group.objects.get().specialty, 'Экономика предприятий')

    def test_repeated_load_does_not_duplicate(self):
        call_command('load_week_schedule', self.path, stdout=None)
        call_command('load_week_schedule', self.path, stdout=None)
        self.assertEqual(Lesson.objects.count(), 3)
        self.assertEqual(Teacher.objects.count(), 3)

    def test_replace_clears_previous_data(self):
        Subject.objects.create(name='Старый предмет')
        Teacher.objects.create(full_name='Старый Преподаватель')
        call_command('load_week_schedule', self.path, replace=True,
                     noinput=True, stdout=None)
        self.assertFalse(Subject.objects.filter(name='Старый предмет').exists())
        self.assertFalse(Teacher.objects.filter(full_name='Старый Преподаватель').exists())

    def test_replace_asks_before_clearing(self):
        Subject.objects.create(name='Старый предмет')
        answers = iter(['нет'])
        with mock.patch('builtins.input', lambda *a: next(answers)):
            call_command('load_week_schedule', self.path, replace=True, stdout=None)
        # Отказ должен оставить базу как была, а не записать половину.
        self.assertTrue(Subject.objects.filter(name='Старый предмет').exists())
        self.assertEqual(Lesson.objects.count(), 0)

    def test_missing_file_is_reported(self):
        with self.assertRaises(CommandError):
            call_command('load_week_schedule', f'{self.tmp}/net-takogo.xlsx', stdout=None)


class LessonTimeSyncTests(TestCase):
    def test_pair_time_is_updated_to_file(self):
        LessonTime.objects.create(number=1, start='06:30', end='07:30')
        LessonTime.objects.create(number=7, start='17:30', end='18:50')
        import tempfile
        from pathlib import Path

        tmp = tempfile.mkdtemp()
        path = make_workbook(str(Path(tmp) / 'r.xlsx'), [
            (12, 1, '9.00-10.20', 'Финансы (лекц.)', '1/201'),
        ])
        call_command('load_week_schedule', path, stdout=None)
        first = LessonTime.objects.get(number=1)
        self.assertEqual((first.start.hour, first.start.minute), (9, 0))