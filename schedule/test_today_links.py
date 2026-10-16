"""Переходы по группе и преподавателю в списке «Сегодня».

Название группы и ФИО преподавателя в таблице «Сегодня» — это ссылки на
их расписания. Раньше они были обычными ссылками: синий текст без
подчёркивания, на странице из двадцати строк их не видно, и человек не
понимает, что на них можно нажать. Теперь это заметные плашки.

Проверяется и разметка, и стили: если бы стиль остался, а класс убрали,
ссылки снова стали бы незаметными — и наоборот.
"""

import re

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from schedule.models import (
    Group,
    Lesson,
    LessonTime,
    Subject,
    Teacher,
)


class TodayLinksTests(TestCase):
    """Ссылки на расписание группы и преподавателя заметны."""

    @classmethod
    def setUpTestData(cls):
        cls.group = Group.objects.create(name='6301', course=1,
                                         specialty='Финансы и кредит (ФК)')
        cls.teacher = Teacher.objects.create(full_name='Холбаева С.Р.',
                                            position='Доцент')
        cls.subject = Subject.objects.create(name='Микроэкономика')
        cls.time = LessonTime.objects.create(number=1, start='09:00',
                                             end='10:20')
        cls.lesson = Lesson.objects.create(
            group=cls.group, subject=cls.subject, teacher=cls.teacher,
            day=1, lesson_time=cls.time,
        )

    def setUp(self):
        self.html = self.client.get(
            reverse('schedule:day'), {'day': 1}).content.decode()

    def test_group_link_is_marked(self):
        self.assertIn(
            f'<a class="cell-link" href="/?view=group&amp;id={self.group.pk}">',
            self.html,
            'название группы должно быть заметной ссылкой',
        )

    def test_teacher_link_is_marked(self):
        self.assertIn(
            f'<a class="cell-link" href="/?view=teacher&amp;id={self.teacher.pk}">',
            self.html,
            'ФИО преподавателя должно быть заметной ссылкой',
        )

    def test_link_leads_to_that_schedule(self):
        """Ссылка ведёт именно туда, куда обещает: на расписание группы.

        Главная страница подставляет сетку отдельным фрагментом, поэтому
        названия группы в её коде нет — он приезжает запросом на /block/.
        """
        target = re.search(r'<a class="cell-link" href="([^"]+)"', self.html)
        self.assertIsNotNone(target)
        response = self.client.get(target.group(1))
        self.assertEqual(response.status_code, 200)

        block = self.client.get(
            reverse('schedule:block'),
            {'view': 'group', 'id': self.group.pk},
        ).content.decode()
        self.assertIn(self.group.name, block)
        self.assertIn(self.subject.name, block)

    def test_no_plain_links_left_in_rows(self):
        """В самих строках таблицы обычных ссылок не осталось.

        Кнопка «Группы» в шапке — это кнопка, ей вид «ссылка» не полагается,
        поэтому проверяются только строки с содержимым ячеек.
        """
        for line in self.html.splitlines():
            if '{{ row.group }}' in line or '{{ row.teacher }}' in line:
                self.assertIn('class="cell-link"', line,
                              f'ссылка без класса: {line.strip()[:80]}')


class CellLinkStyleTests(SimpleTestCase):
    """Плашка должна читаться как кнопка и не выглядеть главной кнопкой."""

    def setUp(self):
        from django.contrib.staticfiles import finders

        with open(finders.find('schedule/style.css'), encoding='utf-8') as fh:
            self.css = fh.read()

    def block(self):
        match = re.search(r'a\.cell-link\s*\{([^}]*)\}', self.css)
        self.assertIsNotNone(match, 'нет правила a.cell-link')
        return match.group(1)

    def test_has_border_and_background(self):
        body = self.block()
        self.assertRegex(body, r'border:\s*1px solid')
        self.assertRegex(body, r'border-radius:\s*\d+px')
        self.assertRegex(body, r'background:\s*var\(')

    def test_reacts_to_hover(self):
        """Без реакции на наведение плашка остаётся серым пятном."""
        hover = re.search(r'a\.cell-link:hover\s*\{([^}]*)\}', self.css)
        self.assertIsNotNone(hover, 'нет реакции на наведение')
        self.assertRegex(hover.group(1), r'background:\s*var\(')

    def test_focus_is_visible_for_keyboard(self):
        focus = re.search(r'a\.cell-link:focus-visible\s*\{([^}]*)\}', self.css)
        self.assertIsNotNone(focus, 'нет рамки фокуса с клавиатуры')
        self.assertRegex(focus.group(1), r'outline:\s*2px solid')

    def test_size_matches_inline_tag_not_a_button(self):
        """Размер как у .tag: это ссылка в списке, а не кнопка действия.

        Если плашка станет размером с .btn, таблица «Сегодня» превратится
        в набор кнопок и перестанет читаться как список.
        """
        tag = re.search(r'\.tag\s*\{([^}]*)\}', self.css).group(1)
        tag_size = float(re.search(r'font-size:\s*([\d.]+)px', tag).group(1))
        link_size = float(re.search(r'font-size:\s*([\d.]+)px', self.block()).group(1))
        self.assertLessEqual(
            abs(link_size - tag_size), 2,
            f'плашка {link_size}px против .tag {tag_size}px — размеры разъедутся',
        )