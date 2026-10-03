"""Заполняет БД расписанием в формате файла-шаблона — для проверки меню."""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from schedule.models import Group, Lesson, LessonTime, Room, Subject, Teacher

TIMES = [
    (1, '08:00', '09:20'), (2, '09:30', '10:50'), (3, '11:00', '12:20'),
    (4, '13:00', '14:20'), (5, '14:30', '15:50'), (6, '16:00', '17:20'),
    (7, '17:30', '18:50'), (8, '19:00', '20:20'),
]

SUBJECTS = [
    ('English', 'English', 'EnG1'),
    ('Discrete Mathematics', 'Discrete Math', None),
    ('Philosophy', 'Philosophy', None),
    ('Introduction to Digital Technologies', 'Intro to Dig Tech', None),
]

TEACHERS = [
    'F. Ruzmetova', 'D. Kdirbaeva', 'A. Kudzinava',
    'B. Toshtemirov', 'V. Zaitsev', 'M. Petrychenko',
]

ROOMS = ['201', '305', '412', 'B-14']

GROUPS = ['EnG1', 'EnG2', 'EnG3', 'G1', 'G2', 'G5']


class Command(BaseCommand):
    help = 'Демонстрационные данные: расписание по группам и преподавателям'

    @transaction.atomic
    def handle(self, *args, **options):
        if Lesson.objects.exists():
            self.stdout.write('Занятия уже есть — пропускаю.')
            return

        times = [
            LessonTime.objects.create(number=n, start=s, end=e)
            for n, s, e in TIMES
        ]
        subjects = [
            Subject.objects.create(name=name, short_name=short,
                                   color='#e8f5e9' if i % 2 else '#e3f2fd')
            for i, (name, short, _) in enumerate(SUBJECTS)
        ]
        teachers = [Teacher.objects.create(full_name=n) for n in TEACHERS]
        rooms = [Room.objects.create(name=n) for n in ROOMS]
        groups = [
            Group.objects.create(name=name, course=1 + i % 3, specialty='Экономика')
            for i, name in enumerate(GROUPS)
        ]

        total = 0
        for gi, group in enumerate(groups):
            for day in range(1, 6):
                for slot, lesson_time in enumerate(times[:4]):
                    subject = subjects[(day + slot + gi) % len(subjects)]
                    Lesson.objects.create(
                        group=group,
                        subject=subject,
                        teacher=teachers[(day + slot + gi) % len(teachers)],
                        room=rooms[(day + slot) % len(rooms)],
                        day=day,
                        lesson_time=lesson_time,
                        week_parity=0,
                        raw_text=(
                            f'{subject.name}\n{group.name}\n'
                            f'{teachers[(day + slot + gi) % len(teachers)].full_name}'
                        ),
                    )
                    total += 1

        self.stdout.write(self.style.SUCCESS(
            f'Готово: {len(groups)} групп, {len(teachers)} преподавателей, '
            f'{len(times)} пар, {total} занятий.'
        ))
        self.stdout.write('Сегодня ' + timezone.localdate().strftime('%d.%m.%Y'))