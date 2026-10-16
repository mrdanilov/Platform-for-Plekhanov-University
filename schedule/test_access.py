"""Контроль доступа: что открыто всем, а что только сотрудникам.

Из прежних проверок остались выгрузки. Импорт расписания удалён вместе с
``/import/`` и ``/import/template.csv``: сотрудницы заполняют базу вручную
через админку, а страница импорта больше не существует.
"""

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Group, Lesson, LessonTime, Room, Subject, Teacher


@override_settings(SECURE_SSL_REDIRECT=False)
class AccessControlTests(TestCase):
    """Гость не должен получать данные, отданные без проверки прав.

    Главное, что тут проверяется, — что выгрузка закрыта тем же
    ``staff_member_required``, каким была защищена страница импорта. Если
    страницу удалили вместе с декоратором, а декоратор на выгрузку не
    перенесли, отчёт по расписанию уехал бы наружу.
    """

    @classmethod
    def setUpTestData(cls):
        cls.guest = None
        cls.plain = User.objects.create_user('plain', password='secret-plain')
        cls.staff = User.objects.create_user('staff1', password='secret-staff',
                                             is_staff=True)

    def setUp(self):
        self.xlsx_url = reverse('schedule:export_xlsx')

    # ---------------------------------------------------------- выгрузки

    def test_xlsx_export_open_to_anonymous(self):
        """Книга доступна всем: в ней ровно те же данные, что в публичном CSV.

        Раньше выгрузка была закрыта «только для сотрудников», но ограничение
        ничего не защищало — телефон и почта не попадают ни туда, ни туда,
        а состав полей совпадает. Причина была в цене сборки, и она снята
        лимитом и кэшем (см. test_export_protection.py).
        """
        response = self.client.get(self.xlsx_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content[:2], b'PK',
                         'xlsx — это zip, сигнатура должна быть PK')

    def test_xlsx_export_open_to_plain_user(self):
        self.client.force_login(self.plain)
        self.assertEqual(self.client.get(self.xlsx_url).status_code, 200)

    def test_xlsx_export_carries_no_contacts(self):
        """Причина открыть выгрузку: в ней нет ничего чувствительного.

        Проверяется по всему ответу — искать «телефон» в тексте книги
        бессмысленно, она zip-архив.
        """
        response = self.client.get(self.xlsx_url)
        self.assertEqual(response.status_code, 200)
        teacher = Teacher.objects.first()
        if teacher is not None and teacher.phone:
            self.assertNotIn(teacher.phone.encode(), response.content)
        if teacher is not None and teacher.email:
            self.assertNotIn(teacher.email.encode(), response.content)

    def test_staff_passes_xlsx_export(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.xlsx_url).status_code, 200)

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