# 03 · 🔴 Open redirect через `Referer` в `/lang/`

**Severity:** Critical — пользовательский заголовок попадает в `Location` без санитизации
**Статус:** ✅ готово · **Оценка:** ~15 мин (фикс + тест)
**Зависит от:** ничего · **Блокирует:** ничего

## Где

`schedule/views.py:305-324`:

```python
target = request.GET.get('next') or ''
if not target.startswith('/') or target.startswith('//'):
    target = request.META.get('HTTP_REFERER') or '/'
if not target.startswith('/'):     # ← «//evil.example» проходит эту проверку
    target = '/'
response = redirect(target)
```

Сам `next` проверяется корректно. Проблема в fallback на `HTTP_REFERER`: он
проверяется только на `startswith('/')`, а `//evil.example` этому удовлетворяет.

## Доказательство

```
GET /lang/?lang=uz  c Referer: //evil.example          -> 302 Location='//evil.example'
GET /lang/?lang=uz  c Referer: //evil.example/phish   -> 302 Location='//evil.example/phish'
GET /lang/?lang=uz  c Referer: https://evil.example   -> 302 Location='/'
```

Значение `//host` браузер трактует как protocol-relative URL и уводит на внешний сайт.

## Направление фикса

1. Заменить самописные `startswith` на канонический хелпер Django
   `django.utils.http.url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()})`.
   Он же покрывает схемы (`javascript:`, `https://evil`) — то, что текущая проверка
   ловит лишь частично.
2. Фallback на `Referer` прогнать через **ту же** валидацию; если невалиден — `'/'`.
3. Рассмотреть отказ от `Referer` вовсе: в этом приложении `next` всегда доступен
   из ссылки в шапке (`lang_url` подставляет `request.get_full_path()`), поэтому
   fallback не несёт пользы и только расширяет поверхность атаки.
4. Провалидировать и `lang`/`next` на длину (защита от абсурдных URL).

## Критерии приёмки

- [x] `Referer: //evil.example` → `Location` остаётся на сайте.
- [x] `Referer: https://evil.example` → `Location` остаётся на сайте.
- [x] `next=/today/` → редирект на `/today/` (позитивный случай не сломан).
- [x] `next=https://evil.example` → редирект на `/`.
- [x] Язык всё равно переключается при любом валидном `next`.

## Реализовано

- `schedule/views.py` — самописные `startswith` заменены на хелпер
  `_safe_return_url(value, request)`: он возвращает значение только если
  `django.utils.http.url_has_allowed_host_and_scheme(value, {request.get_host()})`.
  Проверка одна и та же для `next` и для `Referer`, поэтому «дыры» в двух
  ветках больше нет структурно. Значение `//` (пустой host) Django-хелпер
  считает допустимым — этот случай отсекается отдельно в `_safe_return_url`.
  Порядок сохранён: `next` → `Referer` → `'/'`.
  Бонус хелпера: `javascript:`, `///host`, `/\host` (Chrome считает `\` тем же,
  что `/`), ведущие управляющие символы и URL длиннее 2048 символов.
  `lang` по-прежнему проверяется членством в `LANGS`, длина `next` ограничена
  `MAX_URL_LENGTH` внутри хелпера.
- `schedule/test_header.py` — `test_language_switch_never_redirects_offsite`:
  таблица из 18 пар `(next, referer)`, для каждой проверяются код 302, смена
  cookie и что `Location` остаётся на сайте; `test_language_switch_keeps_valid_next_with_hostile_referer`:
  враждебный `Referer` не ломает валидный `next`.

Пункт 3 из «Направления фикса» (отказ от `Referer` вовсе) реализован частично:
fallback оставлен, потому что при `Referer` вида `https://сайт/today/`
пользователь возвращается на страницу, с которой пришёл. Внешние referer
отсекаются, referer нашего же хоста — это не редирект наружу.

## Тест

Кейсы добавлены в `schedule/test_header.py` (отдельный `test_open_redirect.py` не
заводил — вся логика переключателя языка уже тестируется там). Таблица
`(next, referer) -> ожидаемый Location` покрывает: `//evil.example`,
`/\evil.example`, `///evil.example`, `https://evil.example`, `javascript:alert(1)`,
`//`, пусто, валидный путь, валидный `next` при враждебном referer.

## Проверка

```bash
python manage.py test schedule
```

301 тест, 1 пропуск. Единственный фейл — `test_captcha.RenderTests.test_no_pillow_needed`
(`'PIL' unexpectedly found in sys.modules`): в системном Python установлен Pillow,
это свойство окружения, к правке отношения не имеет.

## Откат

Реверт `schedule/views.py`. Изолированная правка, риск минимальный.