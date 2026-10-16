"""Окно «Замены»: выбор преподавателя и сетка его замен.

Сотруднику нужен не разбор одной записи, а ответ на вопрос «что у меня
меняется». Поэтому клик открывает окно с поиском и списком преподавателей,
а после выбора — та же сетка, что и у остальных окон: дни в строках, пары в
колонках, в ячейке только то, что заменено. Пустая ячейка и есть ответ: на
эту пару замен нет.
"""

from datetime import timedelta
import pathlib

from django.test import TestCase
from django.utils import timezone

from schedule.grid import build_substitution_grid, monday_of
from schedule.models import (
    Group,
    Lesson,
    LessonTime,
    Room,
    Subject,
    Substitution,
    Teacher,
)


class SubstitutionFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.slot1 = LessonTime.objects.create(number=1, start='09:00', end='10:20')
        cls.slot2 = LessonTime.objects.create(number=2, start='10:30', end='11:50')
        cls.subject = Subject.objects.create(name='Матан', short_name='Матем')
        cls.group = Group.objects.create(name='6301', course=1)
        cls.room = Room.objects.create(name='201')
        cls.original = Teacher.objects.create(full_name='Соколова С.Р.')
        cls.replacement = Teacher.objects.create(full_name='Васильева Е.Р.')
        cls.bystander = Teacher.objects.create(full_name='Дачев О.В.')
        cls.monday = monday_of(timezone.localdate())
        cls.lesson = Lesson.objects.create(
            group=cls.group, subject=cls.subject, lesson_time=cls.slot1,
            day=1, teacher=cls.original, room=cls.room,
        )

    def make_sub(self, teacher=None, day_offset=0):
        return Substitution.objects.create(
            lesson=self.lesson,
            date=self.monday + timedelta(days=day_offset),
            teacher=teacher or self.replacement, room=self.room,
            reason='болезнь',
        )


class SubstitutionPickerTests(SubstitutionFixture):
    """Клик по кнопке открывает окно: поиск и список преподавателей."""

    def picker(self, query=''):
        return self.client.get(
            f'/list/?kind=substitution&week={self.monday:%Y-%m-%d}'
            + (f'&q={query}' if query else '')
        ).content.decode()

    def test_lists_all_teachers(self):
        html = self.picker()
        for name in ('Соколова', 'Васильева', 'Дачев'):
            self.assertIn(name, html)

    def test_has_search_field(self):
        self.assertIn('picker-search', self.client.get('/').content.decode())

    def test_search_filters_by_name(self):
        html = self.picker(query='Васил')
        self.assertIn('Васильева', html)
        self.assertNotIn('Дачев', html)

    def test_no_course_filter(self):
        """У преподавателей курса нет — фильтр был бы пустыми кнопками."""
        self.assertNotIn('picker-filters', self.picker())

    def test_count_of_own_replacements(self):
        self.make_sub()
        html = self.picker()
        self.assertIn('1 замен', html)

    def test_count_is_week_scoped(self):
        """Замена прошлой недели не должна попадать в счётчик этой."""
        Substitution.objects.create(
            lesson=self.lesson, date=self.monday - timedelta(days=7),
            teacher=self.replacement, room=self.room,
        )
        self.assertNotIn('1 замен', self.picker())

    def test_teacher_without_substitutions_has_no_count(self):
        self.make_sub()
        html = self.picker()
        line = [x for x in html.split('<li') if 'Дачев' in x][0]
        self.assertNotIn('замен', line)


class SubstitutionGridTests(SubstitutionFixture):
    def html(self, teacher=None, week=None):
        url = '/block/?view=substitution'
        if teacher is not None:
            url += f'&id={teacher.pk}'
        if week:
            url += f'&week={week}'
        return self.client.get(url).content.decode()

    # ------------------------------------------------------------ сетка

    def test_same_table_shape(self):
        """Тот же каркас, что и у остальных окон: дни в строках, пары в колонках."""
        html = self.html(self.original)
        self.assertIn('sheet-table', html)
        self.assertIn('day-head', html)
        self.assertIn('pair-head', html)
        self.assertIn('09:00', html)

    def test_shown_to_the_replaced_teacher(self):
        self.make_sub()
        html = self.html(self.original)
        self.assertIn('Васильева', html)
        self.assertIn('Матем', html)
        self.assertIn('вместо', html)

    def test_shown_to_the_substitute(self):
        self.make_sub()
        html = self.html(self.replacement)
        self.assertIn('Соколова', html)
        self.assertIn('вместо', html)

    def test_not_shown_to_everybody_else(self):
        self.make_sub()
        self.assertNotIn('Матем', self.html(self.bystander))

    def test_teacher_without_substitutions_gets_empty_grid(self):
        self.make_sub()
        html = self.html(self.bystander)
        self.assertIn('sheet-table', html)
        self.assertNotIn('lesson-was', html)

    def test_room_shown_in_footer(self):
        self.make_sub()
        self.assertIn('201', self.html(self.original))

    def test_reason_is_not_shown(self):
        """Причина — личная информация преподавателя, в расписании её нет."""
        self.make_sub()
        self.assertNotIn('болезнь', self.html(self.original))

    def test_other_week_is_empty(self):
        self.make_sub()
        other = (self.monday + timedelta(weeks=1))
        self.assertNotIn('Матем', self.html(self.original, week=other))

    def test_grid_counts_only_its_week(self):
        self.make_sub()
        grid = build_substitution_grid(
            week_monday=self.monday + timedelta(weeks=1), teacher=self.original)
        self.assertEqual(grid['total'], 0)

    def test_parity_is_not_applied(self):
        """Замена привязана к дате, а не к номеру недели."""
        self.make_sub()
        self.assertEqual(Substitution.objects.get().date.isoweekday(), 1)
        self.assertIn('Матем', self.html(self.original))

    def test_title_shows_teacher_name(self):
        self.assertIn('Замены:', self.html(self.original))
        self.assertIn('Соколова', self.html(self.original)[:2000])

    def test_without_teacher_id_nothing_is_shown(self):
        """Выбирать преподавателя обязательно: замена всегда про человека."""
        self.make_sub()
        self.assertEqual(self.client.get('/block/?view=substitution').status_code,
                         404)


class SubstitutionsMenuTests(TestCase):
    def test_button_present(self):
        html = self.client.get('/').content.decode()
        self.assertIn('data-view="substitution"', html)
        self.assertIn('Замены', html)

    def test_button_opens_a_window(self):
        html = self.client.get('/').content.decode()
        button = html[html.index('data-kind="substitution"'):][:260]
        self.assertIn('data-view="substitution"', button)
        self.assertIn('aria-haspopup="dialog"', button,
                      'без этого окно выбора не откроется')

    def test_click_handler_opens_picker(self):
        html = self.client.get('/').content.decode()
        self.assertIn('openPicker(btn.dataset.kind, btn.dataset.view)', html)

    def test_week_is_sent_to_the_picker(self):
        """Счётчик замен в списке считается за ту неделю, что открыта."""
        html = self.client.get('/').content.decode()
        self.assertIn('week: currentWeek', html)

    def test_window_has_its_own_title(self):
        """Заголовок окна замен отличается от остальных."""
        source = (pathlib.Path(__file__).resolve().parent / 'templates'
                  / 'schedule' / '_block.html').read_text(encoding='utf-8')
        self.assertIn("{% t_ 'menu_substitutions' %}: {% endif %}", source)

    def test_other_buttons_keep_pickers(self):
        html = self.client.get('/').content.decode()
        for kind in ('group', 'teacher', 'room'):
            self.assertIn(f'data-kind="{kind}"', html)