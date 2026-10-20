"""`Platform.bat` — с чего человек начинает работу, и самая хрупкая часть.

Пакетный файл не читает Python: его разбирает cmd, построчно, в кодовой
странице консоли, и понимает он только свои правила. Из-за этого здесь
проверяются не «тексты совпадают с обещанными», а вещи, из-за которых
скрипт молча перестаёт работать:

* перевод строк CRLF. С LF файл рассыпается на куски: cmd режет строки
  с кириллицей посреди слова и отдаёт обрывки обратно как команды —
  «'Готово' is not recognized». Сломалось один раз и было исправлено,
  поэтому закреплено проверкой.
* отсутствие BOM: три служебных байта в начале превращают `@echo off`
  в неизвестную команду.
* смена кодировки `chcp 65001` стоит до первой русской строки, иначе
  меню выводится кракозябрами.
* на каждый `goto` найдётся своя метка: опечатка в имени метки даёт
  либо «label not found», либо падение в чужую ветку.
* файлы, к которым скрипт обращается, существуют.

Отдельно проверяется, что правило про CRLF записано в `.gitattributes`:
иначе файл останется аккуратным у автора и рассыплется у того, кто
клонирует проект на другую машину с другими настройками git.
"""

import pathlib
import re

from django.test import SimpleTestCase

ROOT = pathlib.Path(__file__).resolve().parent.parent
BATCH = ROOT / 'Platform.bat'


def batch_text():
    """Текст скрипта; читается байтами, чтобы посмотреть кодировку."""
    return BATCH.read_bytes().decode('utf-8')


class PlatformBatFileFormatTests(SimpleTestCase):
    """Формат файла: то, что ломает cmd раньше, чем дойдёт до логики."""

    def setUp(self):
        self.raw = BATCH.read_bytes()
        self.text = self.raw.decode('utf-8')

    def test_script_exists(self):
        self.assertTrue(
            BATCH.exists(),
            'нет Platform.bat — на Windows запуск платформы описан только '
            'в памятке, и до него доходят не все',
        )

    def test_line_endings_are_crlf(self):
        """Одна из причин рассыпания: файл в одних LF.

        cmd читает строку до символа перевода, а в LF-файле поверх
        многобайтовой кириллицы теряет счёт байт и обрывает команду
        посреди слова.
        """
        bare_lf = self.raw.count(b'\n') - self.raw.count(b'\r\n')
        self.assertEqual(
            bare_lf, 0,
            f'в Platform.bat {bare_lf} строк оканчивается одним LF — '
            'Windows читает такой файл по кускам и режет кириллицу; '
            'нужен CRLF',
        )

    def test_no_byte_order_mark(self):
        self.assertFalse(
            self.raw.startswith(b'\xef\xbb\xbf'),
            'BOM в начале файла: cmd воспринимает его как часть первой '
            'команды и не выполняет «@echo off»',
        )

    def test_no_foreign_characters_before_the_codepage_switch(self):
        """Всё, что стоит до переключения кодировки, обязано быть ASCII.

        Проверяется не просто порядок строк, а именно отсутствие
        не-ASCII символов до `chcp`: одна русская буква в комментарии
        сверху — и cmd, читающий файл в кодовой странице консоли, может
        обрвать следующую команду посреди слова. Ровно это и случалось.
        """
        switch = self.text.lower().find('chcp 65001')
        self.assertNotEqual(switch, -1,
                            'скрипт не переключает кодовую страницу')
        prefix = self.text[:switch]
        self.assertTrue(
            prefix.isascii(),
            'до переключения кодовой страницы есть не-ASCII символы: '
            'cmd прочитает их в кодовой странице консоли',
        )

    def test_crlf_rule_is_versioned(self):
        """Правило должно ехать вместе с репозиторием, а не с машиной."""
        attributes = ROOT / '.gitattributes'
        self.assertTrue(attributes.exists(),
                        'нет .gitattributes — правило про CRLF не сохранится')
        self.assertIn(
            '*.bat text eol=crlf', attributes.read_text(encoding='utf-8'),
            'в .gitattributes нет правила CRLF для пакетных файлов',
        )


class PlatformBatWiringTests(SimpleTestCase):
    """Связи внутри скрипта: метки, файлы, пункты меню."""

    def setUp(self):
        self.text = batch_text()

    def test_every_goto_finds_its_label(self):
        labels = set(re.findall(r'(?m)^:([A-Za-z_]\w*)\s*$', self.text))
        jumps = {
            name.lower() for name in re.findall(
                r'\bgoto\s+:?([A-Za-z_]\w*)', self.text, re.IGNORECASE)
        }
        calls = {
            name.lower() for name in re.findall(
                r'\bcall\s+:([A-Za-z_]\w*)', self.text, re.IGNORECASE)
        }
        known = {name.lower() for name in labels}
        self.assertTrue(labels, 'в скрипте не нашлось ни одной метки')
        missing = (jumps | calls) - known
        self.assertFalse(
            missing,
            f'goto ведёт на несуществующую метку: {sorted(missing)} — '
            'так скрипт падает с «label not found»',
        )

    def test_every_label_is_used(self):
        """Мёртвая метка — обычно метка после переименования."""
        labels = {
            name.lower() for name in re.findall(
                r'(?m)^:([A-Za-z_]\w*)\s*$', self.text)
        }
        used = {
            name.lower() for name in re.findall(
                r'\b(?:goto|call)\s+:?([A-Za-z_]\w*)', self.text,
                re.IGNORECASE)
        }
        dead = labels - used
        self.assertFalse(dead, f'метка никем не используется: {sorted(dead)}')

    def test_menu_numbers_map_to_a_branch(self):
        """Каждый пункт меню уводит в свою ветку, а не в пустоту.

        Проверяется самая частая поломка меню: `choice` принимает ответ,
        а дальше стоит неверное число `if errorlevel` — и выбор молча
        ведёт на соседний пункт.
        """
        # Берётся только блок меню: проверки `if errorlevel` после каждого
        # служебного шага — не пункты выбора и счётчиком бы мешали.
        menu = self.text.split(':menu', 1)[1].split(':dispatch', 1)[0]
        options = re.search(r'choice\s+/c\s+(\S+)', menu)
        self.assertIsNotNone(options, 'нет команды choice — меню не считывает выбор')
        branches = re.findall(r'if errorlevel (\d+) goto (\w+)', menu)
        self.assertEqual(
            len(options.group(1)), len(branches),
            'число вариантов в choice не совпадает с числом веток — '
            'какой-то пункт меню недостижим',
        )
        # Проверка идёт сверху вниз: `if errorlevel` означает «не меньше»,
        # поэтому условия обязаны идти по убыванию.
        levels = [int(number) for number, _ in branches]
        self.assertEqual(levels, sorted(levels, reverse=True),
                         'ветки if errorlevel стоят не по убыванию — '
                         'часть пунктов меню сработает не туда')

    def test_documented_commands_match_the_dispatch(self):
        """Команды из шапки — те же, что понимает скрипт.

        Иначе человек набирает `Platform.bat serve`, копируя пример из
        пояснения, и получает «неизвестная команда».
        """
        dispatch = set(re.findall(
            r'if /i "%~1"=="(\w+)"', self.text))
        self.assertTrue(dispatch, 'в скрипте нет разбора аргументов')
        documented = set(re.findall(r'Platform\.bat (\w+)', self.text))
        self.assertEqual(
            dispatch, documented,
            f'в шапке описано {sorted(documented)}, скрипт понимает '
            f'{sorted(dispatch)}',
        )

    def test_referenced_files_exist(self):
        """Скрипт вызывает чужие файлы — они обязаны быть в проекте."""
        referenced = sorted(set(re.findall(
            r'(tools[\\/][\w.]+|requirements\.txt|manage\.py)', self.text)))
        self.assertGreater(len(referenced), 2,
                           'подозрительно мало внешних вызовов в скрипте')
        for relative in referenced:
            with self.subTest(path=relative):
                self.assertTrue(
                    (ROOT / relative.replace('\\', '/')).exists(),
                    f'Platform.bat обращается к {relative}, такого файла нет',
                )

    def test_server_is_checked_before_it_is_started(self):
        """Повторный запуск не должен падать с «порт занят»."""
        self.assertIn('call :server_up', self.text,
                      'скрипт не проверяет, не запущен ли уже сайт')
        self.assertIn('manage.py runserver 127.0.0.1:8000', self.text)
