"""Тесты монопольной блокировки импорта и её обработки во вьюхе."""

import os
import shutil
import tempfile
import time
from pathlib import Path

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .import_lock import LOCK_NAME, ImportLocked, exclusive_import_lock
from .models import Lesson, LessonTime

CSV = (
    'Группа;День;Пара;Предмет;Преподаватель;Аудитория;Недели\n'
    'ЭК-11;пн;1;Финансы;Иванов И.И.;201;каждую\n'
)


@override_settings(SECURE_SSL_REDIRECT=False)
class ExclusiveImportLockTests(TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix='import_lock_test_')
        self.addCleanup(shutil.rmtree, self._tmp, True)
        self.tmp = Path(self._tmp)

    def test_second_lock_is_rejected(self):
        with exclusive_import_lock(self.tmp):
            with self.assertRaises(ImportLocked):
                with exclusive_import_lock(self.tmp):
                    pass

    def test_lock_is_released(self):
        with exclusive_import_lock(self.tmp):
            self.assertTrue((self.tmp / LOCK_NAME).exists())
        self.assertFalse((self.tmp / LOCK_NAME).exists())
        # повторный захват проходит
        with exclusive_import_lock(self.tmp):
            pass

    def test_lock_is_released_on_error(self):
        with self.assertRaises(ValueError):
            with exclusive_import_lock(self.tmp):
                raise ValueError('boom')
        self.assertFalse((self.tmp / LOCK_NAME).exists())

    def test_stale_lock_is_taken_over(self):
        lock = self.tmp / LOCK_NAME
        lock.write_text('99999 0')
        old = time.time() - 3600
        os.utime(lock, (old, old))
        with exclusive_import_lock(self.tmp, stale_seconds=60):
            pass


@override_settings(SECURE_SSL_REDIRECT=False)
class ImportLockViewTests(TestCase):
    """Параллельный ``apply`` не должен ронять базу и отдавать 500."""

    def setUp(self):
        User.objects.create_user('lockstaff', password='x', is_staff=True)
        self.client.force_login(User.objects.get(username='lockstaff'))
        LessonTime.objects.create(number=1, start='08:30', end='09:50')
        self._tmp = tempfile.mkdtemp(prefix='import_lock_view_')
        self._override = override_settings(
            SECURE_SSL_REDIRECT=False, IMPORT_TMP_DIR=Path(self._tmp),
        )
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(shutil.rmtree, self._tmp, True)

    def _upload(self):
        return SimpleUploadedFile(
            't.csv', CSV.encode('utf-8'), content_type='text/csv',
        )

    def test_apply_while_locked_reports_error_not_500(self):
        self.client.post(
            reverse('schedule:import'), {'file': self._upload(), 'check': '1'},
        )
        with exclusive_import_lock(Path(self._tmp)):
            response = self.client.post(reverse('schedule:import'), {'apply': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['result'].ok)
        self.assertIn('уже выполняется', response.context['result'].errors[0])
        self.assertEqual(Lesson.objects.count(), 0)

    def test_apply_unlocked_writes(self):
        self.client.post(
            reverse('schedule:import'), {'file': self._upload(), 'check': '1'},
        )
        response = self.client.post(reverse('schedule:import'), {'apply': '1'})
        self.assertRedirects(response, f"{reverse('schedule:import')}?ok=1")
        self.assertEqual(Lesson.objects.count(), 1)
