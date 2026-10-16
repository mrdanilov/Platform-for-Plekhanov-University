"""Проверка справочников на дубли: `python manage.py check_duplicates`.

Служебная команда, а не часть интерфейса. Нужна в двух случаях:

* перед включением уникальности на реальной базе — посмотреть, что именно
  будет слито, и подтвердить, что это не два разных человека;
* при ручной правке в админке — убедиться, что дубль не появился мимо
  импорта (например, копированием строки).

По умолчанию ничего не меняет. С ``--merge`` сливает дубли: ссылки
перевешиваются на объект с меньшим ``pk``, пустые поля канонического
дополняются полями дубля. Отмены у этого нет — только бэкап.
"""

from django.core.management.base import BaseCommand
from django.db import OperationalError, transaction

from schedule.merge_duplicates import (
    MERGE_PLAN,
    fill_normalized_keys,
    find_duplicate_groups,
    merge_duplicates,
)


class Command(BaseCommand):
    help = 'Показать (и по --merge слить) дубли справочников расписания'

    def add_arguments(self, parser):
        parser.add_argument(
            '--merge', action='store_true',
            help='слить найденные дубли, а не только показать',
        )

    def handle(self, *args, **options):
        try:
            # ключи могли устареть, если строки правили мимо save()
            stale = fill_normalized_keys(dry_run=True)
        except OperationalError as exc:
            self.stderr.write(
                self.style.ERROR(
                    'В базе ещё нет полей norm_*. Примените миграции: '
                    f'python manage.py migrate ({exc})'
                )
            )
            return

        if stale:
            self.stdout.write(
                f'Внимание: у {stale} записей ключ не совпадает с именем '
                f'(правки в обход save()). Применить migrate.'
            )

        self.stdout.write('Проверка ключей:')
        for kind, plan in MERGE_PLAN.items():
            key = ' + '.join(plan['key'])
            total = plan['model'].objects.count()
            self.stdout.write(f'  {kind}: {total} шт., уникальность по «{key}»')

        duplicates = find_duplicate_groups()
        if not duplicates:
            self.stdout.write(self.style.SUCCESS('Дублей нет.'))
            return

        total_groups = sum(len(groups) for groups in duplicates.values())
        self.stdout.write(self.style.WARNING(
            f'Найдено дублей: {total_groups} (по ключу, регистр и «ё» не важны)'
        ))
        for kind, groups in sorted(duplicates.items()):
            label = MERGE_PLAN[kind]['label']
            for key, pks in groups.items():
                self.stdout.write(
                    f'  {kind}: «{key}» → pk {", ".join(str(p) for p in pks)}'
                )
                for pk in pks:
                    self.stdout.write(f'      pk {pk}: {label(MERGE_PLAN[kind]["model"].objects.get(pk=pk))}')

        if not options['merge']:
            self.stdout.write(
                'Ничего не изменено. Повторить со слиянием: '
                'python manage.py check_duplicates --merge'
            )
            return

        self.stdout.write('Слияние:')
        with transaction.atomic():
            report = merge_duplicates(verbose=False)
        for kind, groups in sorted(report.items()):
            for group in groups:
                self.stdout.write(
                    f'  {kind}: «{group["kept"]}» (pk {group["kept_pk"]}) '
                    f'← слиты {group["merged_pks"]}'
                )

        left = find_duplicate_groups()
        if left:
            self.stdout.write(self.style.ERROR(
                f'После слияния осталось дублей: {left}'
            ))
        else:
            self.stdout.write(self.style.SUCCESS('Дубли слиты, база чистая.'))
