"""Контроль доступа: что открыто всем, а что только сотрудникам."""

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Group, Lesson, LessonTime, Room, Subject, Teacher


@override_settings(SECURE_SSL_REDIRECT=False)
class AccessControlTests(TestCase):
    """Гостя не пускаем туда, где меняются данные.

    staff_member_required уводит на /admin/login/ и гостя, и обычного
    пользователя без прав staff — в обоих случаях это 302. Код 403 он
    отдаёт только внутри самой админки; здесь поведение как у is_staff.
    Главное для безопасности — что неstaff не получает 200.
    """

    @classmethod
    def setUpTestData(cls):
        cls.guest = None
        cls.plain = User.objects.create_user('plain', password='secret-plain')
        cls.staff = User.objects.create_user('staff1', password='secret-staff',
                                             is_staff=True)

    def setUp(self):
        self.staff_url = reverse('schedule:import')
        self.xlsx_url = reverse('schedule:export_xlsx')
        self.tpl_url = reverse('schedule:import_template')

    # ------------------------------------------------------------- импорт

    def test_anonymous_redirected_from_import(self):
        response = self.client.get(self.staff_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])

    def test_plain_user_rejected_from_import(self):
        self.client.force_login(self.plain)
        response = self.client.get(self.staff_url)
        self.assertNotEqual(response.status_code, 200,
                            'обычный пользователь не должен попасть в импорт')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])

    def test_anonymous_cannot_post_to_import(self):
        """Защита должна стоять на вьюхе, а не на форме: без неё и POST открыт."""
        response = self.client.post(self.staff_url, {'apply': '1'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])

    def test_staff_passes_import(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.staff_url).status_code, 200)

    # ---------------------------------------------------------- выгрузки

    def test_xlsx_export_closed_for_anonymous(self):
        response = self.client.get(self.xlsx_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])

    def test_xlsx_export_closed_for_plain_user(self):
        self.client.force_login(self.plain)
        response = self.client.get(self.xlsx_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])

    def test_staff_passes_xlsx_export(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.xlsx_url).status_code, 200)

    def test_template_csv_closed_for_anonymous(self):
        response = self.client.get(self.tpl_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])

    # ----------------------------------------------------- что осталось открытым

    def test_csv_export_stays_public(self):
        """«Выгрузка» в меню доступна всем — так и задумано."""
        LessonTime.objects.create(number=1, start='08:00', end='09:20')
        self.assertEqual(self.client.get(reverse('schedule:export')).status_code, 200)

    def test_block_stays_public(self):
        """Фильтры на главной работают без входа, иначе расписание не откроется."""
        lesson_time = LessonTime.objects.create(number=1, start='08:00', end='09:20')
        group = Group.objects.create(name='ЭК-11', course=2)
        response = self.client.get(reverse('schedule:block'),
                                   {'view': 'group', 'id': group.pk})
        self.assertEqual(response.status_code, 200)
        del lesson_time

    def test_picker_list_stays_public(self):
        Group.objects.create(name='ЭК-12', course=1)
        response = self.client.get(reverse('schedule:picker_list'), {'kind': 'group'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('ЭК-12', response.content.decode())

    # -------------------------------------------------------- методы запроса

    def test_block_rejects_post(self):
        """Фрагмент только читает — принимать POST незачем."""
        LessonTime.objects.create(number=1, start='08:00', end='09:20')
        group = Group.objects.create(name='ЭК-13', course=1)
        response = self.client.post(reverse('schedule:block'),
                                    {'view': 'group', 'id': group.pk})
        self.assertEqual(response.status_code, 405)

    def test_picker_list_rejects_post(self):
        response = self.client.post(reverse('schedule:picker_list'), {'kind': 'group'})
        self.assertEqual(response.status_code, 405)

    def test_csv_export_rejects_post(self):
        response = self.client.post(reverse('schedule:export'))
        self.assertEqual(response.status_code, 405)


@override_settings(SECURE_SSL_REDIRECT=False)
class PersonalDataNotExposedTests(TestCase):
    """Телефон и почта преподавателя не должны попадать в публичные страницы."""

    @classmethod
    def setUpTestData(cls):
        cls.lesson_time = LessonTime.objects.create(number=1, start='08:00',
                                                    end='09:20')
        cls.subject = Subject.objects.create(name='Менеджмент', short_name='Мен')
        cls.room = Room.objects.create(name='301')
        cls.teacher = Teacher.objects.create(
            full_name='Сидоров С.С.',
            phone='+998 71 555 11 22',
            email='sidorov@example.uz',
            position='доцент',
            department='Кафедра менеджмента',
        )
        cls.group = Group.objects.create(name='ЭК-14', course=2)

    def setUp(self):
        self.lesson = Lesson.objects.create(
            group=self.group, subject=self.subject, teacher=self.teacher,
            room=self.room, day=1, lesson_time=self.lesson_time,
        )

    def _assert_no_contacts(self, html, where):
        self.assertNotIn(self.teacher.phone, html,
                         f'телефон преподавателя утёк в {where}')
        self.assertNotIn(self.teacher.email, html,
                         f'почта преподавателя утёк в {where}')

    def test_group_view_has_no_contacts(self):
        html = self.client.get('/', {'view': 'group', 'id': self.group.pk}
                               ).content.decode()
        self.assertIn('Сидоров С.С.', html, 'ФИО в расписании нужно')
        self._assert_no_contacts(html, 'расписании группы')

    def test_teacher_view_has_no_contacts(self):
        html = self.client.get('/', {'view': 'teacher', 'id': self.teacher.pk}
                               ).content.decode()
        self._assert_no_contacts(html, 'расписании преподавателя')

    def test_room_view_has_no_contacts(self):
        html = self.client.get('/', {'view': 'room', 'id': self.room.pk}
                               ).content.decode()
        self._assert_no_contacts(html, 'расписании аудитории')

    def test_today_page_has_no_contacts(self):
        html = self.client.get(reverse('schedule:day')).content.decode()
        self._assert_no_contacts(html, 'странице «На сегодня»')

    def test_picker_list_has_no_contacts(self):
        html = self.client.get(reverse('schedule:picker_list'),
                               {'kind': 'teacher'}).content.decode()
        self.assertIn('Сидоров С.С.', html)
        self._assert_no_contacts(html, 'списке преподавателей')

    def test_block_fragment_has_no_contacts(self):
        html = self.client.get(reverse('schedule:block'),
                               {'view': 'teacher', 'id': self.teacher.pk}
                               ).content.decode()
        self._assert_no_contacts(html, 'фрагменте /block/')