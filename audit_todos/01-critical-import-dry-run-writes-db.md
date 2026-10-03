# 01 · 🔴 «Проверить файл» реально пишет в БД

**Severity:** Critical — тихая порча данных + обманчивый аудит-трейл
**Статус:** ⬜ не начато · **Оценка:** ~30 мин (фикс + тест)
**Зависит от:** ничего · **Блокирует:** `02`, `06`, `09`, `10`

## Где

- `schedule/excel.py:344` — `def import_grid(data, filename='')`: параметра `dry_run` нет вообще
- `schedule/excel.py:461-477` — `import_workbook(data, filename, dry_run=False)`
- `schedule/excel.py:468` — `grid_result = import_grid(data, filename)` — флаг **не передаётся**
- `schedule/views.py:668` — `result = import_workbook(data, filename, dry_run=not apply_now)`
- `schedule/excel.py:376` — `transaction.atomic()` вокруг записи

## Что не так

`import_workbook(data, filename, dry_run=True)` вызывает `import_grid(data, filename)`, а тот
под `transaction.atomic()` создаёт `LessonTime`, `Group`, `Subject`, `Teacher`, `Room`, `Lesson`
и коммитит. Двухшаговость импорта («Проверить файл» → «Применить импорт») работает **только**
для плоского CSV-формата (`importer.import_schedule` проверяет `dry_run` на строке 263).

Дополнительно на этом же шаге срабатывает `_ensure_lesson_times` (`excel.py:309`) — время пар
создаётся мимо админских прав и без записи в журнал.

## Доказательство (реальный HTTP POST от staff, тестовая БД)

```
DB before upload: Lesson=0 Group=0 Subject=0 Teacher=0
POST /import/ {'check':'1', 'file': grid.xlsx}  -> 200
UI:  «Будет создано: 6, обновлено: 0, пропущено: 0.
      Нажмите "Применить импорт", чтобы записать.»
DB after "Проверить файл": Lesson=6 Group=5 Subject=6 Teacher=5 Room=1
ImportLog rows: 0
```

Три последствия одновременно:
1. Пользователю показывают «проверку», данные уже применены.
2. `ImportLog` не пишется — **у аудит-трейла нет следов изменения данных**.
3. Кнопка «Применить импорт» остаётся активной, второй клик запускает импорт повторно.

## Направление фикса

1. Добавить `dry_run=False` в сигнатуру `import_grid`.
2. Внутри `import_grid`, **до** `transaction.atomic()`, сделать так же, как в
   `import_schedule`: в режиме проверки только посчитать, что будет создано/обновлено,
   ничего не записывая (включая `_ensure_lesson_times`).
3. В `import_workbook` передавать `dry_run=dry_run` в **оба** вызова (`import_grid` и
   `import_schedule`) — сейчас флаг доходит только до второго.
4. Убедиться, что `_ensure_lesson_times` в режиме проверки не создаёт `LessonTime`.

## Критерии приёмки

- [ ] `POST /import/` с `check=1` и grid-файлом не меняет ни одного счётчика
      (`Lesson`, `Group`, `Subject`, `Teacher`, `Room`, `LessonTime`).
- [ ] Тот же POST показывает в UI корректные «будет создано/обновлено/пропущено».
- [ ] `POST /import/` с `apply=1` по-прежнему пишет данные и создаёт запись в `ImportLog`.
- [ ] CSV-формат не изменил поведение (регрессия не ломается).
- [ ] Спрятанное предупреждение README про «ничего не пишет» снова соответствует коду.

## Тест, который надо добавить (его отсутствие и скрыло баг)

`schedule/test_excel.py` — рядом с существующими тестами `import_grid`:

```python
def test_dry_run_does_not_write(self):
    # загрузить grid-книгу, вызвать import_workbook(..., dry_run=True)
    # assertEqual(Lesson.objects.count(), 0) и то же для Group/Subject/Teacher/Room/LessonTime
```

Плюс HTTP-уровень в `test_access.py`-стиле: staff жмёт «Проверить файл» → БД пуста.
Проверять именно через `import_workbook`, а не `import_grid` — именно на границе
теряется флаг.

## Проверка

```bash
python manage.py test schedule
python manage.py check --deploy
# ручная проверка в UI: /import/ → «Проверить файл» → /admin/schedule/lesson/ должен быть пуст
```

## Откат

Откатывается ревертом файлов `schedule/excel.py` и `schedule/views.py` (после `04`).
Отдельной миграции нет — фикс меняет только логику, схема БД не трогается.
Если фикс попал на прод с данными, «откатить» в строгом смысле нельзя: нужен
бэкап БД до импорта (см. задачу `19`).