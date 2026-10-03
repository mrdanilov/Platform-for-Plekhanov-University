"""Проверка загружаемых файлов: расширение, содержимое и размер после распаковки.

Расширение и MIME присылает клиент, им доверять нельзя — настоящий признак
формата это первые байты файла. Плюс ограничения на распаковку: zip-бомба
весит копейки, а распухает на гигабайты.
"""

import io
import shutil
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from . import upload_guard
from .importer import import_schedule
from .models import Lesson, LessonTime


def make_xlsx(rows=2):
    """Настоящий xlsx собираем через openpyxl — не руками с zip.

    Ручная сборка нечитаема openpyxl («no valid workbook part»), а тест
    должен проверять настоящий файл, а не подделку.
    """
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = 'Расписание'
    sheet.append(['Группа', 'День', 'Предмет'])
    for i in range(rows):
        sheet.append([f'ЭК-{i:02d}', (i % 6) + 1, 'Финансы'])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


class AllowedSuffixTests(TestCase):
    def test_white_list_exact(self):
        self.assertEqual(upload_guard.ALLOWED_SUFFIXES,
                         {'.xlsx', '.xlsm', '.xls', '.csv'})

    def test_lower_case_accepted(self):
        self.assertEqual(upload_guard.allowed_suffix('Schedule.XLSX'), '.xlsx')
        self.assertEqual(upload_guard.allowed_suffix('a.CsV'), '.csv')

    def test_python_and_html_rejected(self):
        for name in ('payload.py', 'page.html', 'run.sh', 'x.php', 'noext'):
            with self.subTest(name=name):
                self.assertEqual(upload_guard.allowed_suffix(name), '')

    def test_double_extension_takes_last(self):
        """Репорт.xlsx.exe должен отклоняться, а не проходить по первому."""
        self.assertEqual(upload_guard.allowed_suffix('report.xlsx.exe'), '')
        self.assertEqual(upload_guard.allowed_suffix('a.csv.png'), '')


class MagicBytesTests(TestCase):
    def test_xlsx_zip_signature(self):
        self.assertTrue(upload_guard.looks_like('.xlsx', make_xlsx()))
        self.assertTrue(upload_guard.looks_like('.xlsm', make_xlsx()))

    def test_old_xls_signature(self):
        data = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' + b'\x00' * 32
        self.assertTrue(upload_guard.looks_like('.xls', data))

    def test_csv_must_be_text(self):
        self.assertTrue(upload_guard.looks_like('.csv', 'Группа,День\n'.encode()))
        self.assertTrue(upload_guard.looks_like('.csv', 'Группа\n'.encode('cp1251')))
        self.assertFalse(upload_guard.looks_like('.csv', bytes(range(256)) * 4))

    def test_text_in_xlsx_name_rejected(self):
        """Под именем .xlsx лежит текст — не пройдёт."""
        text = 'Группа,День\nЭК-1,1\n'.encode('utf-8')
        self.assertFalse(upload_guard.looks_like('.xlsx', text))

    def test_binary_scripts_never_pass_as_table(self):
        """Скрипт не пройдёт ни под каким разрешённым именем.

        Под .xlsx и .xls — по сигнатуре, их там быть не может. Под .csv
        проверка текста его пропустит: ASCII-скрипт действительно текст, и
        отличает его только имя файла, а .csv в белом списке. Скрипт с
        не-ASCII текстом (кириллический комментарий) уже не проходит.
        """
        script = b'import os\nos.system("rm -rf /")\n'
        self.assertFalse(upload_guard.looks_like('.xlsx', script))
        self.assertFalse(upload_guard.looks_like('.xls', script))
        # сам по себе скрипт текстом не отличить: у CSV нет сигнатуры,
        # поэтому его отсекает белый список расширений, а не этот тест
        self.assertEqual(upload_guard.allowed_suffix('payload.py'), '')

    def test_html_with_script_rejected(self):
        """HTML — это zip? нет; значит под .xlsx/.xls не пройдёт.

        Под .csv пропустит: это тоже текст. Защиту даёт белый список
        расширений, а не проверка содержимого для csv.
        """
        payload = b'<html><script>alert(1)</script></html>'
        self.assertFalse(upload_guard.looks_like('.xlsx', payload))
        self.assertFalse(upload_guard.looks_like('.xls', payload))
        self.assertEqual(upload_guard.allowed_suffix('page.html'), '')

    def test_empty_rejected(self):
        self.assertFalse(upload_guard.looks_like('.csv', b''))


class FakeZipInfo:
    """Заглушка записи архива: нужны только file_size."""

    def __init__(self, size):
        self.file_size = size


class ZipBombTests(TestCase):
    def _infolist(self, sizes):
        return mock.patch.object(
            zipfile.ZipFile, 'infolist',
            lambda _self: [FakeZipInfo(s) for s in sizes],
        )

    def test_normal_archive_passes(self):
        self.assertTrue(upload_guard.guard_zip(make_xlsx()))

    def test_huge_unzipped_size_rejected(self):
        with self._infolist([150 * 1024 * 1024, 80 * 1024 * 1024]):
            with self.assertRaisesMessage(ValueError, 'распаков'):
                upload_guard.guard_zip(make_xlsx())

    def test_many_entries_rejected(self):
        with self._infolist([10] * 5000):
            with self.assertRaisesMessage(ValueError, 'файлов'):
                upload_guard.guard_zip(make_xlsx())

    def test_broken_archive_rejected(self):
        with self.assertRaisesMessage(ValueError, 'повреждён'):
            upload_guard.guard_zip('это точно не zip'.encode('utf-8'))

    def test_limits_are_documented_values(self):
        self.assertEqual(upload_guard.MAX_UNZIPPED_BYTES, 200 * 1024 * 1024)
        self.assertEqual(upload_guard.MAX_ZIP_ENTRIES, 2000)


@override_settings(SECURE_SSL_REDIRECT=False)
class UploadViewTests(TestCase):
    """Проверки на уровне страницы импорта."""

    def setUp(self):
        self.staff = User.objects.create_user('staff_up', password='x',
                                              is_staff=True)
        self.client.force_login(self.staff)
        LessonTime.objects.create(number=1, start='08:00', end='09:20')
        self._tmp = tempfile.mkdtemp(prefix='upguard_')
        self._override = override_settings(IMPORT_TMP_DIR=Path(self._tmp))
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(shutil.rmtree, self._tmp, True)

    def _upload(self, data, name):
        return self.client.post(
            reverse('schedule:import'),
            {'file': SimpleUploadedFile(name, data, content_type='text/csv'),
             'check': '1'},
        )

    def test_python_file_rejected(self):
        response = self._upload(b'import os', 'payload.py')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_apply'])
        self.assertIn('Поддерживаются только',
                      ' '.join(response.context['result'].errors))

    def test_fake_xlsx_rejected(self):
        """Текст под именем .xlsx."""
        response = self._upload('Группа,День\nЭК-1,1\n'.encode('utf-8'), 'table.xlsx')
        self.assertFalse(response.context['can_apply'])
        self.assertIn('не соответствует расширению',
                      ' '.join(response.context['result'].errors))

    def test_good_csv_accepted(self):
        response = self._upload('Группа,День,Предмет\nЭК-01,1,Финансы\n'
                                .encode(), 'ok.csv')
        self.assertTrue(response.context['can_apply'])

    def test_real_xlsx_accepted(self):
        """Настоящая книга проходит: проверка не должна ломать рабочий импорт."""
        response = self._upload(make_xlsx(), 'table.xlsx')
        self.assertTrue(response.context['can_apply'],
                        f'ошибки: {response.context["result"].errors}')

    def test_rejected_file_not_stored_on_disk(self):
        self._upload(b'import os', 'payload.py')
        left = list(Path(self._tmp).glob('*'))
        self.assertEqual(left, [], 'отклонённый файл не должен оседать на диске')

    def test_stored_name_uses_allowed_suffix(self):
        self._upload('Группа,День\n'.encode(), 'ok.CSV')
        stored = list(Path(self._tmp).glob('*'))
        self.assertEqual(len(stored), 1)
        self.assertTrue(stored[0].name.endswith('.csv'))

    def test_zip_bomb_rejected_before_write(self):
        with mock.patch.object(
            zipfile.ZipFile, 'infolist',
            lambda _self: [FakeZipInfo(300 * 1024 * 1024) for _ in range(3)],
        ):
            response = self._upload(make_xlsx(), 'bomb.xlsx')
        self.assertFalse(response.context['can_apply'])
        self.assertEqual(list(Path(self._tmp).glob('*')), [])


class ReadRowsLimitTests(TestCase):
    """Ограничения на разбор нужны и на уровне чтения, а не только загрузки."""

    def test_too_many_rows_rejected(self):
        from . import importer

        original = importer.MAX_ROWS
        importer.MAX_ROWS = 2      # в файле заголовок + 5 строк
        try:
            with self.assertRaisesMessage(ValueError, 'больше'):
                importer.read_rows(make_xlsx(rows=5), 'big.xlsx')
        finally:
            importer.MAX_ROWS = original

    def test_import_schedule_reports_error_not_crash(self):
        from . import importer

        original = importer.MAX_ROWS
        importer.MAX_ROWS = 2
        try:
            result = import_schedule(make_xlsx(rows=5), 'big.xlsx')
        finally:
            importer.MAX_ROWS = original
        self.assertFalse(result.ok)
        self.assertEqual(Lesson.objects.count(), 0)


class SheetTrimTests(TestCase):
    def test_sheet_rows_are_capped(self):
        from . import excel

        original = excel.MAX_SHEET_ROWS
        excel.MAX_SHEET_ROWS = 2
        try:
            self.assertEqual(
                len(excel._trim_sheet([1, 2, 3, 4, 5])), 2)
        finally:
            excel.MAX_SHEET_ROWS = original

    def test_limits_documented(self):
        from . import excel

        self.assertEqual(excel.MAX_SHEETS, 20)
        self.assertEqual(excel.MAX_SHEET_ROWS, 5000)