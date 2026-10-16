"""requirements.txt должен совпадать с тем, что код действительно использует.

Так уже случилось: пакет `whitenoise` стоял на машине разработчика, но не
был записан в requirements.txt. Локально всё работало, а на сервере
проверки ставили зависимости по этому файлу, Django не мог собрать список
middleware — и все тесты падали на первом обращении к странице. Разница
между «у меня работает» и «у всех работает» была в одной строке списка.

Самое неприятное здесь то, что middleware и приложения в настройках
записаны строками, а не импортами. Поиск по `import` такой пакет не видит
вообще — он находится только разбором самих настроек.
"""

import pathlib
import re
import sys

from django.conf import settings
from django.test import SimpleTestCase

ROOT = pathlib.Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / 'requirements.txt'

# Пакеты, которые нужны не приложению, а отдельным вспомогательным
# скриптам. Они перечислены здесь явно, чтобы «нет в requirements.txt» не
# превращалось в тихую поломку на чужой машине.
OPTIONAL_TOOL_PACKAGES = {
    # tools/make_logo.py — разовый скрипт, собирает картинки из готовых.
    # Ставить Pillow ради него в требования незачем: скрипт не нужен ни для
    # работы приложения, ни для установки.
    'PIL',
}

# Свои пакеты и то, что не нужно объявлять зависимостью.
OWN_PACKAGES = {'schedule', 'config', 'timetable'}


def required_packages():
    """Имена пакетов из requirements.txt, без версий."""
    names = set()
    for line in REQUIREMENTS.read_text(encoding='utf-8').splitlines():
        line = line.split('#')[0].strip()
        if not line or line.startswith('-'):
            continue
        name = re.split(r'[=<>!~\[]', line)[0].strip().lower()
        if name:
            names.add(name)
    return names


def dotted_paths_in_settings():
    """Верхний уровень пакетов из MIDDLEWARE и INSTALLED_APPS."""
    found = set()
    for entry in list(settings.MIDDLEWARE) + list(settings.INSTALLED_APPS):
        if not isinstance(entry, str) or '.' not in entry:
            continue
        found.add(entry.split('.')[0])
    return found


def imported_top_level():
    """Верхний уровень пакетов, импортируемых кодом проекта."""
    found = set()
    pattern = re.compile(r'^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)', re.M)
    for folder in ('config', 'schedule', 'tools'):
        for path in (ROOT / folder).rglob('*.py'):
            text = path.read_text(encoding='utf-8')
            for name in pattern.findall(text):
                found.add(name)
    return found


def invoked_by_command():
    """Пакеты, которые запускаются командой, а не импортируются.

    gunicorn в коде не встречается ни разу: его имя стоит в ExecStart
    службы systemd. Без этого поиска пакет выглядел бы неиспользуемым, и
    его удалили бы из требований — а сервер после этого перестал бы
    запускаться.
    """
    used = set()
    if not (ROOT / 'deploy').is_dir():
        return used
    for path in (ROOT / 'deploy').rglob('*'):
        if not path.is_file() or path.suffix not in ('.service', '.conf', '.md',
                                                     '.sh'):
            continue
        text = path.read_text(encoding='utf-8')
        for name in required_packages():
            if re.search(rf'\b{re.escape(name)}\b', text):
                used.add(name)
    return used


class RequirementsCoverageTests(SimpleTestCase):
    """Каждый сторонний пакет, который нужен коду, объявлен в requirements."""

    def setUp(self):
        self.required = required_packages()

    def _third_party(self, names):
        return {
            name for name in names
            if name not in sys.stdlib_module_names
            and name not in OWN_PACKAGES
            and name not in OPTIONAL_TOOL_PACKAGES
            and name != 'django'
        }

    def test_middleware_packages_are_listed(self):
        """Главный случай: пакет из MIDDLEWARE не должен теряться.

        `whitenoise.middleware.WhiteNoiseMiddleware` написан строкой, поэтому
        ни один поиск по импортам его не найдёт. Только разбор настроек.
        """
        missing = self._third_party(dotted_paths_in_settings()) - self.required
        self.assertFalse(
            missing,
            'эти пакеты используются в настройках, но их нет в '
            f'requirements.txt: {sorted(missing)}. На машине разработчика они '
            'могут стоять, а на сервере сборка middleware упадёт.',
        )

    def test_imported_packages_are_listed(self):
        missing = self._third_party(imported_top_level()) - self.required
        self.assertFalse(
            missing,
            f'код импортирует пакеты, которых нет в requirements.txt: '
            f'{sorted(missing)}',
        )

    def test_requirements_has_no_extras(self):
        """В списке не должно быть того, что код не использует.

        Лишняя зависимость — это лишняя точка отказа при установке и повод
        для вопроса «а оно вообще нужно?».
        """
        used = (self._third_party(dotted_paths_in_settings())
                | self._third_party(imported_top_level())
                | invoked_by_command())
        extra = {name for name in self.required if name != 'django'} - used
        self.assertFalse(
            extra, f'в requirements.txt есть неиспользуемые пакеты: {sorted(extra)}',
        )

    def test_versions_are_pinned(self):
        """Версия зафиксирована — иначе однажды приедет несовместимая."""
        unpinned = [
            line for line in REQUIREMENTS.read_text(encoding='utf-8').splitlines()
            if line.strip() and not line.strip().startswith('#')
            and '==' not in line
        ]
        self.assertFalse(
            unpinned,
            f'версия не зафиксирована: {unpinned}',
        )
