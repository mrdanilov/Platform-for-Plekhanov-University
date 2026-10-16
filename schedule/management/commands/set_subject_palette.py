"""Раздать предметам новую палитру: `python manage.py set_subject_palette`.

Служебная команда, а не часть интерфейса.

Зачем. Цвет подложки занятия хранится в модели (``Subject.color``), а не в
CSS: шаблон подставляет его инлайн-стилем, чтобы у каждого предмета был
свой оттенок. Поэтому «сделать цвета насыщеннее» средствами CSS не
возможно — можно только окружить блок рамкой. Сами подложки меняются
здесь.

Старая палитра была на уровне Material Design 50 (светлота ~0.88). На белой
таблице такие цвета выглядели как выцветшая бумага, и два соседних
предмета было не отличить. Новая — светлота ~0.67: блок заметен, а текст
внутри держит 11–14:1.

Раскладка детерминированная: цвет считается от суммы кодов названия — той
же, что и у виджета в админке (``exports._pick_color``), поэтому перезапуск
команды ничего не меняет и одинаковые названия получают одинаковый цвет.

Совпадений избегаем: из палитры в восемь цветов хеш неизбежно даёт
одинаковые, а два соседних предмета одного цвета снова неразличимы. Поэтому
если цвет уже занят, берётся следующий по кругу. Порядок — по ``pk``, так
что результат воспроизводим.

По умолчанию только показывает, что изменится. С ``--apply`` записывает.
Перекрашивание — обратимая операция, но цвета, подобранные вручную в
админке, при этом будут перезаписаны, поэтому сначала посмотрите план.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from schedule.exports import SUBJECT_COLORS, _pick_color
from schedule.models import Subject


def _spread(names):
    """Назначает цвета так, чтобы совпадений не было.

    Стартовая позиция — хеш из ``_pick_color``, дальше по кругу, пока не
    встретится свободный цвет. Так раскладка остаётся привязанной к
    названию, но одинаковые оттенки рядом не оказываются.
    """
    palette = list(SUBJECT_COLORS)
    taken = {}
    for name in names:
        start = palette.index(_pick_color(name))
        for step in range(len(palette)):
            candidate = palette[(start + step) % len(palette)]
            if candidate not in taken.values():
                taken[name] = candidate
                break
    return taken


class Command(BaseCommand):
    help = 'Показать (и по --apply раздать) новую палитру предметов'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='записать цвета, а не только показать план',
        )
        parser.add_argument(
            '--all', action='store_true',
            help='перекрасить и те предметы, чей цвет уже из палитры',
        )

    def handle(self, *args, **options):
        apply_changes = options['apply']
        repaint_all = options['all']
        palette = {c.lower() for c in SUBJECT_COLORS}

        subjects = list(Subject.objects.order_by('pk'))
        wanted_by_name = _spread([s.name for s in subjects])

        plan = []
        for subject in subjects:
            wanted = wanted_by_name[subject.name]
            if wanted == subject.color:
                continue
            if not repaint_all and subject.color.lower() in palette:
                continue
            plan.append((subject, subject.color, wanted))

        if not plan:
            self.stdout.write('Менять нечего: все цвета уже из палитры.')
            return

        self.stdout.write(f'Найдено предметов к перекрашиванию: {len(plan)}\n')
        for subject, old, new in plan:
            self.stdout.write(
                f'  {subject.pk:>4}  {subject.name[:38]:<38} {old} -> {new}'
            )

        if not apply_changes:
            self.stdout.write(
                self.style.WARNING(
                    '\nЭто только план. Записать: '
                    'python manage.py set_subject_palette --apply'
                )
            )
            return

        with transaction.atomic():
            for subject, _old, new in plan:
                subject.color = new
                subject.save(update_fields=['color'])

        self.stdout.write(
            self.style.SUCCESS(f'\nПерекрашено предметов: {len(plan)}')
        )
