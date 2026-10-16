"""Одна лекция на несколько пар подряд — одна ячейка на colspan.

В окне кабинета лекция занимает аудиторию непрерывно. Если она идёт три
пары подряд, показывать её тремя карточками в соседних колонках неверно:
читается как три разных занятия. Проверяется и граница правила — склеиваются
только пары с одинаковым предметом и преподавателем, пустая пара разрыв.
"""

from django.test import TestCase
from django.utils import timezone

from schedule.grid import build_grid, monday_of
from schedule.models import (
    Group,
    Lesson,
    LessonTime,
    Room,
    Subject,
    Teacher,
)


class SlotRunTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.slots = [
            LessonTime.objects.create(number=n, start=f'0{n}:00',
                                      end=f'0{n}:45')
            for n in range(1, 5)
        ]
        cls.pe = Subject.objects.create(name='Физическая культура',
                                        short_name='ФизК')
        cls.math = Subject.objects.create(name='Матан', short_name='Матем')
        cls.teacher = Teacher.objects.create(full_name='Рощина И.Ю.')
        cls.other_teacher = Teacher.objects.create(full_name='Дачев О.В.')
        cls.room = Room.objects.create(name='спорт зал')
        cls.groups = [Group.objects.create(name=n, course=1)
                      for n in ('6305', '6901', '6301', '6309')]
        cls.monday = monday_of(timezone.localdate())

    def lesson(self, group, slot, subject=None, teacher=None, day=1):
        return Lesson.objects.create(
            group=self.groups[group], subject=subject or self.pe,
            lesson_time=slot, day=day, teacher=teacher or self.teacher,
            room=self.room,
        )

    def columns(self, day_index=0):
        grid = build_grid(queryset=Lesson.objects.filter(room=self.room),
                          week_monday=self.monday, merge_runs=True)
        return grid['rows'][day_index]['columns']

    def html(self):
        return self.client.get(
            f'/block/?view=room&id={self.room.pk}').content.decode()

    # ------------------------------------------------------------ склейка

    def test_three_in_a_row_becomes_one_wide_cell(self):
        for slot in self.slots[:3]:
            self.lesson(0, slot)
        columns = self.columns()
        self.assertEqual(columns[0]['colspan'], 3)
        self.assertEqual(sum(c['colspan'] for c in columns),
                         len(self.slots),
                         'склейка не должна терять пары')

    def test_two_in_a_row_span_two_columns(self):
        for slot in self.slots[:2]:
            self.lesson(0, slot)
        self.assertEqual(self.columns()[0]['colspan'], 2)

    def test_single_lesson_stays_one_column(self):
        self.lesson(0, self.slots[0])
        self.assertEqual(self.columns()[0]['colspan'], 1)

    def test_gap_breaks_the_run(self):
        """Пустая пара между двумя занятиями — это перерыв, а не лекция."""
        self.lesson(0, self.slots[0])
        self.lesson(1, self.slots[2])
        columns = self.columns()
        self.assertEqual(columns[0]['colspan'], 1)
        self.assertEqual(columns[1]['colspan'], 1)
        self.assertEqual(columns[2]['colspan'], 1)

    def test_different_teacher_breaks_the_run(self):
        self.lesson(0, self.slots[0])
        self.lesson(1, self.slots[1], teacher=self.other_teacher)
        self.assertEqual(self.columns()[0]['colspan'], 1)

    def test_different_subject_breaks_the_run(self):
        self.lesson(0, self.slots[0])
        self.lesson(1, self.slots[1], subject=self.math)
        self.assertEqual(self.columns()[0]['colspan'], 1)

    def test_groups_of_the_whole_run_are_shown(self):
        for index, slot in enumerate(self.slots[:3]):
            self.lesson(index, slot)
        shown = self.columns()[0]['cells'][0]['combined_groups']
        # По алфавиту, как и в объединённых группах: порядок не должен зависеть
        # от того, в какой паре группа встретилась.
        self.assertEqual(shown, sorted(self.groups[:3], key=lambda g: g.name))

    def test_extra_lesson_in_the_middle_stays_visible(self):
        """Электив другой группы в спортзале не должен исчезнуть."""
        for slot in self.slots[:3]:
            self.lesson(0, slot)
        self.lesson(3, self.slots[2], subject=self.math)
        column = self.columns()[0]
        self.assertEqual(column['colspan'], 3)
        subjects = [cell['subject'] for cell in column['cells']]
        self.assertEqual(subjects.count(self.math), 1,
                         'второе занятие той же пары обязано остаться')
        self.assertEqual(subjects.count(self.pe), 3)

    def test_merging_is_off_outside_room_view(self):
        for slot in self.slots[:2]:
            self.lesson(0, slot)
        grid = build_grid(queryset=Lesson.objects.filter(room=self.room),
                          week_monday=self.monday)
        self.assertIsNone(grid['rows'][0]['columns'])

    # ------------------------------------------------------------ вывод

    def test_colspan_rendered_in_html(self):
        for slot in self.slots[:3]:
            self.lesson(0, slot)
        html = self.html()
        self.assertIn('colspan="3"', html)
        self.assertIn('slot-run', html)

    def test_no_colspan_when_nothing_runs(self):
        self.lesson(0, self.slots[0])
        self.lesson(1, self.slots[1], subject=self.math)
        self.assertNotIn('colspan=', self.html())

    def test_group_view_is_not_merged(self):
        """В расписании группы две одинаковые пары подряд — две пары."""
        for slot in self.slots[:2]:
            self.lesson(0, slot)
        html = self.client.get(
            f'/block/?view=group&id={self.groups[0].pk}').content.decode()
        self.assertNotIn('colspan=', html)