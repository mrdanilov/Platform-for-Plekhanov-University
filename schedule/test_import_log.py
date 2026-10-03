"""Журнал импорта: кто, когда и сколько записей затронул."""

import shutil
import tempfile
from pathlib import Path

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import ImportLog, Lesson, LessonTime

GOOD_CSV = (
    'Группа,День,Пара,Предмет,Преподаватель,Аудитория\n'
    'ЭК-21,1,1,Финансы,Петров И.Р.,201\n'
    'ЭК-21,2,1,Финансы,Петров И.Р.,201\n'
    'ЭК-21,3,1,Финансы,Петров И.Р.,201\n'
)


@override_settings(SECURE_SSL_REDIRECT=False)
class ImportLogTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('staff_log', password='x',
                                              is_staff=True)
        self.client.force_login(self.staff)
        for number, (start, end) in enumerate(
            [('08:30', '09:50'), ('10:00', '11:20'), ('11:40', '13:00')], start=1,
        ):
            LessonTime.objects.create(number=number, start=start, end=end)
        self._tmp = tempfile.mkdtemp(prefix='importlog_')
        self._override = override_settings(IMPORT_TMP_DIR=Path(self._tmp))
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(shutil.rmtree, self._tmp, True)

    def _import(self, filename='raspisanie.csv'):
        self.client.post(
            reverse('schedule:import'),
            {'file': SimpleUploadedFile(filename, GOOD_CSV.encode('utf-8'),
                                        content_type='text/csv'),
             'check': '1'},
        )
        return self.client.post(reverse('schedule:import'), {'apply': '1'})

    def test_apply_creates_log_entry(self):
        self._import()
        self.assertEqual(ImportLog.objects.count(), 1)
        entry = ImportLog.objects.get()
        self.assertEqual(entry.user, self.staff)
        self.assertEqual(entry.username, 'staff_log')
        self.assertEqual(entry.filename, 'raspisanie.csv')
        self.assertEqual(entry.created_count, 3)
        self.assertTrue(entry.created_at)
        self.assertTrue(entry.summary)

    def test_check_step_does_not_log(self):
        """Журналируем только применение, проверка файла никого не меняет."""
        self.client.post(
            reverse('schedule:import'),
            {'file': SimpleUploadedFile('r.csv', GOOD_CSV.encode('utf-8'),
                                        content_type='text/csv'),
             'check': '1'},
        )
        self.assertEqual(ImportLog.objects.count(), 0)

    def test_failed_import_does_not_log(self):
        """Сломанный файл ничего не менял — события импорта не было."""
        self.client.post(
            reverse('schedule:import'),
            {'file': SimpleUploadedFile('bad.csv', 'ерунда'.encode('utf-8'),
                                        content_type='text/csv'),
             'check': '1'},
        )
        self.client.post(reverse('schedule:import'), {'apply': '1'})
        self.assertEqual(ImportLog.objects.count(), 0)

    def test_anonymous_cannot_import_and_log(self):
        self.client.logout()
        self._import()
        self.assertEqual(ImportLog.objects.count(), 0)
        self.assertEqual(Lesson.objects.count(), 0)

    def test_log_ordered_newest_first(self):
        self._import()
        self._import('vtoroy.csv')
        entries = list(ImportLog.objects.all())
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].filename, 'vtoroy.csv')
        self.assertEqual(entries[1].filename, 'raspisanie.csv')

    def test_username_kept_if_user_deleted(self):
        """Логин продублирован строкой: после удаления учётки запись читаема."""
        self._import()
        self.staff.delete()
        entry = ImportLog.objects.get()
        self.assertIsNone(entry.user, 'ссылка должна обнулиться, не падать')
        self.assertEqual(entry.username, 'staff_log')