# 13 · 🟡 Дубли `_parse_week`/`grid.parse_week` и мёртвый код в `grid.py`

**Severity:** Medium — два источника истины на одну логику расписания
**Статус:** ⬜ не начато · **Оценка:** ~20 мин
**Зависит от:** ничего

## Где

`schedule/views.py:53-75`:

```python
def _monday_of(d): ...
def _iso_week(monday): ...
def _parse_week(value): ...
```

`schedule/grid.py:10-38`:

```python
def monday_of(d): ...
def iso_week(d): ...
def parse_week(value): ...
def week_param(week_monday): ...
VIEWS = ('group', 'pair', 'teacher', 'room')     # grid.py:7
```

Проверено grep'ом: `grid.parse_week`, `grid.iso_week`, `grid.monday_of`, `grid.week_param`,
`grid.VIEWS` **не вызываются ниоткуда**. `views.py` импортирует из `grid` только
`build_grid` и `grid_has_content` (`views.py:22`).

## Что не так

Одна и та же логика (разбор недели из параметра запроса, ISO-номер недели, понедельник
недели) реализована дважды. `views` использует свои приватные копии, `grid` содержит
неиспользуемые близнецы. Расхождение при будущей правке гарантировано: например,
`_parse_week` в `views` уже отличается от `parse_week` в `grid` по именам аргументов.

## Направление фикса

1. Выбрать один источник истины. Рекомендую оставить реализацию в `grid.py` (это модуль
   про расписание), переименовать в публичные `monday_of`/`iso_week`/`parse_week`/
   `week_param`.
2. Удалить приватные копии из `views.py`, заменив использования на импорт из `grid`.
3. Удалить неиспользуемое: `grid.VIEWS`, `grid.week_param` (или использовать
   `week_param` в вьюхах вместо ручного `_iso_week(...)` — это и есть его назначение).
4. `VIEWS` в `views.py:41` остаётся единственным определением, если не переносится;
   иначе — наоборот, перенести в одно место и импортировать.

## Критерии приёмки

- [ ] В `grid.py` и `views.py` нет двух реализаций одной функции.
- [ ] `VIEWS` определён в одном месте.
- [ ] Мёртвые функции удалены (проверка: `grep` по именам даёт только определение
      и реальные вызовы).
- [ ] `python manage.py test schedule` проходит без правок тестов
      (тесты используют `?week=YYYY-Www` — формат не меняется).

## Проверка

```bash
grep -rn "parse_week\|iso_week\|monday_of\|week_param\|VIEWS" schedule/ --include=*.py
python manage.py test schedule
```

## Откат

Реверт обоих файлов. Миграций нет.