# 10 · 🟠 Гонка «прочитал — потом создал» в обоих импортёрах

**Severity:** High — 500 вместо внятной ошибки + откат всего импорта целиком
**Статус:** ⬜ не начато · **Оценка:** ~40 мин
**Зависит от:** `09` (нормализованные ключи — иначе `get_or_create` не поможет)

## Где

`schedule/importer.py:324-348`:

```python
existing = Lesson.objects.filter(group=group, day=day, lesson_time=lesson_time,
                                 week_parity=parity).select_related(...).first()
...
else:
    Lesson.objects.create(...)   # ← между filter и create есть окно
```

`schedule/excel.py:418-432` — тот же паттерн (дважды: для `group` и для `group__isnull`).
`IntegrityError` не перехватывается ни там, ни там.

## Что не так

Нет ни `get_or_create`/`update_or_create`, ни `select_for_update`. Единственный барьер —
partial unique `uniq_lesson_slot`, который при гонке выстреливает `IntegrityError`.

## Почему серьёзно

Два сотрудника одновременно жмут «Применить импорт» — штатная ситуация в начале
учебного года. Один получает 500 вместо «занято», и, что важнее, из-за
`transaction.atomic()` **откатывается весь его импорт**, включая уже корректно
разобранные строки. На SQLite это воспроизводится и без двух людей
(см. `05`: `database table is locked`).

## Направление фикса

1. Заменить `filter().first()` + `create()` на `update_or_create()` по ключу слота
   (`group`/`raw_text`, `day`, `lesson_time`, `week_parity`) внутри
   `transaction.atomic()` — обе точки: `importer.py` и `excel.py`.
2. `IntegrityError` ловить и превращать в `result.errors` с номером строки файла,
   а не в 500. Требовать `transaction.atomic()` на весь импорт, чтобы откат был
   предсказуемым.
3. Опционально: сериализовать запись на уровне приложения (lock-файл в `tmp_import`),
   пока решение по `05` не принято.
4. Учесть, что для `group=None` уникального ограничения **нет** (partial condition
   `group__isnull=False`) — там нужен отдельный ключ идемпотентности, например по
   `raw_text`.

## Критерии приёмки

- [ ] Два параллельных `apply` не дают 500; оба получают предсказуемый результат.
- [ ] При конфликте слота в `result.errors` появляется номер строки файла.
- [ ] Идемпотентность: повторный импорт того же файла не создаёт дублей
      (уже заявлено в README — стоит закрепить тестом, см. ниже).
- [ ] `Lesson.objects.create(group=None, ...)` идемпотентен по `raw_text`.

## Тест

```python
# ThreadPoolExecutor, 4 потока на одном файле → нет исключений,
# количество Lesson совпадает с ожидаемым, ошибок нет.
# На SQLite может потребоваться OPTIONS timeout — тогда тест помечается skipUnless.
```

Плюс тест идемпотентности: два последовательных импорта одного файла → количество
`Lesson` не меняется второго раза.

## Проверка

```bash
python manage.py test schedule
```

## Откат

Реверт `importer.py`/`excel.py`. Миграций нет.