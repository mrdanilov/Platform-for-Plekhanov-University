# 18 · 🟡 Импорты внутри функций и XLSX-экспорт внутри `views.py`

**Severity:** Medium — затрудняет чтение и тестируемость, рецидивы при росте кода
**Статус:** ⬜ не начато · **Оценка:** ~1 час
**Зависит от:** `13` (почистить дубли перед переносом)

## Где

Импорты внутри функций:

- `schedule/grid.py:21, 51, 81, 174` — `from django.utils import timezone`, `from .i18n import translate`,
  `from .models import Substitution`, `timezone_today()`
- `schedule/excel.py:346` — `from .importer import ImportResult`
- `schedule/excel.py:463` — `from .importer import import_schedule`
- `schedule/excel.py:201` — `from openpyxl import load_workbook`, `from .upload_guard import guard_zip`
- `schedule/views.py:728-732` — `import io`, `from openpyxl import Workbook`, стили, утилиты

Крупная функция в вьюхах:

- `schedule/views.py:727-819` — `build_xlsx()`, ~90 строк формирования Excel через openpyxl.
  По смыслу это модуль `excel.py` (или новый `export.py`), а не представление.

## Что не так

Часть внутренних импортов действительно обходит цикл (`excel.py` ↔ `importer.py` —
они вызывают друг друга), но остальные не обходят ничего и просто размазывают
зависимости по телу функций. `build_xlsx` в `views.py` смешивает представление с
генератором файла, из-за чего `views.py` (825 строк) остаётся самым крупным модулем
и худшим местом для тестов.

## Направление фикса

1. Вынести `build_xlsx` в `schedule/export.py` (новый модуль) или в `excel.py`.
   Тогда `views.export_xlsx_view` становится тонкой вьюхой, а генератор файла
   тестируется без HTTP.
2. Оставить внутри функций **только** те импорты, которые действительно разрывают цикл
   (`excel` ↔ `importer`), и описать это комментарием. Остальные поднять наверх.
3. `grid.py` — поднять `timezone` и `translate` наверх, если после `13` цикла не будет.
4. Проверить, не остаётся ли после этого цикла: `python -c "import schedule.excel, schedule.importer"`.

## Критерии приёмки

- [ ] `build_xlsx` не в `views.py`; есть тест на генерацию файла без HTTP-клиента.
- [ ] Внутренние импорты остались только там, где разрывают цикл, и это отмечено комментарием.
- [ ] Все тесты проходят, поведение экспорта не изменилось (сравнить байты XLSX для
      одного и того же расписания до/после — структура должна совпасть).

## Проверка

```bash
python manage.py test schedule
python -c "
import os;os.environ['DJANGO_SETTINGS_MODULE']='config.settings'
import django;django.setup();import schedule.excel,schedule.importer  # no cycle crash"
```

## Откат

Реверт файлов; новый модуль `export.py` удаляется. Миграций нет.