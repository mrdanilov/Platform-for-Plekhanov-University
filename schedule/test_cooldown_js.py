"""Пауза на кнопке Excel проверяется дважды.

`ExportCooldownButtonTests` в test_export_protection.py смотрит на разметку:
есть ли `data-cooldown`, переведена ли подпись секунд, объявлены ли стили.
Этого мало — такая проверка не заметит, что отсчёт не тикает или что пауза
слетает при перезагрузке страницы.

Поэтому сам скрипт ещё и исполняется: `tests_js/cooldown.js` вырезает его из
шаблона index.html и гоняет против подменённых document/localStorage/Date с
перемоткой времени. Тест пропускается, если node не установлен — Django от
node не зависит, и требовать его ради одного теста незачем.

Запуск вручную:  node schedule/tests_js/cooldown.js
"""

import shutil
import subprocess
import unittest
from pathlib import Path

HARNESS = Path(__file__).parent / 'tests_js' / 'cooldown.js'
NODE = shutil.which('node')


@unittest.skipIf(NODE is None, 'node не установлен — скрипт паузы не проверяется')
class CooldownScriptTests(unittest.TestCase):
    """Исполнение скрипта паузы, а не поиск строк в шаблоне."""

    def test_cooldown_logic(self):
        result = subprocess.run(
            [NODE, str(HARNESS)],
            capture_output=True,
            text=True,
            encoding='utf-8',
            cwd=str(Path(__file__).parent),
        )
        self.assertEqual(
            result.returncode, 0,
            'логика паузы не сработала:\n'
            f'{result.stdout}\n{result.stderr}'.strip(),
        )
        self.assertNotIn('FAIL', result.stdout)
