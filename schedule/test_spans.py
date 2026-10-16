"""Растяжение пары кнопкой в админке.

Сотрудница жмёт «Растянуть на 2 пары» — хвосты создаются сами: интервалы
берутся из таблицы пар, чётность копируется. Цифру в поле вводить не
надо: вписанная вручную тройка в пятой паре тихо ломает сетку, поэтому
всё, что невозможно, возвращается отказом с объяснением.

Три свойства, которые здесь и проверяются:

* растяжка видна в расписании — одна ячейка на несколько колонок;
* отказ вместо поломки: занятое место, конец таблицы, чужое занятие в
  середине растяжки;
* откат ничего не уносит чужого: правленый вручную хвост не удаляется.
"""

from django.test import TestCase

from schedule.grid import build_grid, monday_of
from schedule.models import (
    Group,
    Lesson,
    LessonTime,
    Room,
    Subject,
    Teacher,
)
from schedule.spans import CannotStretch, changed_extensions, collapse, stretch
from django.utils import timezone


class SpanFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.slots = [
            LessonTime.objects.create(number=n, start=f'0{n}:00',
                                      end=f'0{n}:45')
            for n in range(1, 5)
        ]
        cls.subject = Subject.objects.create(name='Физика', short_name='Физ')
        cls.group = Group.objects.create(name='6301', course=1)
        cls.teacher = Teacher.objects.create(full_name='Соколова С.Р.')
        cls.room = Room.objects.create(name='201')
        cls.lesson = Lesson.objects.create(
            group=cls.group, subject=cls.subject, lesson_time=cls.slots[0],
            day=1, teacher=cls.teacher, room=cls.room,
        )
        cls.monday = monday_of(timezone.localdate())

    def columns(self):
        grid = build_grid(queryset=Lesson.objects.all(),
                          week_monday=self.monday, merge_runs=True)
        return grid['rows'][0]['columns']

    def widths(self):
        return [c['colspan'] for c in self.columns()]


class StretchTests(SpanFixture):
    def test_creates_tail_in_next_slot(self):
        stretch(self.lesson, 2)
        self.assertEqual(Lesson.objects.count(), 2)
        tail = Lesson.objects.filter(extend_of=self.lesson).first()
        self.assertEqual(tail.lesson_time_id, self.slots[1].pk)

    def test_marks_the_root(self):
        stretch(self.lesson, 2)
        self.lesson.refresh_from_db()
        self.assertEqual(self.lesson.spans, 2)

    def test_tail_copies_teacher_room_group(self):
        stretch(self.lesson, 3)
        tail = Lesson.objects.filter(extend_of=self.lesson).first()
        self.assertEqual(tail.teacher_id, self.teacher.pk)
        self.assertEqual(tail.room_id, self.room.pk)
        self.assertEqual(tail.group_id, self.group.pk)

    def test_tail_keeps_parity(self):
        """Иначе на чётной неделе лекция рассыплется на куски."""
        Lesson.objects.filter(pk=self.lesson.pk).update(week_parity=2)
        lesson = Lesson.objects.get(pk=self.lesson.pk)
        stretch(lesson, 2)
        self.assertEqual(Lesson.objects.get(extend_of=lesson).week_parity, 2)

    def test_three_pairs_makes_two_tails(self):
        stretch(self.lesson, 3)
        self.assertEqual(Lesson.objects.filter(extend_of=self.lesson).count(), 2)

    def test_repeating_does_not_multiply(self):
        stretch(self.lesson, 2)
        stretch(self.lesson, 2)
        self.assertEqual(Lesson.objects.filter(extend_of=self.lesson).count(), 1)

    def test_growing_length_adds_missing_tail(self):
        stretch(self.lesson, 2)
        stretch(self.lesson, 3)
        self.assertEqual(Lesson.objects.filter(extend_of=self.lesson).count(), 2)


class StretchRefusalTests(SpanFixture):
    def test_refuses_when_slot_is_taken(self):
        Lesson.objects.create(group=self.group, subject=self.subject,
                              lesson_time=self.slots[1], day=1,
                              teacher=self.teacher, room=self.room)
        with self.assertRaises(CannotStretch) as caught:
            stretch(self.lesson, 2)
        self.assertIn('уже занято', str(caught.exception))

    def test_refuses_past_the_end_of_the_table(self):
        last = Lesson.objects.create(group=self.group, subject=self.subject,
                                     lesson_time=self.slots[3], day=1,
                                     teacher=self.teacher, room=self.room)
        with self.assertRaises(CannotStretch) as caught:
            stretch(last, 2)
        self.assertIn('дальше растягивать некуда', str(caught.exception))

    def test_refuses_absurd_length(self):
        with self.assertRaises(CannotStretch):
            stretch(self.lesson, 9)

    def test_refused_stretch_changes_nothing(self):
        Lesson.objects.create(group=self.group, subject=self.subject,
                              lesson_time=self.slots[1], day=1,
                              teacher=self.teacher, room=self.room)
        before = Lesson.objects.count()
        with self.assertRaises(CannotStretch):
            stretch(self.lesson, 2)
        self.assertEqual(Lesson.objects.count(), before)
        self.lesson.refresh_from_db()
        self.assertEqual(self.lesson.spans, 1)


class RenderedSpanTests(SpanFixture):
    """Растяжка видна в сетке: одна ячейка на несколько колонок."""

    def html(self):
        return self.client.get(
            f'/?view=room&id={self.room.pk}').content.decode()

    def test_width_matches_stretch(self):
        stretch(self.lesson, 3)
        self.assertEqual(self.widths()[0], 3)

    def test_colspan_rendered(self):
        stretch(self.lesson, 3)
        self.assertIn('colspan="3"', self.html())

    def test_tail_is_not_drawn_separately(self):
        stretch(self.lesson, 3)
        self.assertEqual(self.html().count('class="lesson"'), 1)

    def test_pairs_label(self):
        stretch(self.lesson, 3)
        self.assertIn('lesson-span', self.html())
        self.assertIn('1-я 2-я 3-я', self.html())

    def test_groups_view_respects_stretch(self):
        """Явная растяжка видна везде, автосклейка — только в кабинете."""
        stretch(self.lesson, 2)
        html = self.client.get(
            f'/?view=group&id={self.group.pk}').content.decode()
        self.assertIn('colspan="2"', html)

    def test_foreign_lesson_in_the_middle_blocks_it(self):
        """Если в середину растяжки попало чужое занятие — не накрываем его."""
        stretch(self.lesson, 3)
        Lesson.objects.filter(extend_of=self.lesson).first().delete()
        Lesson.objects.create(
            group=Group.objects.create(name='6303', course=1),
            subject=self.subject, lesson_time=self.slots[1], day=1,
            teacher=Teacher.objects.create(full_name='Дачев О.В.'),
            room=self.room)
        self.assertEqual(self.widths()[0], 1,
                         'чужая карточка не должна исчезнуть под широкой ячейкой')


class AutoJoinFlagTests(SpanFixture):
    """Галочка «склеивать» отключает автоматическое объединение."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Вторая группа: первая уже заняла 1-ю пару, а ограничение
        # уникальности не даёт создать для неё же занятие в той же паре.
        cls.other = Group.objects.create(name='6303', course=1)
        for slot in cls.slots[:2]:
            Lesson.objects.create(group=cls.other, subject=cls.subject,
                                  lesson_time=slot, day=1,
                                  teacher=cls.teacher, room=cls.room)

    def columns_of_other(self):
        grid = build_grid(
            queryset=Lesson.objects.filter(group=self.other),
            week_monday=self.monday, merge_runs=True)
        return grid['rows'][0]['columns']

    def test_auto_joined_without_flag_removal(self):
        self.assertEqual(self.columns_of_other()[0]['colspan'], 2)

    def test_flag_splits_them(self):
        Lesson.objects.filter(group=self.other).update(auto_join=False)
        self.assertEqual(self.columns_of_other()[0]['colspan'], 1)


class CollapseTests(SpanFixture):
    def test_collapse_removes_tails(self):
        stretch(self.lesson, 3)
        collapse(self.lesson)
        self.assertEqual(Lesson.objects.count(), 1)

    def test_collapse_resets_length(self):
        stretch(self.lesson, 3)
        collapse(self.lesson)
        self.lesson.refresh_from_db()
        self.assertEqual(self.lesson.spans, 1)

    def test_collapse_back_to_single_column(self):
        stretch(self.lesson, 3)
        collapse(self.lesson)
        self.assertEqual(self.widths()[0], 1)

    def test_deleting_root_removes_tail(self):
        """Хвост без корня — мусор в расписании."""
        stretch(self.lesson, 2)
        self.lesson.delete()
        self.assertEqual(Lesson.objects.count(), 0)

    def test_changed_tail_is_reported(self):
        stretch(self.lesson, 2)
        tail = Lesson.objects.get(extend_of=self.lesson)
        tail.subject = Subject.objects.create(name='Химия', short_name='Хим')
        tail.save()
        self.assertEqual(len(changed_extensions(self.lesson)), 1)

    def test_untouched_tail_is_not_reported(self):
        stretch(self.lesson, 2)
        self.assertEqual(changed_extensions(self.lesson), [])