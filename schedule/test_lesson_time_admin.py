"""Админка времён пар: добавление, правка и защита от удаления.

Раньше в ``LessonTimeAdmin`` стоял запрет на добавление, пока существует
хоть одна пара. Задумывался он как защита от мусора, но ломал реальную
работу: учебный план вуза не обязан совпадать с уже заведёнными парами,
и новую пару было невозможно завести — админка отвечала 403.

Удаление при этом закрывать не нужно: у ``Lesson.lesson_time`` стоит
``on_delete=PROTECT``, и пару, на которую ссылаются занятия, удалить не
даст — защита на уровне базы, с понятным сообщением.

Что проверяется:

* пару можно добавить, даже когда пары уже есть;
* время правится прямо в списке, без захода в карточку;
* пару, на которую ссылаются занятия, удалить нельзя;
* свободную пару удалить можно;
* в списке видно, сколько занятий на пару.
"""

from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse

from schedule.models import Group, Lesson, LessonTime, Subject


class LessonTimeAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            'boss', 'boss@example.uz', 'secret-password')

    def setUp(self):
        self.first = LessonTime.objects.create(number=1, start='08:00', end='09:20')
        self.second = LessonTime.objects.create(number=2, start='09:30', end='10:50')
        self.client.force_login(self.admin)

    # ------------------------------------------------------------ добавление

    def test_add_page_is_open_when_pairs_exist(self):
        response = self.client.get(reverse('admin:schedule_lessontime_add'))
        self.assertEqual(response.status_code, 200,
                         'страница добавления пары закрыта — новую пару '
                         'завести невозможно')

    def test_new_pair_can_be_added(self):
        response = self.client.post(reverse('admin:schedule_lessontime_add'), {
            'number': 3,
            'start': '09:00',
            'end': '10:20',
            '_save': 'Сохранить',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(LessonTime.objects.count(), 3)
        added = LessonTime.objects.get(number=3)
        self.assertEqual(added.start.strftime('%H:%M'), '09:00')
        self.assertEqual(added.end.strftime('%H:%M'), '10:20')

    def test_plan_times_differing_from_existing_are_addable(self):
        """Ситуация из реального файла: в нём 9.00–10.20, в системе 08:00.

        Раньше такая пара не заводилась: список был непуст, и добавление
        блокировалось. Именно из-за этого сотрудница не могла внести
        расписание.
        """
        for number, (start, end) in enumerate(
            [('09:00', '10:20'), ('10:30', '11:50'), ('12:30', '13:50')], start=3,
        ):
            response = self.client.post(reverse('admin:schedule_lessontime_add'), {
                'number': number, 'start': start, 'end': end,
                '_save': 'Сохранить',
            })
            self.assertEqual(response.status_code, 302, start)
        self.assertEqual(LessonTime.objects.count(), 5)

    # --------------------------------------------------------------- правка

    def test_time_is_editable_in_the_list(self):
        """Время должно правиться прямо в списке — без захода в карточку."""
        response = self.client.post(reverse('admin:schedule_lessontime_changelist'), {
            '_save': 'Сохранить',
            'form-TOTAL_FORMS': '2',
            'form-INITIAL_FORMS': '2',
            'form-MIN_NUM_FORMS': '0',
            'form-MAX_NUM_FORMS': '1000',
            f'form-0-id': str(self.first.pk),
            'form-0-start': '07:30',
            'form-0-end': '08:50',
            f'form-1-id': str(self.second.pk),
            'form-1-start': '09:00',
            'form-1-end': '10:20',
        })
        self.assertEqual(response.status_code, 302)
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual(self.first.start.strftime('%H:%M'), '07:30')
        self.assertEqual(self.second.start.strftime('%H:%M'), '09:00')

    def test_number_is_a_link_not_an_editable_field(self):
        """Номер пары в списке не редактируется: клик открывает карточку."""
        html = self.client.get(
            reverse('admin:schedule_lessontime_changelist')).content.decode()
        self.assertIn(
            f'href="{reverse("admin:schedule_lessontime_change", args=[self.first.pk])}"',
            html,
        )
        self.assertNotIn('form-0-number', html,
                         'номер пары не должен правиться в списке')

    def test_time_survives_change_in_card(self):
        response = self.client.post(
            reverse('admin:schedule_lessontime_change', args=[self.first.pk]),
            {'number': 1, 'start': '10:00', 'end': '11:20', '_save': 'Сохранить'},
        )
        self.assertEqual(response.status_code, 302)
        self.first.refresh_from_db()
        self.assertEqual(self.first.start.strftime('%H:%M'), '10:00')

    # -------------------------------------------------------------- удаление

    def _use_the_pair(self, lesson_time):
        Lesson.objects.create(
            group=Group.objects.create(name='6301', course=1),
            subject=Subject.objects.create(name='Микроэкономика'),
            day=1,
            lesson_time=lesson_time,
        )

    def test_pair_in_use_cannot_be_deleted(self):
        """Занятие ссылается на пару — удаление должно быть отклонено."""
        self._use_the_pair(self.first)
        response = self.client.post(
            reverse('admin:schedule_lessontime_delete', args=[self.first.pk]),
            {'post': 'yes'},
        )
        self.assertIn(response.status_code, (302, 200))
        self.assertTrue(
            LessonTime.objects.filter(pk=self.first.pk).exists(),
            'пару удалили вместе с занятиями, которые на неё ссылаются',
        )
        self.assertTrue(Lesson.objects.exists(), 'занятие должно остаться')

    def test_unused_pair_can_be_deleted(self):
        response = self.client.post(
            reverse('admin:schedule_lessontime_delete', args=[self.second.pk]),
            {'post': 'yes'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(LessonTime.objects.filter(pk=self.second.pk).exists())

    # --------------------------------------------------------------- список

    def test_list_shows_how_many_lessons_use_each_pair(self):
        self._use_the_pair(self.first)
        html = self.client.get(
            reverse('admin:schedule_lessontime_changelist')).content.decode()
        self.assertIn('Занятий', html,
                      'в списке не видно, сколько занятий на паре')

    def test_pairs_are_ordered_by_number(self):
        third = LessonTime.objects.create(number=3, start='11:00', end='12:20')
        html = self.client.get(
            reverse('admin:schedule_lessontime_changelist')).content.decode()
        numbers = [
            int(n) for n in __import__('re').findall(
                r'<td class="field-number">.*?>\s*(\d+)\s*<', html, __import__('re').S)
        ]
        self.assertEqual(numbers, sorted(numbers),
                         'пары в списке должны идти по номеру')
        self.assertTrue(third.pk)


class LessonTimePermissionsTests(TestCase):
    """Права сотрудницы: без right-на группу ничего не откроется."""

    def setUp(self):
        self.staff = User.objects.create_user(
            'clerk', 'clerk@example.uz', 'x', is_staff=True)
        self.staff.user_permissions.add(
            Permission.objects.get(codename='change_lessontime'),
            Permission.objects.get(codename='view_lessontime'),
        )
        self.staff = User.objects.get(pk=self.staff.pk)  # сброс кэша прав
        self.client.force_login(self.staff)
        self.pair = LessonTime.objects.create(number=1, start='08:00', end='09:20')

    def test_can_open_change_page(self):
        response = self.client.get(
            reverse('admin:schedule_lessontime_change', args=[self.pair.pk]))
        self.assertEqual(response.status_code, 200)

    def test_can_change_time(self):
        response = self.client.post(
            reverse('admin:schedule_lessontime_change', args=[self.pair.pk]),
            {'number': 1, 'start': '08:30', 'end': '09:50', '_save': 'Сохранить'},
        )
        self.assertEqual(response.status_code, 302)
        self.pair.refresh_from_db()
        self.assertEqual(self.pair.start.strftime('%H:%M'), '08:30')

    def test_cannot_add_without_permission(self):
        response = self.client.get(reverse('admin:schedule_lessontime_add'))
        self.assertEqual(response.status_code, 403,
                         'без права на добавление добавлять нельзя — '
                         'и это ожидаемо')

    def test_cannot_delete_without_permission(self):
        response = self.client.get(
            reverse('admin:schedule_lessontime_delete', args=[self.pair.pk]))
        self.assertEqual(response.status_code, 403)
