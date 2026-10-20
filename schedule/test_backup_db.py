"""Бэкап, который не делает копию, — это не бэкап.

Скрипт `tools/backup_db.py` — единственная защита расписания на рабочей
машине: автобэкапа нет, копии делаются руками и до сих пор делались
нерегулярно. Поэтому проверяется не оформление вывода, а то, что после
запуска на диске появляется файл, который открывается, проходит проверку
целостности и содержит то же, что и база.

Отдельно проверяется поведение, которое ломается молча:

* вторая копия в ту же секунду не затирает первую;
* просроченные копии убираются, но предыдущая остаётся;
* отсутствие базы — это сообщение и код 1, а не трейсбек.

Источником для проверок служит отдельный файл SQLite, а не рабочая
база. Тестовая база Django живёт в памяти и скрипту не видна, а трогать
боевой файл проверками — ровно то, чего этими проверками и не должно
случиться.
"""

import contextlib
import datetime
import importlib.util
import io
import os
import pathlib
import sqlite3
import tempfile

from django.test import SimpleTestCase

# `tools/` — не пакет: там нет `__init__.py` и в PYTHONPATH его нет.
# Скрипт подключается по пути, как его и запускают — «python tools/…».
_TOOLS = pathlib.Path(__file__).resolve().parent.parent / 'tools'
_spec = importlib.util.spec_from_file_location(
    'timetable_backup_db', _TOOLS / 'backup_db.py')
backup_db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backup_db)


class BackupCreatesWorkingCopyTests(SimpleTestCase):
    """Копия после запуска открывается и содержит то же, что и база."""

    def setUp(self):
        self.holder = tempfile.TemporaryDirectory()
        self.addCleanup(self.holder.cleanup)
        self.work = pathlib.Path(self.holder.name)
        self.backup_dir = self.work / 'backups'
        self.database = self.make_database(lessons=3)

        os.environ['TIMETABLE_BACKUP_DIR'] = str(self.backup_dir)
        self.addCleanup(os.environ.pop, 'TIMETABLE_BACKUP_DIR', None)
        os.environ['TIMETABLE_DB'] = str(self.database)
        self.addCleanup(os.environ.pop, 'TIMETABLE_DB', None)

    @staticmethod
    def make_database_at(path, lessons):
        """База такой же формы, как рабочая: одна таблица занятий."""
        connection = sqlite3.connect(path)
        with connection:
            connection.execute(
                'CREATE TABLE schedule_lesson ('
                'id INTEGER PRIMARY KEY, subject_id INTEGER, '
                'day INTEGER, lesson_time_id INTEGER)')
            connection.executemany(
                'INSERT INTO schedule_lesson VALUES (?, ?, ?, ?)',
                [(number, number, number % 6 + 1, 1)
                 for number in range(1, lessons + 1)],
            )
        connection.close()
        return path

    def make_database(self, lessons):
        return self.make_database_at(self.work / 'db.sqlite3', lessons)

    def run_backup(self):
        """Запуск скрипта как команды, с перехваченным выводом."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = backup_db.main()
        return code, out.getvalue(), err.getvalue()

    def copies(self):
        if not self.backup_dir.exists():
            return []
        return sorted(p.name for p in self.backup_dir.glob('db_*.sqlite3'))

    def rows_in(self, name):
        connection = sqlite3.connect(self.backup_dir / name)
        try:
            return connection.execute(
                'SELECT id, subject_id, day FROM schedule_lesson '
                'ORDER BY id').fetchall()
        finally:
            connection.close()

    def test_backup_creates_a_file_in_the_named_folder(self):
        code, out, err = self.run_backup()
        self.assertEqual(code, 0, f'скрипт сообщил об ошибке: {err}')
        self.assertEqual(len(self.copies()), 1, 'копия не появилась')
        self.assertIn('Бэкап:', out)

    def test_copy_passes_integrity_check(self):
        """Проверка делается сразу: бэкап, выяснивший порчу при
        восстановлении, бэкапом не был."""
        self.run_backup()
        connection = sqlite3.connect(self.backup_dir / self.copies()[0])
        try:
            problem = connection.execute(
                'PRAGMA integrity_check').fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(problem, 'ok', 'копия повреждена сразу после снятия')

    def test_copy_contains_every_row_of_the_database(self):
        """Снимок средствами SQLite, а не копированием файла.

        Разница заметна, когда в базу пишут во время бэкапа — то есть
        ровно в тот момент, когда бэкап и нужен.
        """
        self.run_backup()
        source = sqlite3.connect(self.database)
        try:
            expected = source.execute(
                'SELECT id, subject_id, day FROM schedule_lesson '
                'ORDER BY id').fetchall()
        finally:
            source.close()
        self.assertEqual(len(expected), 3, 'источник для проверки пуст')
        self.assertEqual(self.rows_in(self.copies()[0]), expected,
                         'в копии строк нет или они другие')

    def test_reported_number_of_lessons_matches_the_database(self):
        """Число в сообщении — не украшение: по нему сверяют бэкап."""
        code, out, err = self.run_backup()
        self.assertEqual(code, 0, err)
        self.assertIn('Занятий в копии: 3', out)

    def test_second_backup_in_the_same_second_keeps_both(self):
        """Кнопку можно нажать дважды быстрее, чем тикает секунда."""
        self.run_backup()
        self.run_backup()
        self.assertEqual(len(self.copies()), 2,
                         'вторая копия затёрла первую')

    def test_old_copies_are_dropped_but_the_previous_one_stays(self):
        """Одна предыдущая копия остаётся всегда.

        Если сбой заметили только сейчас, вчерашняя ещё может
        пригодиться. Копия за прошлый месяц — уже повод для
        беспокойства, но не для хранения.
        """
        self.run_backup()
        older = self.backup_dir / 'db_20000101_000000.sqlite3'
        newest_expired = self.backup_dir / 'db_20000102_000000.sqlite3'
        for path in (older, newest_expired):
            path.write_bytes(b'')
            stamp = (
                datetime.datetime.now() - datetime.timedelta(days=45)
            ).timestamp()
            os.utime(path, (stamp, stamp))

        self.run_backup()

        names = self.copies()
        self.assertNotIn(older.name, names,
                         'просроченная копия осталась лежать вечно')
        self.assertIn(newest_expired.name, names,
                      'убраны все старые копии, включая предыдущую')

    def test_fresh_copies_are_never_touched(self):
        """Свежие копии не подпадают под чистку — срок ещё не вышел."""
        self.run_backup()
        self.run_backup()
        self.run_backup()
        self.assertEqual(len(self.copies()), 3)

    def test_missing_database_is_reported_not_crashed(self):
        """Нет базы — это не авария: свежий проект её ещё не создавал."""
        os.environ['TIMETABLE_DB'] = str(self.work / 'нет_такой.sqlite3')

        code, out, err = self.run_backup()

        self.assertEqual(code, 1)
        self.assertIn('Нет файла базы', err)
        self.assertEqual(self.copies(), [],
                         'создана копия от несуществующей базы')

    def test_backups_do_not_leave_the_machine(self):
        """Папка копий не должна попасть в репозиторий.

        В имени копии дата, в содержимом — расписание всего вуза: на
        GitHub такое не выкладывается даже среди прочего.
        """
        gitignore = (pathlib.Path(__file__).resolve().parent.parent
                     / '.gitignore').read_text(encoding='utf-8')
        self.assertIn('backups/', gitignore, 'папка копий не в .gitignore')
        self.assertIn('*.sqlite3', gitignore,
                      'копии базы под другими именами не запрещены')

    def test_database_file_itself_is_not_in_the_repository(self):
        """Одно слово в `.gitignore` надёжнее, чем память о нём."""
        import subprocess
        tracked = subprocess.run(
            ['git', 'ls-files', '*.sqlite3'],
            capture_output=True, text=True,
            cwd=pathlib.Path(__file__).resolve().parent.parent,
        )
        self.assertEqual(
            tracked.stdout.strip(), '',
            'в репозитории отслеживаются файлы базы: '
            f'{tracked.stdout.strip()}',
        )
