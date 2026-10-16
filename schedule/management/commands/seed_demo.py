"""Демонстрационные расписание: выдуманные группы, предметы и преподаватели.

Зачем это в публичном репозитории. Человек, открывший проект, не должен
угадывать, как выглядит пустая база: он выполнит миграции, увидит пустое
расписание и решит, что платформа ничего не умеет. Демо-данные показывают
интерфейс сразу после установки.

Все данные выдуманные. Ни одной настоящей группы, преподавателя или
аудитории: скриншоты из этого набора попадают в README, который читают
посторонние, и настоящие фамилии там оказаться не должны.

Команда ничего не перезаписывает: если в базе уже есть занятия, она
ничего не делает. Это защита от нечаянной потери расписания.

    python manage.py seed_demo
"""

import random

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from schedule.models import (Group, Lesson, LessonTime, LessonType, Room,
                             Subject, Substitution, Teacher)

PAIRS = [
    (1, '08:30', '09:50'),
    (2, '10:00', '11:20'),
    (3, '11:40', '13:00'),
    (4, '13:20', '14:40'),
    (5, '15:10', '16:30'),
    (6, '16:40', '18:00'),
]

TYPES = [
    ('Лекция', '#b8d4f2'),
    ('Практика', '#bfe3bc'),
    ('Лабораторная работа', '#d6c4ee'),
]

# Предметы, цвет преподавателя и сколько пар в неделю.
SUBJECTS = [
    ('Экономическая теория', 'Экон. теория', 'лекция'),
    ('Микроэкономика', 'Микроэкономика', 'практика'),
    ('Математический анализ', 'Мат. анализ', 'лекция'),
    ('Статистика и эконометрика', 'Статистика', 'практика'),
    ('Менеджмент', 'Менеджмент', 'лекция'),
    ('Финансы', 'Финансы', 'практика'),
    ('Бухгалтерский учёт', 'Бух. учёт', 'практика'),
    ('Маркетинг', 'Маркетинг', 'лекция'),
    ('Информационные технологии', 'Инф. технологии', 'лабораторная работа'),
    ('Физическая культура', 'Физ. культура', 'практика'),
    ('Налоги и налогообложение', 'Налоги', 'лекция'),
    ('Философия', 'Философия', 'лекция'),
    ('Иностранный язык', 'Англ. язык', 'практика'),
    ('Экология и природопользование', 'Экология', 'лекция'),
]

# Преподаватели выдуманные. Должность и кафедра — как в реальном расписании,
# чтобы интерфейс выглядел правдоподобно.
TEACHERS = [
    ('Соколова С.', 'Доцент'),
    ('Ершова М.Э.', 'Доцент'),
    ('Крылова Г.Ш.', 'Профессор'),
    ('Ильина С.А.', 'Доцент'),
    ('Архипова З.З.', 'Старший преподаватель'),
    ('Романова Н.Э.', 'Доцент'),
    ('Аксенов У.Т.', 'Старший преподаватель'),
    ('Юдина Д.А.', 'Доцент'),
    ('Гаврилова А.Н.', 'Старший преподаватель'),
    ('Афанасьева Е.Г.', 'Преподаватель'),
]

GROUPS = [
    ('6301', 1, 'Экономика и управление человеческим капиталом'),
    ('6303', 1, 'Экономика и управление человеческим капиталом'),
    ('6305', 1, 'Международная торговля'),
    ('6307', 1, 'Международная торговля'),
    ('6201', 2, 'Экономика предприятий ресторанного бизнеса'),
    ('6203', 2, 'Экономика предприятий ресторанного бизнеса'),
    ('6205', 2, 'Финансы и кредит'),
    ('5301', 3, 'Экономика и статистика'),
    ('5303', 3, 'Менеджмент организации'),
    ('4301', 4, 'Экономика и менеджмент'),
    ('4303', 4, 'Экономика и менеджмент'),
]

ROOMS = [('201', '1'), ('305', '1'), ('412', '2'), ('207', '3'),
         ('306', '3'), ('104', '1')]


class Command(BaseCommand):
    help = 'Демонстрационные расписание: выдуманные данные для показа проекта'

    @transaction.atomic
    def handle(self, *args, **options):
        if Lesson.objects.exists():
            self.stdout.write(
                self.style.WARNING(
                    'Занятия уже есть — ничего не меняю. '
                    'Демо-данные нужны только в пустой базе.'))
            return

        # Генератор с фиксированным зерном: демо-расписание должно быть
        # одинаковым при каждом запуске, иначе скриншоты в README разъедутся
        # с тем, что видит человек, запустивший команду у себя.
        rng = random.Random(20261016)

        times = [LessonTime.objects.create(number=n, start=s, end=e)
                 for n, s, e in PAIRS]
        types = {
            name: LessonType.objects.get_or_create(
                norm_name=name.lower(),
                defaults={'name': name, 'color': color})[0]
            for name, color in TYPES
        }
        subjects = []
        for name, short, _ in SUBJECTS:
            subject = Subject.objects.get_or_create(
                norm_name=name.lower(),
                defaults={'name': name, 'short_name': short})
            subjects.append(subject[0])
        teachers = [
            Teacher.objects.get_or_create(
                norm_name=fio.lower().replace(' ', ''),
                defaults={'full_name': fio, 'position': position})[0]
            for fio, position in TEACHERS
        ]
        groups = []
        for code, course, specialty in GROUPS:
            group = Group.objects.get_or_create(
                norm_name=f'{code}{course}',
                defaults={'name': code, 'course': course,
                          'specialty': specialty})
            groups.append(group[0])
        rooms = [
            Room.objects.get_or_create(
                norm_name=f'{name}{building}',
                defaults={'name': name, 'building': building})[0]
            for name, building in ROOMS
        ]
        gym = Room.objects.get_or_create(
            norm_name='спортзал', defaults={'name': 'спорт зал'})[0]

        type_by_name = {'лекция': types['Лекция'],
                        'практика': types['Практика'],
                        'лабораторная работа': types['Лабораторная работа']}

        # Расписание строится по-настоящему: занятие стоит в конкретной паре
        # конкретного дня и либо идёт каждую неделю, либо через раз.
        # Порядок слотов задан явно, а не наугад, иначе сетка получалась бы
        # полупустой и демо показывало бы не то, что увидит человек.
        SLOTS = [
            (1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3),
            (3, 1), (3, 2), (3, 3), (4, 1), (4, 2), (4, 3),
            (5, 1), (5, 2), (5, 3), (6, 1), (6, 2),
        ]

        lessons = []
        for group_index, group in enumerate(groups):
            order = list(range(len(subjects)))
            rng.shuffle(order)
            for slot_index, (day, pair) in enumerate(SLOTS):
                # Две трети занятий идут каждую неделю, остальные через раз:
                # так чётная и нечётная недели выглядят по-разному, но обе
                # выглядят заполненными.
                parity = 0 if slot_index % 3 else rng.choice([1, 2])
                if slot_index % 5 == 4:
                    continue
                subject = subjects[order[slot_index % len(order)]]
                _, _, type_key = SUBJECTS[order[slot_index % len(order)]]
                # Один и тот же предмет у всех групп ведёт один и тот же
                # преподаватель — иначе окно преподавателя выглядело бы
                # случайным набором.
                teacher = teachers[order[slot_index % len(order)] % len(teachers)]
                room = (gym if 'Физическая культура' in subject.name
                        else rooms[slot_index % len(rooms)])
                lessons.append(Lesson(
                    group=group, subject=subject,
                    lesson_type=type_by_name[type_key],
                    teacher=teacher, room=room,
                    day=day, lesson_time=times[pair - 1],
                    week_parity=parity))

        Lesson.objects.bulk_create(lessons)

        # Две замены на этой неделе: без них окно «Замены» выглядит пустым
        # и не показывает главное — что причина замены не публикуется, а
        # метка видна и в группе, и в окне преподавателя.
        monday = timezone.localdate() - timedelta_to_monday()
        busy = list(Lesson.objects.filter(day=1).order_by('id')[:2])
        substitutes = list(teachers)
        for number, lesson in enumerate(busy):
            if lesson.room_id:
                Substitution.objects.create(
                    lesson=lesson,
                    date=monday,
                    teacher=substitutes[-1 - number],
                    room=lesson.room,
                    reason='заболела')

        self.stdout.write(self.style.SUCCESS(
            f'Готово: {Lesson.objects.count()} занятий, '
            f'{Group.objects.count()} групп, '
            f'{Subject.objects.count()} предметов, '
            f'{Teacher.objects.count()} преподавателей, '
            f'{Substitution.objects.count()} замен.'))


def timedelta_to_monday():
    """Смещение от сегодняшнего дня до понедельника текущей недели."""
    from datetime import timedelta

    return timedelta(days=timezone.localdate().weekday())
