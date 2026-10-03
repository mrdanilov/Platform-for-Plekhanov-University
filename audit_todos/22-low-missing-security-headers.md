# 22 · ⚪ Нет `SECURE_REFERRER_POLICY`, `Permissions-Policy`; HSTS включается молча

**Severity:** Low — гигиена заголовков безопасности
**Статус:** ⬜ не начато · **Оценка:** ~10 мин
**Зависит от:** `08`

## Где

`config/settings.py:53-60`:

```python
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 0 if DEBUG else 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
```

Отсутствуют: `SECURE_REFERRER_POLICY`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`.

Точечно `referrerpolicy` проставлен только в одном месте —
`university.html:42-43` (iframe с Яндекс-картой).

## Что не так

1. `SECURE_HSTS_PRELOAD = not DEBUG` вместе с `SECURE_HSTS_INCLUDE_SUBDOMAINS`
   включается автоматически при `DEBUG=0`. `includeSubDomains` и preload —
   **необратимые** последствия для всех поддоменов (браузер обязуется ходить к ним
   только по HTTPS). Для этого приложения решение в коде — избыточная предосторожность,
   и оно нигде не описано в README.
2. Нет `SECURE_REFERRER_POLICY` — глобально. `X-Frame-Options: DENY` и
   `SECURE_CONTENT_TYPE_NOSNIFF` уже есть, а referrer — нет.

## Направление фикса

1. Добавить `SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'` (значение по
   умолчанию в современных браузерах; явная фиксация даёт предсказуемость).
2. Вынести HSTS-поддомены и preload в отдельные env-переменные (`DJANGO_HSTS_SUB=1`,
   `DJANGO_HSTS_PRELOAD=1`) с описанием в README. По умолчанию — только
   `SECURE_HSTS_SECONDS`.
3. Опционально `Permissions-Policy` с минимальным набором (например
   `geolocation=(), camera=()`), если iframe с картой не требует исключений
   (проверить: карта Яндекса `map-widget` использует `geolocation`? — проверить в браузере).

## Критерии приёмки

- [ ] `SECURE_REFERRER_POLICY` присутствует в ответах.
- [ ] HSTS-поддомены/preload управляются env и по умолчанию выключены.
- [ ] README описывает, когда включать `includeSubDomains`/preload и почему это необратимо.
- [ ] Карта на странице университета работает (iframe не сломан).

## Проверка

```bash
DJANGO_DEBUG=0 DJANGO_ALLOWED_HOSTS=example.uz python manage.py check --deploy
```

## Откат

Реверт `settings.py`. Миграций нет.