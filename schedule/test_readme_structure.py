"""Схема папок в памятке должна соответствовать реальной структуре.

Зачем. Название папки приложения (`schedule/`) отличается от названия проекта
(`timetable/`), и это сбивает с толку не в первый раз: пути в документации
выглядят как ссылки на чужой проект. Чтобы вопрос не возникал снова, в
памятке есть схема — и она обязана быть правдой.

Проверяется содержание, а не оформление: если завтра появится новый модуль,
тест потребует его упомянуть, а если файл переименуют — упадёт и укажет, что
схему надо поправить.

Схема живёт в `docs/HANDBOOK.md`, а не в README. README открывают чужие
люди на GitHub: там описание проекта, а не разбор устройства папок. Такая
разбивка проверяется отдельно — см. `ReadmeStaysShortTests`.
"""

import pathlib
import re
from pathlib import Path

from django.test import SimpleTestCase

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = ROOT / 'README.md'
HANDBOOK = ROOT / 'docs' / 'HANDBOOK.md'

# Символы отрисовки дерева в README. Отступ по ним определяет, в какой
# папке лежит файл: «├── config/» — папка верхнего уровня,
# «│   ├── models.py» — файл внутри неё.
_BRANCH_CHARS = '├└'
_DASHES = '─'
_VERTICAL = '│'
_ARROW = re.compile('[\u2190\u2192]')   # ← →


def readme_text():
    return HANDBOOK.read_text(encoding='utf-8')


def _parse_tree(section):
    """Пути файлов из схемы дерева папок.

    Разбор идёт по отступам: сколько вертикальных черт до имени — столько
    уровней. У «OFFICER.md» в корне и у «models.py» внутри папки вид с
    префиксом одинаковый, и различает их только глубина. Родителем файла
    считается папка, встреченная на уровень выше.

    Модуль без библиотек и без регулярных выражений по всему тексту: иначе
    «OFFICER.md» приклеивается к папке, увиденной строчкой выше, — такое
    уже случалось.
    """
    folders_by_depth = {}
    files = []

    for raw in section.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue

        # Позиция первого символа ветки: до неё только отступ.
        marker_pos = min(
            (line.find(ch) for ch in _BRANCH_CHARS if ch in line),
            default=-1,
        )
        if marker_pos < 0:
            continue

        depth = line[:marker_pos].count(_VERTICAL)

        # Всё после маркера: имя и, возможно, комментарий со стрелкой.
        body = _ARROW.split(line[marker_pos:])[0]
        # Символы ветки и черточки в начале имени — не часть имени.
        body = body.lstrip().lstrip(_BRANCH_CHARS).lstrip(_DASHES).strip()
        if not body:
            continue

        # Папка: имя оканчивается на «/» или состоит только из сегментов пути.
        if body.endswith('/') or re.fullmatch(r'[\w]+(/[\w]+)+/?', body):
            folders_by_depth[depth] = body.rstrip('/').split('/')[0]
            continue

        named = re.match(r'([\w]+\.(?:py|sqlite3|sh|md))', body)
        if not named:
            continue

        parents = [d for d in folders_by_depth if d < depth]
        parent = folders_by_depth[max(parents)] if parents else None
        files.append(f'{parent}/{named.group(1)}' if parent else named.group(1))

    return files


def structure_section():
    """Блок схемы папок из README."""
    text = readme_text()
    if '## Структура папок' not in text:
        return None
    section = text.split('## Структура папок')[1]
    fence = '`' * 3
    if fence not in section:
        return None
    return section.split(fence)[1]


class ReadmeStructureTests(SimpleTestCase):
    """Схема папок в документации соответствует диску."""

    def setUp(self):
        self.section = structure_section()

    def test_structure_section_exists(self):
        self.assertIsNotNone(
            self.section,
            'в README нет раздела «Структура папок» — структуру проекта '
            'приходится выяснять, открывая папки',
        )

    def test_every_folder_in_diagram_exists(self):
        """Каждая папка в схеме — настоящая папка проекта.

        Проверяются папки верхнего уровня: именно их человек ищет на диске.
        Вложенные (``templates/schedule/``, ``static/schedule/``) отдельно
        не проверяются — их существование уже следует из существования
        родителя, а «static/» сам по себе под папкой не лежит и не должен.
        """
        section = self.section
        top_level = set()

        for raw in section.splitlines():
            line = raw.rstrip()
            marker_pos = min(
                (line.find(ch) for ch in _BRANCH_CHARS if ch in line),
                default=-1,
            )
            if marker_pos < 0:
                continue
            # Верхний уровень — без вертикальной черты в отступе.
            if _VERTICAL in line[:marker_pos]:
                continue

            body = _ARROW.split(line[marker_pos:])[0]
            body = body.lstrip().lstrip(_BRANCH_CHARS).lstrip(_DASHES).strip()
            name = body.rstrip('/').split('/')[0]
            if name and not re.match(r'[\w]+\.(?:py|sqlite3|sh|md)', name):
                top_level.add(name)

        top_level.discard('timetable')   # это сам корень проекта
        self.assertTrue(top_level, 'в схеме не нашлось ни одной папки')
        for folder in sorted(top_level):
            self.assertTrue(
                (ROOT / folder).is_dir(),
                f'в схеме есть папка {folder}/, которой нет на диске',
            )

    def test_every_file_in_diagram_exists(self):
        """Каждый файл в схеме существует по указанному пути.

        Разбор идёт по отступам: глубина дерева считается по символам
        «├──» и «└──» до имени, и по этой глубине выбирается родитель из
        уже встреченных папок. Так «OFFICER.md» в корне не приклеивается
        к случайной папке, увиденной строчкой выше, а ``models.py``
        внутри ``schedule/`` ищется именно там.
        """
        tree = _parse_tree(self.section)
        self.assertGreaterEqual(
            len(tree), 8, 'в схеме слишком мало файлов — она не разбирается'
        )
        for rel in tree:
            self.assertTrue(
                (ROOT / rel).exists(),
                f'в схеме указан {rel}, а такого файла нет',
            )

    def test_diagram_places_key_files_in_the_right_folder(self):
        """Логика в schedule/, настройки в config/, скрипты в tools/.

        Проверяется не просто наличие файла, а его папка. Именно здесь
        человек и путается: `schedule/upload_guard.py` выглядит как файл
        чужого проекта, пока не проверишь, что папка лежит внутри timetable.
        """
        tree = _parse_tree(self.section)
        expected = {
            'manage.py': None,
            'db.sqlite3': None,
            'OFFICER.md': None,
            'schedule/models.py': 'schedule',
            'schedule/views.py': 'schedule',
            'schedule/exports.py': 'schedule',
            'config/settings.py': 'config',
            'tools/watchdog.py': 'tools',
        }
        for rel, folder in expected.items():
            self.assertIn(rel, tree, f'{rel} не указан в схеме')
            if folder is not None:
                self.assertEqual(
                    Path(rel).parent.as_posix(), folder,
                    f'{rel} должен лежать в папке {folder}/',
                )

    def test_mentions_that_schedule_is_a_subfolder(self):
        """Главная путаница названа словами, а не оставлена на догадку."""
        text = readme_text()
        section = text.split('## Структура папок')[1]
        self.assertIn('schedule', section)
        # Прямо сказано, что отдельного проекта с таким именем нет.
        self.assertIn('подпапк', section.lower())
        self.assertIn('отдельного проекта', section.lower())

    def test_says_where_to_look_for_each_kind_of_change(self):
        """Должно быть видно, что менять в какой папке."""
        text = readme_text()
        section = text.split('## Структура папок')[1].lower()
        self.assertIn('где что искать', section)
        self.assertIn('schedule/', section)
        self.assertIn('config/', section)
        self.assertIn('tools/', section)

    def test_root_files_are_documented(self):
        """Точки входа, база и инструкция — на верхнем уровне."""
        text = readme_text()
        section = text.split('## Структура папок')[1]
        for name in ('manage.py', 'db.sqlite3', 'OFFICER.md'):
            self.assertIn(name, section, f'{name} не упомянут в схеме')


class ReadmePathsResolveTests(SimpleTestCase):
    """Пути в бэктиках README ведут в существующие файлы."""

    def setUp(self):
        self.text = readme_text()

    def test_backticked_paths_exist(self):
        paths = sorted(set(re.findall(
            r'`((?:schedule|config|tools)/[A-Za-z0-9_./]+)`', self.text,
        )))
        self.assertGreater(len(paths), 10, 'подозрительно мало путей в README')
        for rel in paths:
            self.assertTrue(
                (ROOT / rel).exists(),
                f'README ссылается на {rel}, а такого файла нет',
            )

    def test_no_reference_to_renamed_or_absent_module(self):
        """Удалённый модуль не должен упоминаться как существующий."""
        for rel in re.findall(
            r'`(schedule/[A-Za-z0-9_./]+\.py)`', self.text,
        ):
            self.assertTrue(
                (ROOT / rel).exists(),
                f'в README есть ссылка на несуществующий модуль {rel}',
            )


class ReadmeStaysShortTests(SimpleTestCase):
    """README открывают чужие люди на GitHub — он не должен разрастаться.

    Так уже случилось: README вырос до тысячи строк и стал памяткой для
    автора. Подробности переехали в `docs/HANDBOOK.md`, и это закреплено
    проверкой.

    Планка — 140 строк. Три раза README уезжал в памятку: на тысячу строк,
    потом на 335 «как у разработчиков». Подробности по коду живут в
    `docs/HANDBOOK.md`, инструкция сотруднице — в `OFFICER.md`, и здесь
    остаётся только то, что человек видит за минуту: что это, что умеет и
    как запустить.
    """

    MAX_LINES = 140

    def setUp(self):
        self.text = README.read_text(encoding='utf-8')
        self.lines = self.text.splitlines()

    def test_readme_is_not_a_handbook(self):
        self.assertLessEqual(
            len(self.lines), self.MAX_LINES,
            f'README вырос до {len(self.lines)} строк. Подробности — в '
            f'docs/HANDBOOK.md, здесь описание проекта для посторонних.',
        )

    def test_readme_explains_what_the_project_is(self):
        """Человек с GitHub должен понять, что это, за 30 секунд."""
        lowered = self.text.lower()
        for needle in ('django', 'sqlite', 'возможности', 'запуск'):
            self.assertIn(needle, lowered, f'в README нет слова «{needle}»')

    def test_readme_has_the_usual_sections(self):
        """Минимальный набор: без этих разделов нечего открывать."""
        for heading in ('## Что это', '## Возможности', '## Скриншоты',
                        '## Стек', '## Запуск', '## Документация'):
            self.assertIn(heading, self.text, f'в README нет раздела {heading}')

    def test_readme_has_no_table_of_contents(self):
        """Оглавление нужно, когда страниц много. Здесь их шесть.

        Оно занимает столько же места, сколько сами разделы, и со временем
        перестаёт совпадать с содержанием.
        """
        self.assertNotIn('## Оглавление', self.text,
                         'оглавление в коротком README лишнее')

    def test_readme_points_at_the_handbook(self):
        """Отсылка нужна, иначе подробности никто не найдёт."""
        self.assertIn('docs/HANDBOOK.md', self.text,
                      'в README нет ссылки на памятку по коду')

    def test_badge_points_at_the_workflow_that_exists(self):
        """Бейдж проверок должен вести на реальный файл с проверками.

        Бейдж на несуществующий workflow показывает «no status» и выглядит
        как сломанная ссылка, а не как проверка.
        """
        import re as _re

        badges = _re.findall(r'actions/workflows/([A-Za-z0-9_.-]+)/badge\.svg',
                             self.text)
        self.assertTrue(badges, 'в README нет бейджа проверок')
        for name in badges:
            self.assertTrue(
                (ROOT / '.github' / 'workflows' / name).exists(),
                f'бейдж ссылается на несуществующий workflow {name}',
            )

    def test_handbook_exists_and_is_the_long_one(self):
        self.assertTrue(HANDBOOK.exists(), 'нет памятки docs/HANDBOOK.md')
        handbook_lines = len(HANDBOOK.read_text(encoding='utf-8').splitlines())
        self.assertGreater(
            handbook_lines, len(self.lines),
            'памятка должна быть подробнее README, а не наоборот',
        )