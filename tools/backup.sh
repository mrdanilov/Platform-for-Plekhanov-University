#!/usr/bin/env bash
# Бэкап базы расписания на сервере.
#
# Копия делается сред��ствами SQLite, а не простым копированием файла:
# если в момент копирования идёт запись, файл может достаться
# наполовину записанным, и такую копию не восстановить.
#
# Запуск:  sudo /opt/timetable/tools/backup.sh
# Планировщик добавляется один раз: см. install.md.

set -euo pipefail

DB="${TIMETABLE_DB:-/opt/timetable/db.sqlite3}"
DEST="${TIMETABLE_BACKUP_DIR:-/var/backups/timetable}"
KEEP_DAYS="${TIMETABLE_BACKUP_KEEP_DAYS:-30}"

if [ ! -f "$DB" ]; then
    echo "Нет файла базы: $DB" >&2
    exit 1
fi

mkdir -p "$DEST"
chmod 700 "$DEST"

STAMP="$(date +%Y%m%d_%H%M%S)"
TARGET="$DEST/db_$STAMP.sqlite3"

python3 - "$DB" "$TARGET" <<'PY'
import sqlite3
import sys

source, target = sys.argv[1], sys.argv[2]
src = sqlite3.connect(source)
dst = sqlite3.connect(target)
with dst:
    src.backup(dst)
dst.close()
src.close()

check = sqlite3.connect(target)
problems = check.execute('PRAGMA integrity_check').fetchone()[0]
lessons = check.execute(
    'SELECT COUNT(*) FROM schedule_lesson').fetchone()[0]
check.close()

if problems != 'ok':
    print('Копия повреждена:', problems, file=sys.stderr)
    sys.exit(1)

print('  занятий в копии:', lessons)
PY

chmod 600 "$TARGET"

# Старые копии убираем, но одну предыдущую оставляем всегда: если сбой
# заметят только сейчас, вчерашняя копия ещё может пригодиться.
find "$DEST" -name 'db_*.sqlite3' -mtime "+$KEEP_DAYS" -delete

echo "Готово: $TARGET"
