"""Два занятия у группы в один день на одной паре.

Такое расписание бывает, когда пара делится между преподавателями: группа
одна, день и пара те же, а занятий два. Раньше ограничение в базе
запрещало это как опечатку, хотя рендерить такое умеет — ячейка содержит
список занятий и показывает их друг под другом.

Что проверяется:

* два разных предмета в одной ячейке сохраняются;
* точная копия занятия по-прежнему запрещена — это опечатка, а не расписание;
* в таблице оба занятия попадают в одну ячейку и не слипаются;
* сообщение об ошибке объясняет, что именно повторилось.
"""

import io

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from schedule.models import (
    Group,
    Lesson,
    LessonTime,
    LessonType,
    Room,
    Subject,
    Teacher,
)


class TwoLessonsInOneSlotTests(TestCase):
    """Ограничение: уникально всё занятие, а не слот."""

    def setUp(self):
        self.time = LessonTime.objects.create(number=1, start='08:30', end='09:50')
        self.second_time = LessonTime.objects.create(number=2, start='10:00', end='11:20')
        self.group = Group.objects.create(name='6301', course=1)
        self.teacher = Teacher.objects.create(full_name='Холбаева С.Р.')
        self.other_teacher = Teacher.objects.create(full_name='Гулямова А.Н.')
        self.micro = Subject.objects.create(name='Микроэкономика')
        self.law = Subject.objects.create(name='Право')
        self.room = Room.objects.create(name='3/308')

    def test_two_different_subjects_in_one_slot(self):
        Lesson.objects.create(
            group=self.group, subject=self.micro, teacher=self.teacher,
            room=self.room, day=1, lesson_time=self.time,
        )
        Lesson.objects.create(
            group=self.group, subject=self.law, teacher=self.other_teacher,
            room=self.room, day=1, lesson_time=self.time,
        )
        self.assertEqual(
            Lesson.objects.filter(group=self.group, day=1,
                                  lesson_time=self.time).count(),
            2,
            'пара делится между преподавателями — это законное расписание',
        )

    def test_exact_duplicate_still_blocked(self):
        Lesson.objects.create(
            group=self.group, subject=self.micro, teacher=self.teacher,
            room=self.room, day=1, lesson_time=self.time,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Lesson.objects.create(
                    group=self.group, subject=self.micro, teacher=self.other_teacher,
                    room=self.room, day=1, lesson_time=self.time,
                )
        self.assertEqual(
            Lesson.objects.filter(group=self.group, subject=self.micro).count(), 1,
            'точный дубль не должен сохраниться даже с другим преподавателем',
        )

    def test_same_subject_other_day_allowed(self):
        Lesson.objects.create(
            group=self.group, subject=self.micro, day=1, lesson_time=self.time,
        )
        Lesson.objects.create(
            group=self.group, subject=self.micro, day=2, lesson_time=self.time,
        )
        self.assertEqual(Lesson.objects.filter(group=self.group).count(), 2)

    def test_same_slot_other_parity_allowed(self):
        """Один предмет в одну пару, но через неделю — разные занятия."""
        Lesson.objects.create(
            group=self.group, subject=self.micro, day=1,
            lesson_time=self.time, week_parity=1,
        )
        Lesson.objects.create(
            group=self.group, subject=self.micro, day=1,
            lesson_time=self.time, week_parity=2,
        )
        self.assertEqual(Lesson.objects.filter(group=self.group).count(), 2)


class TwoLessonsRenderingTests(TestCase):
    """Оба занятия должны попасть в таблицу и читаться по отдельности."""

    def setUp(self):
        self.time = LessonTime.objects.create(number=1, start='08:30', end='09:50')
        self.second_time = LessonTime.objects.create(number=2, start='10:00', end='11:20')
        self.group = Group.objects.create(name='6301', course=1)
        self.micro = Subject.objects.create(name='Микроэкономика')
        self.law = Subject.objects.create(name='Право')
        self.kind = LessonType.objects.create(name='Лекция')
        for subject in (self.micro, self.law):
            Lesson.objects.create(
                group=self.group, subject=subject, lesson_type=self.kind,
                day=1, lesson_time=self.time,
                note='служебная пометка',
            )
        # пустая пара, чтобы было видно: соседние ячейки не наползают
        self.empty_pair = self.second_time

    def html(self, **params):
        query = {'view': 'group', 'id': self.group.pk}
        query.update(params)
        response = self.client.get(reverse('schedule:index'), query)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_both_lessons_land_in_one_cell(self):
        html = self.html()
        blocks = html.count('<div class="lesson"')
        self.assertEqual(blocks, 2, 'оба занятия должны попасть в таблицу')

    def test_cell_holds_exactly_two_blocks(self):
        """Проверяется именно ячейка: оба блока внутри одного <td>."""
        html = self.html()
        block = html[html.index('<td class="slot'):]
        block = block[:block.index('</td>')]
        self.assertEqual(block.count('<div class="lesson"'), 2)

    def test_subjects_are_distinguishable(self):
        html = self.html()
        self.assertIn('Микроэкономика', html)
        self.assertIn('Право', html)

    def test_neighbouring_cells_are_not_affected(self):
        """Вторая пара пуста — лишних блоков в таблице быть не должно."""
        html = self.html()
        self.assertEqual(html.count('<div class="lesson"'), 2,
                         'занятия попали в другие ячейки')

    def test_note_stays_internal(self):
        """Примечание не показывается на сайте.

        В примечании сотрудница пишет служебное: «спорт зал», «по группам».
        Это не для студентов, поэтому поле остаётся внутренним — даже если
        в одной ячейке сидят два занятия, подпись видна только в админке.
        """
        saved = list(Lesson.objects.values_list('note', flat=True))
        self.assertTrue(any(saved), 'примечание должно быть в базе, иначе '
                                    'проверка ничего не значит')
        html = self.html()
        self.assertNotIn('служебная пометка', html,
                         'примечание утекло в публичное расписание')


class ConstraintMessageTests(TestCase):
    """Сообщение об ошибке должно говорить, что именно повторилось."""

    def test_message_mentions_subject(self):
        from schedule.models import Lesson as LessonModel
        constraint = LessonModel._meta.constraints[0]
        message = constraint.violation_error_message
        self.assertIn('предмет', message.lower(),
                      'в сообщении должно быть сказано про предмет: '
                      'без него непонятно, что именно повторилось')

    def test_constraint_covers_subject(self):
        from schedule.models import Lesson as LessonModel
        constraint = LessonModel._meta.constraints[0]
        self.assertEqual(constraint.name, 'uniq_lesson_slot')
        for field in ('group', 'day', 'lesson_time', 'week_parity', 'subject'):
            self.assertIn(field, constraint.fields)


class ExportWithTwoLessonsTests(TestCase):
    """Выгрузка не должна падать и не должна терять второе занятие."""

    def setUp(self):
        self.time = LessonTime.objects.create(number=1, start='08:30', end='09:50')
        self.group = Group.objects.create(name='6301', course=1)
        self.micro = Subject.objects.create(name='Микроэкономика')
        self.law = Subject.objects.create(name='Право')
        for subject in (self.micro, self.law):
            Lesson.objects.create(
                group=self.group, subject=subject, day=1, lesson_time=self.time,
            )

    def test_xlsx_contains_both(self):
        """Оба занятия должны попасть в одну ячейку книги.

        Выгрузка берёт фильтр из запроса: без выбранной группы сетка пустая,
        поэтому группу указываем явно.
        """
        response = self.client.get(reverse('schedule:export_xlsx'),
                                  {'view': 'group', 'id': self.group.pk})
        self.assertEqual(response.status_code, 200)
        book = load_workbook(io.BytesIO(response.content))
        texts = [c.value for row in book.active.iter_rows()
                 for c in row if isinstance(c.value, str)]
        self.assertTrue(any('Микроэкономика' in t for t in texts))
        self.assertTrue(any('Право' in t for t in texts),
                        'второе занятие потерялось при выгрузке')

    def test_both_lessons_share_one_cell_in_book(self):
        response = self.client.get(reverse('schedule:export_xlsx'),
                                  {'view': 'group', 'id': self.group.pk})
        book = load_workbook(io.BytesIO(response.content))
        cells = [c.value for row in book.active.iter_rows()
                 for c in row if isinstance(c.value, str)]
        shared = [t for t in cells if 'Микроэкономика' in t and 'Право' in t]
        self.assertTrue(
            shared,
            'два занятия одной пары должны попасть в одну ячейку книги',
        )
