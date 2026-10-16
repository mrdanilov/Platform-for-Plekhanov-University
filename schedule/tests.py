"""Тесты замен, вида «на сегодня» и выгрузки."""

import io
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from .models import Group, Lesson, LessonTime, Room, Substitution, Subject, Teacher


# Правило недели берётся из grid.py, а не копируется сюда: своя копия
# расходилась с рабочей при первой же правке формата (задача 13 аудита).
# Тест, который считает неделю по-своему, проверял бы не то, что в бою.
from .grid import _parity, monday_of, week_param


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
        self.assertIn('Сидорова', html)
        self.assertNotIn('Петров', html)

    def test_substitution_reason_not_shown(self):
        """Причина замены — личная информация преподавателя.

        В расписании остаётся только метка «замена». Причина («болезнь»,
        «диарея», семейные обстоятельства) остаётся в модели и админке, но не
        попадает ни в сетку, ни в карточки, ни на страницу дня — и, что важнее,
        в выгрузки: CSV и Excel публичны и скачиваются без входа.
        """
        Substitution.objects.create(
            lesson=self.lesson, date=self.monday,
            teacher=self.other_teacher, reason='диарея у учителя',
        )
        for label, html in (
            ('сетка', self._block()),
            ('карточки', self._block(view='pair', id=self.time.pk)),
            ('страница дня', self.client.get(
                reverse('schedule:day')).content.decode()),
            ('выгрузка CSV', self.client.get(
                reverse('schedule:export')).content.decode()),
        ):
            with self.subTest(label):
                self.assertNotIn('диарея', html)
                self.assertNotIn('class="reason"', html)

    def test_substitution_reason_not_in_xlsx(self):
        """Проверяется содержимое книги, а не её байты: текст лежит в XML
        внутри zip, поэтому искать его надо разобранной книгой."""
        Substitution.objects.create(
            lesson=self.lesson, date=self.monday,
            teacher=self.other_teacher, reason='диарея у учителя',
        )
        response = self.client.get(reverse('schedule:export_xlsx'))
        book = load_workbook(io.BytesIO(response.content))
        texts = [c.value for row in book.active.iter_rows()
                 for c in row if isinstance(c.value, str)]
        self.assertFalse(
            [t for t in texts if 'диарея' in t],
            'причина замены попала в книгу',
        )

    def test_substitution_reason_kept_in_admin(self):
        """Причина не теряется: она нужна сотруднику, который её вносил."""
        sub = Substitution.objects.create(
            lesson=self.lesson, date=self.monday,
            teacher=self.other_teacher, reason='болезнь',
        )
        sub.refresh_from_db()
        self.assertEqual(sub.reason, 'болезнь')

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
        # Неделя ищется тем же правилом, что и в приложении: по ISO-неделе
        # календаря чётность считать нельзя, она живёт в grid._parity.
        even_monday = self.monday
        while _parity(even_monday) != 2:
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
        self.assertContains(response, 'Сидорова')
        self.assertContains(response, 'замена')
        self.assertNotContains(
            response, 'болезнь',
            msg_prefix='причина замены на странице дня не показывается')


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
        # День берём фиксированный, а не сегодняшний. Расписание живёт
        # пн-сб (шесть дней), а в воскресенье isoweekday() даёт 7, и вьюха
        # обрезает номер до 6 — тест падал каждое воскресенье независимо
        # от кода. Чётность недели берётся у текущей недели, как её и
        # считает вьюха без параметра week.
        day_num = 1
        monday = monday_of(timezone.localdate())
        parity = _parity(monday)

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