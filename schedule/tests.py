"""Тесты замен, вида «на сегодня» и выгрузки."""

from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Group, Lesson, LessonTime, Room, Substitution, Subject, Teacher


def monday_of(d):
    return d - timedelta(days=d.weekday())


def week_param(monday):
    year, week, _ = monday.isocalendar()
    return f'{year}-W{week:02d}'


# Тесты должны работать и при DJANGO_DEBUG=0, где включён редирект на HTTPS
@override_settings(SECURE_SSL_REDIRECT=False)
class SubstitutionTests(TestCase):
    def setUp(self):
        self.time = LessonTime.objects.create(number=1, start='08:30', end='09:50')
        self.subject = Subject.objects.create(name='Финансы', short_name='Фин')
        self.teacher = Teacher.objects.create(full_name='Петров И.Р.')
        self.other_teacher = Teacher.objects.create(full_name='Сидорова М.А.')
        self.room = Room.objects.create(name='201')
        self.group = Group.objects.create(name='ЭК-01', course=2)
        self.other_group = Group.objects.create(name='ЭК-02', course=2)
        self.monday = monday_of(timezone.localdate())
        self.lesson = Lesson.objects.create(
            group=self.group, subject=self.subject, teacher=self.teacher,
            room=self.room, day=1, lesson_time=self.time,
        )
        self.other_lesson = Lesson.objects.create(
            group=self.other_group, subject=self.subject,
            teacher=self.other_teacher, day=1, lesson_time=self.time,
        )

    def _block(self, **params):
        params.setdefault('view', 'group')
        params.setdefault('id', self.group.pk)
        return self.client.get(reverse('schedule:block'), params).content.decode()

    def test_substitution_replaces_teacher_in_group_view(self):
        Substitution.objects.create(
            lesson=self.lesson, date=self.monday,
            teacher=self.other_teacher, reason='болезнь',
        )
        html = self._block()
        self.assertIn('замена', html)
        self.assertIn('болезнь', html)
        self.assertIn('Сидорова', html)
        self.assertNotIn('Петров', html)

    def test_substitution_visible_for_substituting_teacher(self):
        Substitution.objects.create(
            lesson=self.lesson, date=self.monday, teacher=self.other_teacher,
        )
        response = self.client.get(
            reverse('schedule:block'),
            {'view': 'teacher', 'id': self.other_teacher.pk},
        )
        self.assertContains(response, 'замена')
        self.assertContains(response, 'Фин')

    def test_original_teacher_loses_the_lesson_after_substitution(self):
        Substitution.objects.create(
            lesson=self.lesson, date=self.monday, teacher=self.other_teacher,
        )
        response = self.client.get(
            reverse('schedule:block'),
            {'view': 'teacher', 'id': self.teacher.pk},
        )
        self.assertNotContains(response, 'Фин')

    def test_substitution_visible_on_opposite_parity_week(self):
        self.lesson.week_parity = 1
        self.lesson.save()
        even_monday = self.monday
        while even_monday.isocalendar()[1] % 2 != 0:
            even_monday += timedelta(weeks=1)
        Substitution.objects.create(
            lesson=self.lesson,
            date=even_monday + timedelta(days=self.lesson.day - 1),
            teacher=self.other_teacher,
        )
        html = self._block(week=week_param(even_monday))
        self.assertIn('замена', html)

    def test_substitution_not_shown_next_week(self):
        Substitution.objects.create(
            lesson=self.lesson, date=self.monday, teacher=self.other_teacher,
        )
        next_monday = self.monday + timedelta(weeks=1)
        html = self._block(week=week_param(next_monday))
        self.assertNotIn('замена', html)

    def test_day_view_shows_substitution(self):
        Substitution.objects.create(
            lesson=self.lesson, date=self.monday,
            teacher=self.other_teacher, reason='болезнь',
        )
        response = self.client.get(reverse('schedule:day'), {'day': 1})
        self.assertContains(response, 'болезнь')
        self.assertContains(response, 'Сидорова')


@override_settings(SECURE_SSL_REDIRECT=False)
class DayViewTests(TestCase):
    def setUp(self):
        self.time = LessonTime.objects.create(number=1, start='08:30', end='09:50')
        self.subject = Subject.objects.create(name='Математика', short_name='Матем')
        self.group = Group.objects.create(name='ЭК-01', course=1)
        self.lesson = Lesson.objects.create(
            group=self.group, subject=self.subject, day=1, lesson_time=self.time,
        )

    def test_day_view_respects_parity(self):
        today = timezone.localdate()
        monday = monday_of(today)
        day_num = today.isoweekday()
        parity = 1 if monday.isocalendar()[1] % 2 else 2

        self.lesson.day = day_num
        self.lesson.week_parity = parity
        self.lesson.save()
        response = self.client.get(reverse('schedule:day'), {'day': day_num})
        self.assertIn(self.lesson.pk, [r['lesson'].pk for r in response.context['rows']])

        self.lesson.week_parity = 2 if parity == 1 else 1
        self.lesson.save()
        response = self.client.get(reverse('schedule:day'), {'day': day_num})
        self.assertNotIn(self.lesson.pk, [r['lesson'].pk for r in response.context['rows']])

    def test_day_view_has_tabs_for_all_weekdays(self):
        response = self.client.get(reverse('schedule:day'))
        self.assertEqual(len(response.context['day_tabs']), 6)


@override_settings(SECURE_SSL_REDIRECT=False)
class ExportViewTests(TestCase):
    """CSV публичен, Excel и шаблон — только для сотрудников."""

    def setUp(self):
        User.objects.create_user('staff_x', password='x', is_staff=True)
        self.time = LessonTime.objects.create(number=1, start='08:00', end='09:20')
        self.subject = Subject.objects.create(name='Финансы', short_name='Фин')
        self.teacher = Teacher.objects.create(full_name='Петров И.Р.')
        self.room = Room.objects.create(name='201')
        self.group = Group.objects.create(name='ЭК-01', course=2)
        Lesson.objects.create(
            group=self.group, subject=self.subject, teacher=self.teacher,
            room=self.room, day=1, lesson_time=self.time,
        )

    def test_csv_export_has_header_and_rows(self):
        response = self.client.get(reverse('schedule:export'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])
        body = response.content.decode('utf-8-sig')
        self.assertIn('Группа', body)
        self.assertIn('ЭК-01', body)
        self.assertIn('Финансы', body)

    def test_template_csv_is_offered(self):
        self.client.force_login(User.objects.get(username='staff_x'))
        response = self.client.get(reverse('schedule:import_template'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('День', response.content.decode('utf-8-sig'))

    def test_xlsx_layout_matches_photo_template(self):
        """Дни в строках, пары в колонках, серые шапки — как в файле-шаблоне."""
        from io import BytesIO

        from openpyxl import load_workbook

        self.client.force_login(User.objects.get(username='staff_x'))
        response = self.client.get(
            reverse('schedule:export_xlsx'),
            {'view': 'group', 'id': self.group.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('spreadsheetml', response['Content-Type'])

        sheet = load_workbook(BytesIO(response.content)).active
        grey = lambda c: c.fill.fgColor.rgb.upper()
        # угол пустой, серый
        self.assertIsNone(sheet['A3'].value)
        self.assertEqual(grey(sheet['A3']), 'FFE7E6E6')
        # шапка пары: номер и время
        self.assertEqual(sheet['B3'].value, '1\n08:00 - 09:20')
        self.assertEqual(grey(sheet['B3']), 'FFE7E6E6')
        self.assertTrue(sheet['B3'].font.bold)
        # первый столбец — дни недели
        self.assertEqual(sheet['A4'].value, 'Понедельник')
        self.assertEqual(grey(sheet['A4']), 'FFF2F2F2')
        self.assertEqual(sheet['A9'].value, 'Суббота')
        # содержимое ячейки
        self.assertIn('Финансы', sheet['B4'].value)
        self.assertIn('201', sheet['B4'].value)
        self.assertEqual(sheet.column_dimensions['A'].width, 10)
        self.assertEqual(sheet.column_dimensions['B'].width, 18)

    def test_xlsx_export_requires_valid_target(self):
        self.client.force_login(User.objects.get(username='staff_x'))
        response = self.client.get(
            reverse('schedule:export_xlsx'), {'view': 'group', 'id': 99999},
        )
        self.assertEqual(response.status_code, 404)

    def test_xlsx_without_target_is_empty_but_valid(self):
        from io import BytesIO

        from openpyxl import load_workbook

        self.client.force_login(User.objects.get(username='staff_x'))
        response = self.client.get(reverse('schedule:export_xlsx'))
        self.assertEqual(response.status_code, 200)
        sheet = load_workbook(BytesIO(response.content)).active
        self.assertIsNone(sheet['B4'].value)