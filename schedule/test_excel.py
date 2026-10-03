"""Тесты разбора Excel: сетка «дни в строках, пары в колонках»."""

import shutil
import tempfile
from io import BytesIO
from pathlib import Path

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .excel import (
    DAY_NAMES,
    detect_grid,
    import_grid,
    import_workbook,
    norm_day,
    open_workbook,
    parse_cell,
)
from .models import Group, Lesson, LessonTime, Room, Subject, Teacher

TEMPLATE = 'C:\\Users\\Den\\Documents\\Расписание_как_на_фото.xlsx'


def build_workbook(rows, path='t.xlsx'):
    """Собирает книгу в том же виде, что и файл-шаблон."""
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Расписание'
    for row in rows:
        sheet.append(row)
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()


FILLED = [
    ['', '1\n8:00 - 9:20', '2\n9:30 - 10:50', '3\n11:00 - 12:20'],
    ['Mn', 'English\nEnG4\nF. Ruzmetova\n1/13', '', 'Discrete Mathematics\nG1\nA. Kudzinava\n1/15'],
    ['Tu', 'Финансы\nЭК-01\nПетров И.Р.\nАуд. 201', '', ''],
    ['Wed', '', 'Philosophy\nSE G1\nV. Zaitsev\n1/10', ''],
    ['Thu', "Teacher's Day", '', ''],
    ['Fri', '', '', 'Маркетинг\nG5\nB. Toshtemirov\n2/15'],
    ['Sat', '', '', ''],
]

# Плоская таблица: одна строка — одно занятие. detect_grid её не находит,
# поэтому import_workbook обязан откатиться на плоский разбор.
FLAT_TABLE = [
    ['Группа', 'День', 'Пара', 'Предмет', 'Преподаватель'],
    ['ЭК-01', 'пн', '1', 'Финансы', 'Петров И.Р.'],
    ['ЭК-01', 'вт', '2', 'Статистика', 'Иванов И.И.'],
    ['ЭК-01', 'ср', '1', 'Маркетинг', 'Сидоров С.С.'],
]

# Плоская таблица, где первая строка валидна, а вторая — с непонятным днём:
# плоский разбор успевает частично «прочитать» файл, но возвращает ошибку.
BROKEN_FLAT = [
    ['Группа', 'День', 'Пара', 'Предмет'],
    ['ЭК-01', 'пн', '1', 'Финансы'],
    ['ЭК-01', 'абракадабра', '1', 'Статистика'],
]


class NormDayTests(TestCase):
    def test_english_abbreviations(self):
        self.assertEqual(norm_day('Mn'), 1)
        self.assertEqual(norm_day('Tu'), 2)
        self.assertEqual(norm_day('Wed'), 3)
        self.assertEqual(norm_day('Thu'), 4)
        self.assertEqual(norm_day('Fri'), 5)
        self.assertEqual(norm_day('Sat'), 6)

    def test_russian_names(self):
        self.assertEqual(norm_day('пн'), 1)
        self.assertEqual(norm_day('Вторник'), 2)
        self.assertEqual(norm_day('Четверг'), 4)
        self.assertEqual(norm_day('Суббота'), 6)

    def test_numbers_and_junk(self):
        self.assertEqual(norm_day('1'), 1)
        self.assertIsNone(norm_day('Воскресенье'))
        self.assertIsNone(norm_day(''))
        self.assertIsNone(norm_day(None))
        self.assertIsNone(norm_day('9'))


class ParseCellTests(TestCase):
    def test_english_layout(self):
        parts = parse_cell('English\nEnG4\nF. Ruzmetova\n1/13')
        self.assertEqual(parts['subject'], 'English')
        self.assertEqual(parts['group'], 'EnG4')
        self.assertEqual(parts['teacher'], 'F. Ruzmetova')
        self.assertEqual(parts['room'], '')

    def test_multi_word_subject_is_not_a_teacher(self):
        parts = parse_cell('Discrete Mathematics\nG5\nB. Toshtemirov\n1/15')
        self.assertEqual(parts['subject'], 'Discrete Mathematics')
        self.assertEqual(parts['teacher'], 'B. Toshtemirov')

    def test_russian_teacher_with_initials(self):
        parts = parse_cell('Финансы\nЭК-01\nПетров И.Р.\nАуд. 201')
        self.assertEqual(parts['subject'], 'Финансы')
        self.assertEqual(parts['group'], 'ЭК-01')
        self.assertEqual(parts['teacher'], 'Петров И.Р.')
        self.assertEqual(parts['room'], '201')

    def test_group_with_course_suffix(self):
        parts = parse_cell('Практика\nЭК-01 2\nКаримов Б.Р.')
        self.assertEqual(parts['group'], 'ЭК-01 2')
        self.assertEqual(parts['teacher'], 'Каримов Б.Р.')

    def test_room_inside_line(self):
        self.assertEqual(parse_cell('Финансы / ауд. 201')['room'], '201')
        self.assertEqual(parse_cell('Финансы / ауд. 201')['subject'], 'Финансы')

    def test_holiday_is_a_subject(self):
        parts = parse_cell("Teacher's Day")
        self.assertEqual(parts['subject'], "Teacher's Day")
        self.assertEqual(parts['teacher'], '')

    def test_empty_cell(self):
        parts = parse_cell('')
        self.assertEqual(parts['subject'], '')
        self.assertEqual(parts['group'], '')


@override_settings(SECURE_SSL_REDIRECT=False)
class DetectGridTests(TestCase):
    def test_finds_pairs_and_days(self):
        sheets = open_workbook(build_workbook(FILLED), 't.xlsx')
        grid = detect_grid(sheets[0])
        self.assertIsNotNone(grid)
        self.assertEqual([p['number'] for p in grid['pairs']], [1, 2, 3])
        self.assertEqual(grid['pairs'][0]['start'], '08:00')
        self.assertEqual(grid['pairs'][0]['end'], '09:20')
        self.assertEqual(grid['day_column'], 0)
        self.assertEqual([d['day'] for d in grid['days']], [1, 2, 3, 4, 5, 6])

    def test_empty_table_still_has_grid(self):
        rows = [
            ['', '1\n8:00 - 9:20', '2\n9:30 - 10:50'],
            ['Mn', None, None], ['Tu', None, None], ['Wed', None, None],
        ]
        grid = detect_grid(open_workbook(build_workbook(rows), 't.xlsx')[0])
        self.assertIsNotNone(grid)
        self.assertEqual(len(grid['pairs']), 2)
        self.assertEqual(len(grid['days']), 3)

    def test_not_a_timetable(self):
        rows = [['Список', 'студентов'], ['Иванов', '6203']]
        self.assertIsNone(detect_grid(open_workbook(build_workbook(rows), 't.xlsx')[0]))


@override_settings(SECURE_SSL_REDIRECT=False)
class ImportGridTests(TestCase):
    def test_empty_template_imports_nothing_without_errors(self):
        data = build_workbook([
            ['', '1\n8:00 - 9:20', '2\n9:30 - 10:50'],
            ['Mn', None, None], ['Tu', None, None], ['Wed', None, None],
            ['Thu', None, None], ['Fri', None, None], ['Sat', None, None],
        ])
        result = import_grid(data, 't.xlsx')
        self.assertEqual(result.errors, [])
        self.assertEqual(result.created, 0)
        self.assertEqual(Lesson.objects.count(), 0)
        # но время пар создалось из шапки
        self.assertEqual(LessonTime.objects.count(), 2)
        self.assertIn('таблица пустая', ' '.join(result.warnings))

    def test_creates_lesson_times_from_header(self):
        import_grid(build_workbook(FILLED), 't.xlsx')
        times = {lt.number: (lt.start.strftime('%H:%M'), lt.end.strftime('%H:%M'))
                 for lt in LessonTime.objects.all()}
        self.assertEqual(times[1], ('08:00', '09:20'))
        self.assertEqual(times[3], ('11:00', '12:20'))

    def test_imports_lessons_with_groups_and_teachers(self):
        result = import_grid(build_workbook(FILLED), 't.xlsx')
        self.assertEqual(result.errors, [])
        self.assertEqual(Lesson.objects.count(), 6)

        english = Lesson.objects.get(subject__name='English')
        self.assertEqual(english.day, 1)
        self.assertEqual(english.lesson_time.number, 1)
        self.assertEqual(english.group.name, 'EnG4')
        self.assertEqual(english.teacher.full_name, 'F. Ruzmetova')

        russian = Lesson.objects.get(subject__name='Финансы')
        self.assertEqual(russian.teacher.full_name, 'Петров И.Р.')
        self.assertEqual(russian.room.name, '201')

    def test_second_import_does_not_duplicate(self):
        data = build_workbook(FILLED)
        import_grid(data, 't.xlsx')
        result = import_grid(data, 't.xlsx')
        self.assertEqual(result.created, 0)
        self.assertEqual(Lesson.objects.count(), 6)

    def test_changed_teacher_updates_lesson(self):
        import_grid(build_workbook(FILLED), 't.xlsx')
        changed = [list(row) for row in FILLED]
        changed[1][1] = 'English\nEnG4\nНовиков Н.Н.\n1/13'
        result = import_grid(build_workbook(changed), 't.xlsx')
        self.assertEqual(result.updated, 1)
        self.assertEqual(Lesson.objects.count(), 6)
        self.assertTrue(
            Lesson.objects.filter(teacher__full_name='Новиков Н.Н.').exists()
        )

    def test_file_without_grid_reports_error(self):
        result = import_grid(
            build_workbook([['Список', 'студентов'], ['Иванов', '6203']]), 't.xlsx',
        )
        self.assertTrue(result.errors)
        self.assertIn('сетка расписания', result.errors[0])

    def test_day_names_cover_six_days(self):
        self.assertEqual(len(DAY_NAMES), 6)


@override_settings(SECURE_SSL_REDIRECT=False)
class ImportWorkbookDryRunTests(TestCase):
    """Флаг dry_run обязан дойти до сетки, а не только до плоской таблицы."""

    def test_dry_run_does_not_write(self):
        result = import_workbook(build_workbook(FILLED), 't.xlsx', dry_run=True)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.created, 6)
        for model in (Lesson, Group, Subject, Teacher, Room, LessonTime):
            self.assertEqual(model.objects.count(), 0, model.__name__)

    def test_apply_after_dry_run_writes(self):
        data = build_workbook(FILLED)
        import_workbook(data, 't.xlsx', dry_run=True)
        self.assertEqual(Lesson.objects.count(), 0)

        result = import_workbook(data, 't.xlsx', dry_run=False)
        self.assertEqual(result.errors, [])
        self.assertEqual(Lesson.objects.count(), 6)
        self.assertEqual(LessonTime.objects.count(), 3)


@override_settings(SECURE_SSL_REDIRECT=False)
class ImportWorkbookFallbackTests(TestCase):
    """Fallback «сетка → плоская таблица» не должен применять импорт дважды."""

    def setUp(self):
        # плоскому разбору нужны уже настроенные пары, иначе он вернёт ошибку
        LessonTime.objects.create(number=1, start='08:00', end='09:20')
        LessonTime.objects.create(number=2, start='09:30', end='10:50')

    def _counters(self):
        return {
            model.__name__: model.objects.count()
            for model in (Lesson, Group, Subject, Teacher, Room, LessonTime)
        }

    def test_grid_success_reports_source_grid(self):
        result = import_workbook(build_workbook(FILLED), 't.xlsx')
        self.assertEqual(result.errors, [])
        self.assertEqual(result.source, 'grid')
        self.assertEqual(result.source_label, 'сетка (дни в строках, пары в колонках)')
        self.assertEqual(Lesson.objects.count(), 6)

    def test_grid_error_falls_back_to_flat_without_grid_writes(self):
        result = import_workbook(build_workbook(FLAT_TABLE), 't.xlsx')
        self.assertEqual(result.errors, [])
        self.assertEqual(result.source, 'flat')
        # в базе только записи плоской таблицы, ни одной «сеточной»
        self.assertEqual(result.created, 3)
        self.assertEqual(Lesson.objects.count(), 3)
        # время пар из ручной настройки, а не из несуществующей шапки сетки
        self.assertEqual(LessonTime.objects.count(), 2)

    def test_both_parsers_fail_leaves_db_untouched(self):
        before = self._counters()
        result = import_workbook(build_workbook(BROKEN_FLAT), 't.xlsx')
        self.assertTrue(result.errors)
        self.assertEqual(result.source, '')
        # частичная запись плоского разбора откачена: ни одного нового объекта
        self.assertEqual(self._counters(), before)

    def test_dry_run_fallback_reports_source_and_does_not_write(self):
        result = import_workbook(build_workbook(FLAT_TABLE), 't.xlsx', dry_run=True)
        self.assertEqual(result.source, 'flat')
        for model in (Lesson, Group, Subject, Teacher, Room):
            self.assertEqual(model.objects.count(), 0, model.__name__)

    def test_csv_file_uses_flat_source(self):
        data = ('Группа;День;Пара;Предмет\nЭК-01;пн;1;Финансы\n').encode('utf-8')
        result = import_workbook(data, 't.csv')
        self.assertEqual(result.errors, [])
        self.assertEqual(result.source, 'flat')
        self.assertEqual(Lesson.objects.count(), 1)


@override_settings(SECURE_SSL_REDIRECT=False)
class ImportSourceUiTests(TestCase):
    """UI обязан прямо сообщать, какой разбор сработал."""

    def setUp(self):
        self.staff = User.objects.create_user('staff_src', password='x',
                                              is_staff=True)
        self.client.force_login(self.staff)
        LessonTime.objects.create(number=1, start='08:00', end='09:20')
        LessonTime.objects.create(number=2, start='09:30', end='10:50')
        self._tmp = tempfile.mkdtemp(prefix='importsrc_')
        self._override = override_settings(IMPORT_TMP_DIR=Path(self._tmp))
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(shutil.rmtree, self._tmp, True)

    def _check(self, data, name='t.xlsx'):
        return self.client.post(
            reverse('schedule:import'),
            {'file': SimpleUploadedFile(name, data), 'check': '1'},
        )

    def test_fallback_reported_as_flat_table(self):
        response = self._check(build_workbook(FLAT_TABLE))
        self.assertContains(response, 'плоская таблица')
        self.assertEqual(Lesson.objects.count(), 0, 'проверка не пишет в базу')

    def test_grid_success_reported_as_grid(self):
        response = self._check(build_workbook(FILLED))
        self.assertContains(response, 'сетка (дни в строках, пары в колонках)')

    def test_both_failed_reported_as_nothing_applied(self):
        response = self._check(build_workbook(BROKEN_FLAT))
        self.assertContains(response, 'ни один разбор не удался')


@override_settings(SECURE_SSL_REDIRECT=False)
class RealTemplateFileTests(TestCase):
    """Проверка на настоящем файле из Documents, если он есть."""

    def test_user_template_file_is_detected(self):
        import os

        if not os.path.exists(TEMPLATE):
            self.skipTest('файл-шаблон не найден')
        with open(TEMPLATE, 'rb') as fh:
            data = fh.read()
        sheets = open_workbook(data, TEMPLATE)
        self.assertTrue(sheets)
        grid = detect_grid(sheets[0])
        self.assertIsNotNone(grid)
        self.assertEqual(len(grid['pairs']), 8)
        self.assertEqual(len(grid['days']), 6)
        self.assertEqual(grid['pairs'][0]['start'], '08:00')
        self.assertEqual(grid['pairs'][-1]['end'], '20:20')

        result = import_grid(data, TEMPLATE)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.created, 0, 'в шаблоне нет занятий')
        self.assertEqual(LessonTime.objects.count(), 8)