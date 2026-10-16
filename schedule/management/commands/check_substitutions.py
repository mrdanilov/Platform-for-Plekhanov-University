"""Поиск замен, у которых дата разошлась с днём недели занятия.

Запуск:

    python manage.py check_substitutions      # только показать
    python manage.py check_substitutions --fix   # поправить

Зачем это нужно. Такая запись не выглядит сломанной: она лежит в базе,
а в таблице просто не появляется, потому что ``grid.build_grid`` ищет
замену по ключу ``(lesson_id, date.isoweekday())``. Ни ошибки при
сохранении, ни метки «замена» — только неверное расписание, и найти
причину можно лишь вручную.

Записи, созданные после проверки (она живёт в ``save``, ``bulk_create``
и ``update``), появиться не могут. Команда нужна для данных, созданных
до проверки, — и как страховка, если запись попадёт в базу сторонним
путём: прямым SQL, выгрузкой из другой системы.

Что делает ``--fix``. Дата приводится к тому дню недели, который стоит
в занятии, внутри той же недели. Это единственное исправление, при котором
замена начинает работать: сдвиг на ±7 дней меняет дату, но не меняет
день недели.
"""

import datetime as dt

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import QuerySet

from schedule.models import Lesson, Substitution


class Command(BaseCommand):
    help = 'Найти замены, у которых дата не совпадает с днём недели занятия'

    def add_arguments(self, parser):
        parser.add_argument(
            '--fix', action='store_true',
            help='привести дату к дню недели занятия (по умолчанию только '
                 'показать план)',
        )

    @staticmethod
    def _mismatched() -> QuerySet:
        """Записи, у которых дата не совпадает с днём недели занятия.

        Отбор делается в Python, а не в базе: день недели из даты в SQL не
        выводится, и SQLite не умеет ``strftime`` в CHECK. Записей мало —
        это единицы, а не тысячи, так что перебор незаметен.
        """
        broken = []
        for sub in Substitution.objects.select_related('lesson'):
            if sub.lesson.day != sub.date.weekday() + 1:
                broken.append(sub)
        return broken

    @staticmethod
    def _correct_date(sub) -> dt.date:
        """Дата в том же дне недели, что и занятие, в пределах недели замены.

        Берётся понедельник недели исходной даты и к нему добавляется
        нужный день. Так замена остаётся в своей неделе: перенос на
        соседнюю неделю изменил бы и смысл записи, и расписание целиком.
        """
        monday = sub.date - dt.timedelta(days=sub.date.weekday())
        return monday + dt.timedelta(days=sub.lesson.day - 1)

    def handle(self, *args, **options):
        broken = self._mismatched()

        if not broken:
            self.stdout.write(self.style.SUCCESS(
                'Расхождений не найдено: у всех замен дата совпадает '
                'с днём недели занятия.'))
            return

        plan = []
        for sub in broken:
            fixed = self._correct_date(sub)
            plan.append((sub, fixed))

        self.stdout.write(self.style.WARNING(
            f'Замен с расхождением: {len(plan)}\n'))
        for sub, fixed in plan:
            self.stdout.write(
                f'  id={sub.pk}  {sub.lesson.group} · пара {sub.lesson.lesson_time_id} '
                f'· день занятия {sub.lesson.day}\n'
                f'      было:  {sub.date:%d.%m.%Y} ({sub.date:%A})\n'
                f'      станет: {fixed:%d.%m.%Y} ({fixed:%A})'
            )

        if not options['fix']:
            self.stdout.write(self.style.WARNING(
                '\nЭто только план. Исправить: '
                'python manage.py check_substitutions --fix'))
            return

        with transaction.atomic():
            for sub, fixed in plan:
                # Пишется через save(), а не через queryset.update():
                # сразу после исправления проверка дня недели должна
                # убедиться, что исправление верное.
                sub.date = fixed
                sub.save()

        # Контрольная проверка: после правки расхождений быть не должно.
        remaining = self._mismatched()
        if remaining:
            self.stdout.write(self.style.ERROR(
                f'Исправлено: {len(plan)}. Но осталось расхождений: '
                f'{len(remaining)}. Что-то пошло не так, записи не тронуты.'))
            raise SystemExit(1)

        self.stdout.write(self.style.SUCCESS(
            f'\nИсправлено замен: {len(plan)}. '
            'Теперь они появятся в расписании.'))
        # Напоминание: правка даты меняет то, что сотрудница видела.
        self.stdout.write(
            'Проверьте, что новая дата та же, что записана в журнале '
            'замен — команда приводит дату к дню недели занятия, а не '
            'к дате из бумажного журнала.')