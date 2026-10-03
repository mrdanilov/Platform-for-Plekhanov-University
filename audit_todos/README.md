# Audit TODOs — «Расписание» (Django / РЭУ им. Г. В. Плеханова)

Индекс задач по итогам технического аудита. Каждый фикс — отдельный файл с
контекстом, доказательством, направлением решения, критериями приёмки и командой проверки.

**Легенда статусов:** `⬜ не начато` · `🟡 в работе` · `✅ готово` · `⏭️ отменено (обоснование в файле)`

## Сводка

| # | Severity | Задача | Файл | Статус |
|---|----------|--------|------|--------|
| 01 | 🔴 | «Проверить файл» реально пишет в БД | [01](01-critical-import-dry-run-writes-db.md) | ⬜ |
| 02 | 🔴 | Fallback-разбор применяет импорт дважды | [02](02-critical-import-fallback-double-apply.md) | ✅ |
| 03 | 🔴 | Open redirect через `Referer` в `/lang/` | [03](03-critical-open-redirect-referer.md) | ✅ |
| 04 | 🔴 | Проект не под контролем версий | [04](04-critical-no-version-control.md) | ⬜ |
| 05 | 🟠 | SQLite + локальный FS + LocMemCache не масштабируются | [05](05-high-sqlite-and-local-storage-scaling.md) | ⬜ |
| 06 | 🟠 | Импорт только upsert: удалённые занятия остаются навсегда | [06](06-high-import-upsert-never-deletes.md) | ⬜ |
| 07 | 🟠 | Нет rate limit на контактной форме и логине админки | [07](07-high-no-rate-limit-contact-and-admin.md) | ⬜ |
| 08 | 🟠 | Прод-конфиг: редирект-цикл за nginx и неотдаваемая статика | [08](08-high-prod-config-proxy-and-static.md) | ⬜ |
| 09 | 🟠 | Импорт создаёт дубли сущностей (регистр, «ё») | [09](09-high-import-creates-duplicate-entities.md) | ⬜ |
| 10 | 🟠 | Гонка «прочитал — потом создал» в обоих импортёрах | [10](10-high-import-read-then-create-race.md) | ⬜ |
| 11 | 🟡 | Нет health-check эндпоинта | [11](11-medium-no-health-check.md) | ⬜ |
| 12 | 🟡 | Логирование не настроено вообще | [12](12-medium-no-logging.md) | ⬜ |
| 13 | 🟡 | Дубли `_parse_week`/`grid.parse_week`, мёртвый код в `grid.py` | [13](13-medium-duplicated-week-parsing.md) | ⬜ |
| 14 | 🟡 | Два URL-маршрута на одну вьюху экспорта | [14](14-medium-duplicate-export-url.md) | ⬜ |
| 15 | 🟡 | `Substitution.clean()` не вызывается при create | [15](15-medium-substitution-clean-not-enforced.md) | ⬜ |
| 16 | 🟡 | `Lesson.get_absolute_url()` падает при `group=None` | [16](16-medium-lesson-get-absolute-url-null-group.md) | ⬜ |
| 17 | 🟡 | Интеграционный тест с реальным файлом молча выключен | [17](17-medium-skipped-integration-test-hardcoded-path.md) | ⬜ |
| 18 | 🟡 | Импорты внутри функций, XLSX-экспорт в `views.py` | [18](18-medium-function-level-imports-and-xlsx-in-views.md) | ⬜ |
| 19 | 🟡 | Бэкап копированием живого `db.sqlite3` | [19](19-medium-unsafe-backup-by-copying-sqlite.md) | ⬜ |
| 20 | 🟡 | Версия Python не зафиксирована | [20](20-medium-python-version-not-pinned.md) | ⬜ |
| 21 | ⚪ | `Subject.color` без валидации → CSS-инъекция | [21](21-low-subject-color-no-validation.md) | ⬜ |
| 22 | ⚪ | Нет `SECURE_REFERRER_POLICY`, `Permissions-Policy` | [22](22-low-missing-security-headers.md) | ⬜ |
| 23 | ⚪ | Очистка `tmp_import` только при загрузке, `glob('*')` по каталогу | [23](23-low-tmp-upload-cleanup-glob.md) | ⬜ |
| 24 | ⚪ | `upload.read()` целиком в память, лимиты загрузки не заданы | [24](24-low-upload-read-into-memory.md) | ⬜ |
| 25 | ⚪ | Мелочи: `i18n.format`, inline `onclick`, `random` в captcha | [25](25-low-misc-nitpicks.md) | ⬜ |
| 26 | ⚪ | Нет `.env.example` | [26](26-low-no-env-example.md) | ⬜ |

## Порядок работ

Порядок продиктован зависимостями, а не severity. Полное обоснование — в отчёте
аудита; здесь короткая версия:

**Волна 1 — разблокировать всё остальное (закрывает Critical):**
`01` → `04` → `03`

Логика: пока «Проверить файл» пишет в БД, любые фиксы нельзя безопасно проверять на
продовых данных; пока нет git, состояние «до/после» неразличимо и неоткатываемо;
open redirect изолирован и ждёт своей очереди.

**Волна 2 — сделать деплой рабочим:**
`08` → `07`

Логика: без `SECURE_PROXY_SSL_HEADER` и `STATIC_ROOT` сайт за nginx не работает вообще —
чинить баги на незадеплоенном коде бессмысленно. Rate limit зависит от п.8: нужен
реальный IP за прокси (`DJANGO_TRUST_XFF`).

**Волна 3 — целостность данных:**
`09` + `10` (вместе) → `06`

Логика: `09`/`10` правят то, как импорт пишет; удаление записей (`06`) вводится
только когда «проверка без записи» работает и гонок нет.

**Волна 4 — эксплуатация:**
`05` → `11`, `12` → `13`–`20`

**Волна 5 — гигиена:**
`21`–`26`

## Общие правила

- Перед фиксом: `git` должен быть инициализирован (задача `04`), иначе откат невозможен.
- После каждого фикса: `python manage.py test schedule` (287 тестов) + `python manage.py check --deploy`
  c прод-окружением. Обе команды на текущем состоянии проходят чисто.
- Задачи `01`, `09`, `10`, `06` требуют новых тестов — сейчас именно их отсутствие
  скрывало баг `01`. Тест пишется **вместе** с фиксом, не после.
- Ни одна задача не предполагает redesign. Все фиксы — точечные.