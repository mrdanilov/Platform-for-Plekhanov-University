"""Объединённые группы: одна лекция на несколько групп — одна карточка.

Преподаватель в один момент ведёт одну пару. Если в файле она стоит у
нескольких групп, это объединённая группа, а не несколько занятий: раньше
они рисовались рядом отдельными карточками, и на виде «Пара» выглядело
так, будто у всех групп в девять утра по своему предмету в спортзале.

Но сливать можно не всё: один преподаватель в двух залах в один момент —
это ошибка в расписании, и прятать её слиянием нельзя.
"""

from datetime import date

from django.test import TestCase
from django.utils import timezone

from schedule.grid import build_grid, monday_of, merge_combined
from schedule.models import (
    Group,
    Lesson,
    LessonTime,
    Room,
    Subject,
    Teacher,
)


class MergeCombinedTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.slot = LessonTime.objects.create(number=1, start='09:00', end='10:20')
        cls.subject = Subject.objects.create(name='Физическая культура',
                                             short_name='ФизК', color='#dcea9e')
        cls.teacher = Teacher.objects.create(full_name='Рощина И.Ю.')
        cls.gym = Room.objects.create(name='спорт зал')
        cls.other = Room.objects.create(name='201')
        cls.first = Group.objects.create(name='6305', course=1)
        cls.second = Group.objects.create(name='6310', course=1)
        cls.third = Group.objects.create(name='6501', course=1)
        for group in (cls.first, cls.second, cls.third):
            Lesson.objects.create(
                group=group, subject=cls.subject, lesson_time=cls.slot,
                day=1, teacher=cls.teacher, room=cls.gym,
            )
        cls.monday = monday_of(timezone.localdate())

    def grid(self, view, subject):
        return build_grid(queryset=Lesson.objects.all(), week_monday=self.monday,
                          cards=(view == 'pair'))

    def cells(self, view, subject):
        return self.grid(view, subject)['rows'][0]['cells'][self.slot.pk]

    # ------------------------------------------------------- слияние

    def test_three_groups_become_one_cell(self):
        cells = self.cells('pair', self.slot)
        self.assertEqual(len(cells), 1,
                         'объединённая группа должна быть одной карточкой')

    def test_all_codes_are_kept(self):
        cell = self.cells('pair', self.slot)[0]
        self.assertEqual([g.name for g in cell['combined_groups']],
                         ['6305', '6310', '6501'])

    def test_total_counts_merged_as_one(self):
        self.assertEqual(self.grid('pair', self.slot)['rows'][0]['total'], 1)

    def test_different_rooms_are_not_merged(self):
        """Один преподаватель в двух залах — ошибка, а не объединение."""
        Lesson.objects.filter(group=self.third).update(room=self.other)
        cells = self.cells('pair', self.slot)
        self.assertEqual(len(cells), 2, 'занятия в разных залах сливать нельзя')
        self.assertEqual([g.name for g in cells[0]['combined_groups']],
                         ['6305', '6310'])

    def test_teacher_and_room_views_merge_too(self):
        for view, subject in (('teacher', self.teacher), ('room', self.gym)):
            with self.subTest(вид=view):
                self.assertEqual(len(self.cells(view, subject)), 1)

    def test_group_view_keeps_one_plain_group(self):
        grid = build_grid(
            queryset=Lesson.objects.filter(group=self.first),
            week_monday=self.monday,
        )
        cell = grid['rows'][0]['cells'][self.slot.pk][0]
        self.assertEqual(cell['combined_groups'], [])
        self.assertEqual(cell['group'], self.first)

    # ------------------------------------------------- разметка видов

    def test_pair_cards_join_codes_with_comma(self):
        html = self.client.get(f'/?view=pair&id={self.slot.pk}').content.decode()
        self.assertIn('6305, 6310, 6501', html)
        self.assertIn('lesson-combined', html)

    def test_pair_cards_do_not_repeat_teacher_and_room(self):
        html = self.client.get(f'/?view=pair&id={self.slot.pk}').content.decode()
        self.assertEqual(html.count('Рощина'), 1)
        self.assertEqual(html.count('спорт зал'), 1)

    def test_day_view_merges_and_keeps_links(self):
        html = self.client.get('/today/?day=1').content.decode()
        self.assertIn('6305', html)
        # На странице дня предмет выводится короткой меткой (Subject.label),
        # а не полным названием.
        self.assertEqual(html.count(self.subject.label), 1,
                         'на странице дня объединение — одно занятие')
        for pk in (self.first.pk, self.second.pk, self.third.pk):
            self.assertIn(f'/?view=group&amp;id={pk}', html)

    def test_group_view_shows_its_own_code_only(self):
        html = self.client.get(f'/?view=group&id={self.first.pk}').content.decode()
        self.assertNotIn('lesson-combined', html)


class MergeHelperTests(TestCase):
    """Сам помощник: сливает только по полному совпадению."""

    def cells(self, specs):
        out = []
        for subject, teacher, room, group in specs:
            out.append({'subject': subject, 'teacher': teacher, 'room': room,
                        'group': group, 'combined_groups': []})
        return out

    def test_empty_list(self):
        self.assertEqual(merge_combined([]), [])

    def test_single_cell_gets_empty_list(self):
        subject = Subject.objects.create(name='Матан', short_name='М')
        cells = self.cells([(subject, None, None, None)])
        self.assertEqual(len(merge_combined(cells)), 1)
        self.assertEqual(cells[0]['combined_groups'], [])

    def test_different_subjects_stay_apart(self):
        a = Subject.objects.create(name='ФизК', short_name='Ф')
        b = Subject.objects.create(name='Матан', short_name='М')
        self.assertEqual(len(merge_combined(self.cells(
            [(a, None, None, None), (b, None, None, None)]))), 2)

    def test_group_codes_sorted_by_name(self):
        subject = Subject.objects.create(name='ФизК', short_name='Ф')
        late = Group.objects.create(name='7000', course=1)
        early = Group.objects.create(name='1000', course=1)
        merged = merge_combined(self.cells(
            [(subject, None, None, late), (subject, None, None, early)]))
        self.assertEqual([g.name for g in merged[0]['combined_groups']],
                         ['1000', '7000'])

    def test_lesson_without_subject_is_keyed_by_raw_text(self):
        first = merge_combined(self.cells(
            [(None, None, None, Group.objects.create(name='1', course=1))]))
        second = self.cells([(None, None, None,
                              Group.objects.create(name='2', course=1))])
        for cell in second:
            cell['raw_text'] = 'Аудит'
        first[0]['raw_text'] = 'Аудит'
        self.assertEqual(len(merge_combined(first + second)), 1)