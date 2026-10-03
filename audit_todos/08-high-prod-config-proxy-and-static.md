# 08 · 🟠 Прод-конфиг: редирект-цикл за nginx и неотдаваемая статика

**Severity:** High — с текущим конфигом сайт за reverse-proxy не работает
**Статус:** ⬜ не начато · **Оценка:** ~15 мин (две строки в settings + README)
**Зависит от:** ничего · **Блокирует:** `05` (B), `07`

## Где

`config/settings.py:43-58, 185`:

```python
DEBUG = os.environ.get('DJANGO_DEBUG', '1') == '1'
SECURE_SSL_REDIRECT = not DEBUG        # строка 53
STATIC_URL = 'static/'                 # строка 185 — STATIC_ROOT нет
```

Не заданы: `SECURE_PROXY_SSL_HEADER`, `STATIC_ROOT`, WhiteNoise.
README:306-315 («Что стоит настроить на сервере») эти пункты не упоминает.

## Что не так (подтверждено прогоном при `DJANGO_DEBUG=0`)

**1. Бесконечный редирект-цикл за nginx.** `SECURE_SSL_REDIRECT = not DEBUG`, но
`SECURE_PROXY_SSL_HEADER` не задан. При TLS-терминации на nginx Django считает запрос
незащищённым:
```
GET / c X-Forwarded-Proto: https -> 301 https://example.uz/   -> nginx -> снова 301
```

**2. Статика не отдаётся в проде.** `STATIC_ROOT` не задан:
```
manage.py collectstatic -> ImproperlyConfigured: staticfiles app without STATIC_ROOT
GET /static/schedule/style.css -> 301, 0 байт
```
При `DEBUG=0` `runserver` статику не раздаёт → сайт без CSS/JS.

**3. Дефолт безопасен лишь случайно.** `check --deploy` на настройках по умолчанию даёт
5 предупреждений (`security.W004/W008/W012/W016/W018`). Забытый `DJANGO_DEBUG=0` даёт
не дыру, а нерабочий сайт: `ALLOWED_HOSTS=['localhost','127.0.0.1','[::1]']` → 400 на любом
реальном хосте (проверено). Это лучше, чем debug-режим в проде, но «работает случайно».

## Направление фикса

1. `SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')` — **только** при
   гарантированном переписывании заголовка nginx, то же условие, что уже описано в
   README для `DJANGO_TRUST_XFF`. Сделать управляемым через env (например
   `DJANGO_TRUST_PROTO=1`), по умолчанию выключено — иначе получим ту же дыру, что
   с XFF.
2. `STATIC_ROOT = BASE_DIR / 'staticfiles'` (`staticfiles/` уже в `.gitignore`).
3. Раздача статики: nginx с `try_files` на `STATIC_ROOT` (предпочтительно) либо WhiteNoise
   (тогда добавить в `requirements.txt`).
4. Дополнить README: WSGI-сервер (gunicorn/uwsgi), `collectstatic`, `runserver` в проде
   не использовать, порядок: `migrate` → `collectstatic` → старт.
5. Проверить `SECURE_HSTS_PRELOAD`/`SECURE_HSTS_INCLUDE_SUBDOMAINS` — см. задачу `22`.

## Критерии приёмки

- [ ] При `DJANGO_DEBUG=0` и заголовке `X-Forwarded-Proto: https` главная отдаёт 200.
- [ ] Без заголовка — 301 на https (это правильное поведение при отсутствии прокси).
- [ ] `manage.py collectstatic` отрабатывает без ошибок, файлы появляются в `STATIC_ROOT`.
- [ ] `/static/schedule/style.css` отдаёт 200 с CSS при `DEBUG=0` и включённой раздаче.
- [ ] `manage.py check --deploy` с полным прод-окружением — без предупреждений.
- [ ] README описывает полный порядок деплоя.

## Проверка

```bash
export DJANGO_DEBUG=0 DJANGO_ALLOWED_HOSTS=example.uz DJANGO_CSRF_TRUSTED_ORIGINS=https://example.uz
python manage.py check --deploy
python manage.py collectstatic --noinput
```

## Откат

Реверт `config/settings.py` и README. Схема БД не затрагивается.