"""Проверка импорта из CSV/XLSX и защиты страниц."""

import shutil
import tempfile
from io import BytesIO
from pathlib import Path

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .importer import import_schedule
from .models import (
    Group,
    ImportLog,
    Lesson,
    LessonTime,
    Room,
    Subject,
    Teacher,
)

GOOD_CSV = """Группа;День;Пара;Предмет;Тип;Преподаватель;Аудитория;Недели
ЭК-11;пн;1;Финансы;Лекция;Иванов И.И.;Главный - 201;каждую
ЭК-11;вт;2;Статистика;Практика;Петров П.П.;305;нечёт
ЭК-11 3;среда;3;Маркетинг;Семинар;Сидорова М.А.;201;чёт
"""

BAD_DAY_CSV = """Группа;День;Пара;Предмет
ЭК-12;какой-то день;1;Финансы
"""

BAD_TIME_CSV = """Группа;День;Пара;Предмет
ЭК-12;пн;99;Финансы
"""

GRID_ROWS = [
    ['', '1\n8:00 - 9:20', '2\n9:30 - 10:50'],
    ['Mn', 'English\nEnG4\nF. Ruzmetova\n1/13', ''],
    ['Tu', 'Финансы\nЭК-01\nПетров И.Р.\nАуд. 201', ''],
    ['Wed', '', 'Philosophy\nSE G1\nV. Zaitsev\n1/10'],
    ['Thu', '', ''],
    ['Fri', '', ''],
    ['Sat', '', ''],
]


def build_grid_workbook():
    """Сетка «дни в строках, пары в колонках»."""
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Расписание'
    for row in GRID_ROWS:
        sheet.append(row)
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()


@override_settings(SECURE_SSL_REDIRECT=False)
class ImporterTests(TestCase):
    def setUp(self):
        for number, (start, end) in enumerate(
            [('08:30', '09:50'), ('10:00', '11:20'), ('11:40', '13:00')], start=1,
        ):
            LessonTime.objects.create(number=number, start=start, end=end)

    def _import(self, text, name='t.csv', dry_run=False):
        return import_schedule(text.encode('utf-8'), name, dry_run=dry_run)

    def test_imports_rows_and_creates_references(self):
        result = self._import(GOOD_CSV)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.created, 3)
        self.assertEqual(Lesson.objects.count(), 3)
        self.assertTrue(Group.objects.filter(name='ЭК-11', course=1).exists())
        self.assertTrue(Group.objects.filter(name='ЭК-11', course=3).exists())
        self.assertTrue(Subject.objects.filter(name='Финансы').exists())
        self.assertTrue(Teacher.objects.filter(full_name='Иванов И.И.').exists())
        self.assertEqual(Room.objects.count(), 3)
        self.assertTrue(Room.objects.filter(name='201', building='Главный').exists())
        self.assertTrue(Room.objects.filter(name='201', building='').exists())
        self.assertTrue(Room.objects.filter(name='305').exists())

    def test_parity_and_day_are_parsed(self):
        self._import(GOOD_CSV)
        even = Lesson.objects.get(subject__name='Статистика')
        self.assertEqual(even.day, 2)
        self.assertEqual(even.week_parity, 1)
        wed = Lesson.objects.get(subject__name='Маркетинг')
        self.assertEqual(wed.day, 3)
        self.assertEqual(wed.week_parity, 2)

    def test_dry_run_changes_nothing(self):
        result = self._import(GOOD_CSV, dry_run=True)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(Lesson.objects.count(), 0)
        self.assertIn('Проверено строк', ' '.join(result.warnings))

    def test_unknown_day_is_reported(self):
        result = self._import(BAD_DAY_CSV)
        self.assertFalse(result.ok)
        self.assertIn('не понял день', result.errors[0])
        self.assertEqual(Lesson.objects.count(), 0)

    def test_unknown_lesson_time_is_reported(self):
        result = self._import(BAD_TIME_CSV)
        self.assertFalse(result.ok)
        self.assertIn('пара «99»', result.errors[0])

    def test_empty_file_is_reported(self):
        result = self._import('')
        self.assertFalse(result.ok)

    def test_rerun_updates_instead_of_duplicating(self):
        self._import(GOOD_CSV)
        changed = GOOD_CSV.replace('Иванов И.И.', 'Новиков Н.Н.')
        result = self._import(changed)
        self.assertEqual(result.updated, 1)
        self.assertEqual(Lesson.objects.count(), 3)
        self.assertTrue(Teacher.objects.filter(full_name='Новиков Н.Н.').exists())

    def test_works_with_cp1251(self):
        data = GOOD_CSV.encode('cp1251')
        result = import_schedule(data, 't.csv')
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(Lesson.objects.count(), 3)

    def test_reads_xlsx(self):
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.append(['Группа', 'День', 'Пара', 'Предмет', 'Преподаватель'])
        sheet.append(['ЭК-20', 'пн', 1, 'Финансы', 'Иванов И.И.'])
        stream = BytesIO()
        workbook.save(stream)
        result = import_schedule(stream.getvalue(), 't.xlsx')
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(Lesson.objects.count(), 1)
        self.assertEqual(Lesson.objects.get().teacher.full_name, 'Иванов И.И.')


@override_settings(SECURE_SSL_REDIRECT=False)
class ImportViewTests(TestCase):
    """Импорт закрыт для всех, кроме сотрудников — тесты идут от staff."""

    def setUp(self):
        User.objects.create_user('importer', password='x', is_staff=True)
        self.client.force_login(User.objects.get(username='importer'))
        for number, (start, end) in enumerate(
            [('08:30', '09:50'), ('10:00', '11:20'), ('11:40', '13:00')], start=1,
        ):
            LessonTime.objects.create(number=number, start=start, end=end)
        self._tmp = tempfile.mkdtemp(prefix='import_test_')
        self._override = override_settings(
            SECURE_SSL_REDIRECT=False, IMPORT_TMP_DIR=Path(self._tmp),
        )
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(shutil.rmtree, self._tmp, True)

    def _upload(self, text, name='t.csv'):
        return SimpleUploadedFile(
            name, text.encode('utf-8'), content_type='text/csv',
        )

    def test_check_step_does_not_write(self):
        response = self.client.post(
            reverse('schedule:import'), {'file': self._upload(GOOD_CSV), 'check': '1'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['can_apply'])
        self.assertTrue(response.context['checked_only'])
        self.assertEqual(Lesson.objects.count(), 0)

    def test_apply_step_writes(self):
        self.client.post(
            reverse('schedule:import'), {'file': self._upload(GOOD_CSV), 'check': '1'},
        )
        response = self.client.post(reverse('schedule:import'), {'apply': '1'})
        self.assertRedirects(response, f"{reverse('schedule:import')}?ok=1")
        self.assertEqual(Lesson.objects.count(), 3)

    def test_errors_block_apply(self):
        self.client.post(
            reverse('schedule:import'), {'file': self._upload(BAD_DAY_CSV), 'check': '1'},
        )
        response = self.client.post(reverse('schedule:import'), {'apply': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_apply'])
        self.assertEqual(Lesson.objects.count(), 0)

    def test_missing_file_is_reported(self):
        response = self.client.post(reverse('schedule:import'), {'check': '1'})
        self.assertFalse(response.context['result'].ok)

    def test_oversized_file_is_rejected(self):
        from schedule.importer import MAX_UPLOAD_BYTES

        big = SimpleUploadedFile(
            'big.csv', b'a' * (MAX_UPLOAD_BYTES + 10), content_type='text/csv',
        )
        response = self.client.post(reverse('schedule:import'), {'file': big, 'check': '1'})
        self.assertFalse(response.context['result'].ok)
        self.assertIn('10 МБ', response.context['result'].errors[0])


@override_settings(SECURE_SSL_REDIRECT=False)
class ImportViewGridTests(TestCase):
    """«Проверить файл» на grid-книге не должно ничего писать в базу."""

    def setUp(self):
        User.objects.create_user('gridstaff', password='x', is_staff=True)
        self.client.force_login(User.objects.get(username='gridstaff'))
        self._tmp = tempfile.mkdtemp(prefix='import_grid_test_')
        self._override = override_settings(
            SECURE_SSL_REDIRECT=False, IMPORT_TMP_DIR=Path(self._tmp),
        )
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(shutil.rmtree, self._tmp, True)

    def _upload(self):
        return SimpleUploadedFile(
            'grid.xlsx', build_grid_workbook(),
            content_type=(
                'application/vnd.openxmlformats-officedocument'
                '.spreadsheetml.sheet'
            ),
        )

    def test_check_step_does_not_write(self):
        response = self.client.post(
            reverse('schedule:import'), {'file': self._upload(), 'check': '1'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['checked_only'])
        self.assertTrue(response.context['can_apply'])
        for model in (Lesson, Group, Subject, Teacher, Room, LessonTime):
            self.assertEqual(model.objects.count(), 0, model.__name__)

    def test_apply_step_writes_and_logs(self):
        self.client.post(
            reverse('schedule:import'), {'file': self._upload(), 'check': '1'},
        )
        response = self.client.post(reverse('schedule:import'), {'apply': '1'})
        self.assertRedirects(response, f"{reverse('schedule:import')}?ok=1")
        self.assertEqual(Lesson.objects.count(), 3)
        self.assertEqual(ImportLog.objects.count(), 1)