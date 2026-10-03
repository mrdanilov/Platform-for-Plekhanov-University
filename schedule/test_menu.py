"""Тесты: новое главное меню, блок расписания, выбор по группам и преподавателям."""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Group, Lesson, LessonTime, Room, Subject, Teacher


def monday_of(d):
    return d - timedelta(days=d.weekday())


def week_param(monday):
    year, week, _ = monday.isocalendar()
    return f'{year}-W{week:02d}'


@override_settings(SECURE_SSL_REDIRECT=False)
class MenuTests(TestCase):
    def setUp(self):
        self.time1 = LessonTime.objects.create(number=1, start='08:00', end='09:20')
        self.time2 = LessonTime.objects.create(number=2, start='09:30', end='10:50')
        self.finance = Subject.objects.create(name='Финансы', short_name='Фин')
        self.math = Subject.objects.create(name='Математика', short_name='Матем')
        self.petrov = Teacher.objects.create(full_name='Петров И.Р.')
        self.sidorova = Teacher.objects.create(full_name='Сидорова М.А.')
        self.room = Room.objects.create(name='201')
        self.group_a = Group.objects.create(name='ЭК-01', course=2)
        self.group_b = Group.objects.create(name='ЭК-02', course=2)

        monday = monday_of(timezone.localdate())
        self.lesson_a = Lesson.objects.create(
            group=self.group_a, subject=self.finance, teacher=self.petrov,
            room=self.room, day=1, lesson_time=self.time1,
        )
        self.lesson_b = Lesson.objects.create(
            group=self.group_b, subject=self.math, teacher=self.sidorova,
            day=2, lesson_time=self.time2,
        )

    # ---------------------------------------------- начальное состояние

    def test_home_shows_empty_block_by_default(self):
        response = self.client.get(reverse('schedule:index'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['empty'])
        self.assertContains(response, 'Выберите группу или преподавателя')
        # расписания в блоке нет
        self.assertNotContains(response, 'Петров И.Р.')

    def test_home_has_all_three_buttons(self):
        response = self.client.get(reverse('schedule:index'))
        for label in ['Группы', 'Пары', 'Преподаватели']:
            self.assertContains(response, label)

    def test_home_after_reload_is_empty_again(self):
        self.client.get(reverse('schedule:index'), {'view': 'group', 'id': self.group_a.pk})
        response = self.client.get(reverse('schedule:index'))
        self.assertTrue(response.context['empty'])

    # ------------------------------------------------------- выбор группы

    def test_group_selection_filters_by_group(self):
        response = self.client.get(
            reverse('schedule:index'), {'view': 'group', 'id': self.group_a.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['empty'])
        self.assertContains(response, 'Фин')
        self.assertNotContains(response, 'Матем')

    def test_other_group_sees_only_its_own_lesson(self):
        response = self.client.get(
            reverse('schedule:index'), {'view': 'group', 'id': self.group_b.pk},
        )
        self.assertContains(response, 'Матем')
        self.assertNotContains(response, 'Петров И.Р.')

    def test_picker_list_returns_groups(self):
        response = self.client.get(reverse('schedule:picker_list'), {'kind': 'group'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ЭК-01')
        self.assertContains(response, 'ЭК-02')
        self.assertContains(response, '2 курс')

    def test_picker_list_filters_groups_by_query(self):
        response = self.client.get(
            reverse('schedule:picker_list'), {'kind': 'group', 'q': 'ЭК-02'},
        )
        self.assertContains(response, 'ЭК-02')
        self.assertNotContains(response, 'ЭК-01')

    # ---------------------------------------------------- выбор преподавателя

    def test_teacher_selection_filters_by_teacher(self):
        response = self.client.get(
            reverse('schedule:index'), {'view': 'teacher', 'id': self.petrov.pk},
        )
        self.assertContains(response, 'Фин')
        self.assertContains(response, 'ЭК-01')
        self.assertNotContains(response, 'Матем')

    def test_teacher_view_shows_group_names(self):
        response = self.client.get(
            reverse('schedule:index'), {'view': 'teacher', 'id': self.petrov.pk},
        )
        self.assertContains(response, 'ЭК-01')

    def test_picker_list_returns_teachers_by_name(self):
        response = self.client.get(
            reverse('schedule:picker_list'), {'kind': 'teacher', 'q': 'Петров'},
        )
        self.assertContains(response, 'Петров И.Р.')
        self.assertNotContains(response, 'Сидорова М.А.')

    def test_two_teachers_get_different_schedules(self):
        first = self.client.get(
            reverse('schedule:block'), {'view': 'teacher', 'id': self.petrov.pk},
        ).content.decode()
        second = self.client.get(
            reverse('schedule:block'), {'view': 'teacher', 'id': self.sidorova.pk},
        ).content.decode()
        self.assertIn('Фин', first)
        self.assertNotIn('Матем', first)
        self.assertIn('Матем', second)
        self.assertNotIn('Фин', second)

    # ------------------------------------------------------------- пары

    def test_pair_tab_lists_pairs(self):
        response = self.client.get(reverse('schedule:picker_list'), {'kind': 'pair'})
        self.assertContains(response, '1-я пара')
        self.assertContains(response, '2-я пара')
        self.assertContains(response, '08:00')

    def test_pair_selection_shows_only_that_pair(self):
        response = self.client.get(
            reverse('schedule:index'), {'view': 'pair', 'id': self.time2.pk},
        )
        self.assertContains(response, 'Матем')
        self.assertNotContains(response, 'Фин')

    def test_pair_without_id_falls_back_to_first_pair(self):
        response = self.client.get(reverse('schedule:index'), {'view': 'pair'})
        self.assertFalse(response.context['empty'])
        self.assertContains(response, 'Фин')

    # ----------------------------------------------------- фрагмент блока

    def test_block_fragment_returns_only_block(self):
        response = self.client.get(
            reverse('schedule:block'), {'view': 'group', 'id': self.group_a.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Фин')
        self.assertNotContains(response, '<html')

    def test_block_without_selection_is_404(self):
        self.assertEqual(self.client.get(reverse('schedule:block')).status_code, 404)
        self.assertEqual(
            self.client.get(reverse('schedule:block'), {'view': 'group'}).status_code,
            404,
        )

    def test_unknown_view_is_404(self):
        self.assertEqual(
            self.client.get(reverse('schedule:block'), {'view': 'nonsense'}).status_code,
            404,
        )

    def test_missing_id_is_404(self):
        self.assertEqual(
            self.client.get(
                reverse('schedule:block'), {'view': 'group', 'id': 99999},
            ).status_code,
            404,
        )

    # --------------------------------------------------- таблица-шаблон

    def test_table_has_days_as_rows_and_pairs_as_columns(self):
        response = self.client.get(
            reverse('schedule:block'), {'view': 'group', 'id': self.group_a.pk},
        )
        html = response.content.decode()
        self.assertIn('Понедельник', html)
        self.assertIn('Суббота', html)
        # заголовок пары: номер и интервал
        self.assertIn('08:00', html)

    def test_today_row_is_marked(self):
        today = timezone.localdate()
        if today.isoweekday() > 6:
            self.skipTest('редкий случай')
        response = self.client.get(
            reverse('schedule:block'), {'view': 'group', 'id': self.group_a.pk},
        )
        self.assertContains(response, 'today-row')

    # ------------------------------------------------------ недели

    def test_week_navigation_switches_parity(self):
        this_monday = monday_of(timezone.localdate())
        self.lesson_a.week_parity = 1          # только нечётные недели
        self.lesson_a.save()

        def monday_with_parity(want_odd):
            monday = this_monday
            while (monday.isocalendar()[1] % 2 == 1) != want_odd:
                monday += timedelta(weeks=1)
            return monday

        even_monday = monday_with_parity(False)
        odd_monday = monday_with_parity(True)

        response = self.client.get(
            reverse('schedule:index'),
            {'view': 'group', 'id': self.group_a.pk, 'week': week_param(even_monday)},
        )
        self.assertNotContains(response, 'Фин')

        response = self.client.get(
            reverse('schedule:index'),
            {'view': 'group', 'id': self.group_a.pk, 'week': week_param(odd_monday)},
        )
        self.assertContains(response, 'Фин')

    def test_broken_week_falls_back_to_current(self):
        response = self.client.get(
            reverse('schedule:index'),
            {'view': 'group', 'id': self.group_a.pk, 'week': 'ерунда'},
        )
        self.assertEqual(response.context['week_monday'], monday_of(timezone.localdate()))

    # ------------------------------------------- старые адреса работают

    def test_old_group_url_redirects_to_new_menu(self):
        response = self.client.get(reverse('schedule:group_detail', args=[self.group_a.pk]))
        self.assertRedirects(
            response,
            f"{reverse('schedule:index')}?view=group&id={self.group_a.pk}",
            fetch_redirect_response=False,
        )

    def test_old_teacher_url_redirects_to_new_menu(self):
        response = self.client.get(reverse('schedule:teacher_detail', args=[self.petrov.pk]))
        self.assertRedirects(
            response,
            f"{reverse('schedule:index')}?view=teacher&id={self.petrov.pk}",
            fetch_redirect_response=False,
        )