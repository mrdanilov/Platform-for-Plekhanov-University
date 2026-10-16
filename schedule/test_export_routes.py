"""Один ресурс — один адрес и одно имя.

Закрывает задачу 14 аудита. Раньше на `export_xlsx_view` висели два
маршрута: `export/timetable.xlsx` и `export/current.xlsx`. Второй нигде не
использовался, но существовал, и это ровно тот случай, из-за которого
через полгода работает «только один из двух адресов» — и непонятно какой.

Проверяется не отсутствие строки в `urls.py`, а свойства, которые важны
на самом деле: один адрес, одно имя, рабочая кнопка и понятный отказ на
старом адресе.
"""

from django.test import TestCase, override_settings
from django.urls import NoReverseMatch, resolve, reverse

from schedule.models import (
    Group,
    Lesson,
    LessonTime,
    Room,
    Subject,
    Teacher,
)


@override_settings(SECURE_SSL_REDIRECT=False)
class ExportRouteTests(TestCase):
    """Критерий приёмки: на одну вьюху — один маршрут и один name."""

    def test_xlsx_url_resolves(self):
        self.assertEqual(
            reverse('schedule:export_xlsx'), '/export/timetable.xlsx')

    def test_csv_and_xlsx_names_pair_up(self):
        """Имена адресов согласованы между собой.

        `/export/timetable.csv` и `/export/timetable.xlsx` — один ресурс в
        двух форматах, значит и называться должны одинаково.
        """
        self.assertEqual(reverse('schedule:export'), '/export/timetable.csv')
        self.assertEqual(
            reverse('schedule:export_xlsx'), '/export/timetable.xlsx')

    def test_old_name_is_gone(self):
        with self.assertRaises(NoReverseMatch):
            reverse('schedule:export_xlsx_current')

    def test_old_url_no_longer_resolves(self):
        from django.urls import Resolver404
        with self.assertRaises(Resolver404):
            resolve('/export/current.xlsx')

    def test_one_view_one_route(self):
        """На каждую вьюху выгрузки — ровно один маршрут.

        Считается по `urlpatterns`, а не по глазам: тот же приём, что и в
        задаче 13, — определение должно быть одно.
        """
        from schedule import urls as schedule_urls

        seen = {}
        for entry in schedule_urls.urlpatterns:
            name = entry.name
            seen.setdefault(name, []).append(str(entry.pattern))

        for name, patterns in seen.items():
            with self.subTest(name=name):
                self.assertEqual(
                    len(patterns), 1,
                    f'имя {name} объявлено на {len(patterns)} путях: {patterns}',
                )

        for route_name in ('export', 'export_xlsx'):
            with self.subTest(route_name=route_name):
                self.assertIn(route_name, seen)

    def test_no_duplicate_url_for_same_view(self):
        """Одна и та же вьюха не должна висеть на двух разных адресах."""
        from schedule import urls as schedule_urls

        by_view = {}
        for entry in schedule_urls.urlpatterns:
            by_view.setdefault(entry.callback, []).append(str(entry.pattern))
        for callback, patterns in by_view.items():
            with self.subTest(view=callback.__name__):
                self.assertEqual(
                    len(patterns), 1,
                    f'{callback.__name__} висит на {patterns}',
                )

    def test_xlsx_is_open_like_csv(self):
        """Обе выгрузки публичны.

        Раньше книга была закрыта «только для сотрудников», но ограничение
        ничего не защищало: состав полей у книги и CSV одинаковый, а
        телефон и почта не попадают ни туда, ни туда. Причина закрытия была
        в цене сборки, и она снята лимитом и кэшем — см.
        `test_export_protection.py`.
        """
        xlsx = self.client.get(reverse('schedule:export_xlsx'))
        csv = self.client.get(reverse('schedule:export'))
        self.assertEqual(xlsx.status_code, 200)
        self.assertEqual(csv.status_code, 200)

    def test_csv_stays_public(self):
        """CSV по-прежнему открыт: на него ссылается меню для студентов."""
        response = self.client.get(reverse('schedule:export'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])


@override_settings(SECURE_SSL_REDIRECT=False)
class ExcelButtonTests(TestCase):
    """Кнопка «Excel» в блоке расписания ведёт на живой адрес."""

    def setUp(self):
        self.time = LessonTime.objects.create(
            number=1, start='08:30', end='09:50')
        self.subject = Subject.objects.create(name='Финансы')
        self.teacher = Teacher.objects.create(full_name='Петров И.Р.')
        self.room = Room.objects.create(name='201')
        self.group = Group.objects.create(name='ЭК-01', course=2)
        Lesson.objects.create(
            group=self.group, subject=self.subject, teacher=self.teacher,
            room=self.room, day=1, lesson_time=self.time,
        )

    def test_button_points_to_live_route(self):
        html = self.client.get(
            reverse('schedule:block'),
            {'view': 'group', 'id': self.group.pk},
        ).content.decode()
        self.assertIn('/export/timetable.xlsx?view=group', html,
                      'кнопка Excel должна вести на существующий маршрут')

    def test_button_keeps_dead_address_gone(self):
        """Старый адрес в разметке появиться не должен.

        Ссылка в шаблоне пишется руками и легко остаётся после удаления
        маршрута: по виду она ничем не отличается от рабочей.
        """
        html = self.client.get(
            reverse('schedule:block'),
            {'view': 'group', 'id': self.group.pk},
        ).content.decode()
        self.assertNotIn('/export/current.xlsx', html)

    def test_button_link_resolves_to_a_real_url(self):
        """Адрес из кнопки должен резолвиться, а не только выглядеть живым."""
        import re

        html = self.client.get(
            reverse('schedule:block'),
            {'view': 'group', 'id': self.group.pk},
        ).content.decode()
        match = re.search(r'href="(/export/[^"]+)"', html)
        self.assertIsNotNone(match, 'в блоке нет ссылки на выгрузку')
        url = match.group(1).replace('&amp;', '&')
        path = url.split('?')[0]
        resolved = resolve(path)
        self.assertEqual(resolved.url_name, 'export_xlsx')

    def test_staff_gets_a_real_workbook(self):
        from django.contrib.auth.models import User

        staff = User.objects.create_user('staff_x', password='x', is_staff=True)
        self.client.force_login(staff)
        response = self.client.get(
            reverse('schedule:export_xlsx'),
            {'view': 'group', 'id': self.group.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            response['Content-Disposition'].endswith('.xlsx"'),
            'книга должна отдаваться как вложение xlsx',
        )
        self.assertEqual(response.content[:2], b'PK',
                         'xlsx — это zip, сигнатура должна быть PK')
