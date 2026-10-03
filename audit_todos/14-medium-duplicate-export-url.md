# 14 · 🟡 Два URL-маршрута на одну вьюху экспорта

**Severity:** Medium — мёртвый путь + два имени у одного ресурса
**Статус:** ⬜ не начато · **О оценка:** ~5 мин
**Зависит от:** ничего

## Где

`schedule/urls.py:23-24`:

```python
path('export/timetable.xlsx', views.export_xlsx_view, name='export_xlsx'),
path('export/current.xlsx',   views.export_xlsx_view, name='export_xlsx_current'),
```

Подтверждено: `resolve('/export/timetable.xlsx').func is resolve('/export/current.xlsx').func`
→ `True`. Одна вьюха, два имени маршрута, два URL.

## Что не так

- Шаблон `_block.html:32` ссылается на `/export/current.xlsx` — значит
  `/export/timetable.xlsx` в приложении не используется.
- README:245-247 упоминает «Кнопка "Excel" в шапке блока» и отдельно
  `/export/timetable.csv` — про `.xlsx` URL не сказано ничего, из чего следует, что
  старый путь остался как «на всякий случай».
- Для CSV используется здоровая практика одно имя на ресурс (`export_csv_view` → `name='export'`),
  а здесь — исключение, которое со временем даст «работает только один из двух адресов».

## Направление фикса

1. Оставить один маршрут. Какой — вопрос вкуса: `export/timetable.xlsx` согласован
   с `export/timetable.csv` (парность имён) и выглядит предпочтительнее; тогда правится
   `_block.html:32`.
2. Удалить второй `name` (`export_xlsx_current`) и его usages в шаблонах/тестах.
3. Проверить, не используется ли старый путь во внешних ссылках/закладках
   (в README не упоминается — вероятно, можно удалять).
4. При желании — 301-редирект со старого адреса на новый, если путь был опубликован.

## Критерии приёмки

- [ ] На одну вьюху — один маршрут и один `name`.
- [ ] Кнопка «Excel» в блоке расписания работает.
- [ ] `reverse('schedule:export_xlsx')` резолвится, `reverse('schedule:export_xlsx_current')`
      больше не существует.
- [ ] `test_access.py` (использует `reverse('schedule:export_xlsx')`) проходит.

## Проверка

```bash
python manage.py test schedule
python -c "
import django,os;os.environ['DJANGO_SETTINGS_MODULE']='config.settings';django.setup()
from django.urls import resolve,reverse
print(resolve('/export/timetable.xlsx').url_name, resolve('/export/current.xlsx').url_name)"
```

## Откат

Вернуть строку в `urls.py` и ссылку в `_block.html`. Миграций нет.