"""Фильтр по курсу в окне выбора.

У вуза одно расписание звонков на всех: в модели ``LessonTime`` только
номер и интервал, курса там нет. Поэтому связь «курс — пара» не хранится,
а восстанавливается по фактическим занятиям: пара попадает в курс тогда,
когда в ней занимается хотя бы одна группа этого курса.

Отсюда важное свойство, которое проверяется тестами: курс, у которого
нет ни одного занятия, не должен получать кнопку в фильтре, а пара без
занятий не должна попадать в список — иначе в списке «Пар» снова появятся
пустые пары от старых демо-данных.

Что проверяется:

* фильтр строится из реальных курсов, а не из выдуманного списка;
* внутри курса пары отсортированы по номеру и идут подряд;
* время в строке — настоящее, из модели, а не «после предыдущей плюс час»;
* на «Все курсах» блоки разных курсов разделены заголовком;
* фильтр не ломает существующий поиск и окно «Групп»;
* пары с одинаковым временем у разных курсов не склеиваются в одну строку.
"""

import re

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from schedule.models import Group, Lesson, LessonTime, Subject, Teacher


class PickerCourseFilterTests(TestCase):
    """Фильтр по курсу в окне выбора."""

    @classmethod
    def setUpTestData(cls):
        cls.subject = Subject.objects.create(name='Микроэкономика')
        cls.teacher = Teacher.objects.create(full_name='Соколова С.Р.')
        # Расписание звонков: утренние пары 1–3, дневные 4–6.
        cls.times = {}
        for number, (start, end) in enumerate((
            ('09:00', '10:20'), ('10:30', '11:50'), ('12:30', '13:50'),
            ('14:00', '15:20'), ('15:30', '16:50'), ('17:00', '18:20'),
        ), start=1):
            cls.times[number] = LessonTime.objects.create(
                number=number, start=start, end=end)

        # 1 и 4 курсы учатся на парах 1–3, 2 и 3 — на парах 4–6.
        # Так в файле вуза: утром младшие, днём старшие курсы.
        for name, course, numbers in (
            ('6301', 1, (1, 2, 3)),
            ('3305', 4, (1, 2, 3)),
            ('5303', 2, (4, 5, 6)),
            ('4301', 3, (4, 5, 6)),
        ):
            group = Group.objects.create(name=name, course=course)
            for number in numbers:
                Lesson.objects.create(
                    group=group, subject=cls.subject, teacher=cls.teacher,
                    day=1, lesson_time=cls.times[number],
                )

        # Пустая пара: в файле не встречается, но в базе осталась от
        # демо-данных. В список попасть не должна.
        cls.empty_pair = LessonTime.objects.create(
            number=7, start='17:30', end='18:50')

    def setUp(self):
        # Лимит 30 обращений к /list/ в минуту общий на процесс, и LocMemCache
        # между тестами не сбрасывается. Без очистки серия тестов этого класса
        # исчерпывает лимит, и следующий получает не список, а заглушку
        # «Слишком много запросов» — тест падает из-за чужого счётчика.
        cache.clear()
        self.addCleanup(cache.clear)

    def html(self, **params):
        params.setdefault('kind', 'pair')
        return self.client.get(reverse('schedule:picker_list'), params).content.decode()

    def pairs_in(self, html):
        """Номера пар в порядке появления в разметке."""
        return [int(n) for n in re.findall(
            r'<span class="picker-item-label">(\d+)-я пара</span>', html)]

    # ------------------------------------------------------- фильтр

    def test_filter_lists_real_courses(self):
        html = self.html()
        self.assertIn('Все курсы', html)
        for course in (1, 2, 3, 4):
            self.assertIn(f'data-course="{course}"', html)

    def test_no_course_button_without_lessons(self):
        """Курса без единого занятия в базе нет — значит и кнопки нет.

        Кнопка «5 курс», под которой ничего не находится, — это ровно та
        пустышка, из-за которой человек не понимает, что дальше.
        """
        html = self.html()
        self.assertNotIn('data-course="5"', html)

    def test_one_button_per_course(self):
        """Кнопка на курс, а не на группу.

        Здесь был баг: в Meta у группы задана сортировка по course и name, и
        Django добавляет name в SELECT при distinct() — на выходе выходило
        по кнопке на каждую группу, то есть одиннадцать «1 курс» подряд.
        Проверяется число кнопок, а не просто наличие.
        """
        Group.objects.create(name='6302', course=1)
        Group.objects.create(name='5302', course=2)
        expected = len({g.course for g in Group.objects.all()})

        for kind in ('pair', 'group'):
            with self.subTest(окно=kind):
                html = self.client.get(
                    reverse('schedule:picker_list'), {'kind': kind}).content.decode()
                # По пробелу или кавычке после класса: иначе под счётчик
                # попадает контейнер «picker-filters» — он начинается так же.
                buttons = len(re.findall(r'class="picker-filter(?:\s|")', html))
                # +1 — кнопка «Все курсы»
                self.assertEqual(
                    buttons, expected + 1,
                    f'в окне «{kind}» кнопок {buttons}, а курсов {expected}',
                )

    def test_course_buttons_have_no_duplicates(self):
        html = self.html()
        for course in (1, 2, 3, 4):
            with self.subTest(курс=course):
                self.assertEqual(
                    html.count(f'data-course="{course}"'), 1,
                    f'кнопка «{course} курс» продублирована',
                )

    def test_only_one_button_is_active(self):
        """Активна одна кнопка — та, по которой фильтровали.

        При дублях подсвечивались все кнопки одного курса сразу.
        """
        html = self.html(course=2)
        self.assertEqual(html.count('picker-filter active'), 1)
        active = re.search(r'<button[^>]*picker-filter active[^>]*>', html)
        self.assertIsNotNone(active)
        self.assertIn('data-course="2"', active.group(0))

    def test_filter_has_no_invented_time(self):
        """Время в списке — из модели, а не собранное на лету."""
        html = self.html()
        self.assertIn('09:00 – 10:20', html)
        self.assertIn('14:00 – 15:20', html)
        # пустая демо-пара с 17:30 не показывается
        self.assertNotIn('17:30', html)

    # --------------------------------------------------- по курсам

    def test_course_filter_keeps_only_its_pairs(self):
        self.assertEqual(self.pairs_in(self.html(course=1)), [1, 2, 3])
        self.assertEqual(self.pairs_in(self.html(course=4)), [1, 2, 3])
        self.assertEqual(self.pairs_in(self.html(course=2)), [4, 5, 6])
        self.assertEqual(self.pairs_in(self.html(course=3)), [4, 5, 6])

    def test_pairs_sorted_by_number(self):
        for course in (1, 2, 3, 4):
            with self.subTest(курс=course):
                numbers = self.pairs_in(self.html(course=course))
                self.assertEqual(numbers, sorted(numbers))

    def test_empty_pair_never_listed(self):
        """Пара, в которой никто не занимается, в список не попадает."""
        self.assertNotIn(str(self.empty_pair.number), self.pairs_in(self.html()))
        for course in (1, 2, 3, 4):
            self.assertNotIn(self.empty_pair.number,
                             self.pairs_in(self.html(course=course)))

    def test_all_courses_groups_by_course(self):
        """На «Все курсах» видно, где кончился один курс и начался другой."""
        html = self.html()
        self.assertIn('1 курс', html)
        self.assertIn('2 курс', html)
        self.assertIn('3 курс', html)
        self.assertIn('4 курс', html)
        self.assertIn('picker-course-head', html)
        self.assertIn('picker-course-sep', html)

    def test_course_headers_are_not_duplicated(self):
        """Заголовок курса один на блок, а не перед каждой парой.

        Считаются именно заголовки внутри списка: одноимённые кнопки
        фильтра выше — другое место, и они повторяются по числу курсов.
        """
        html = self.html()
        self.assertEqual(html.count('class="picker-course-head"'), 4)
        self.assertEqual(html.count('>1 курс</li>'), 1)
        self.assertEqual(html.count('>4 курс</li>'), 1)

    def test_single_course_has_no_separator(self):
        """Внутри одного курса разделитель не нужен: список сплошной."""
        html = self.html(course=2)
        self.assertNotIn('picker-course-sep', html)
        self.assertNotIn('picker-course-head', html)

    def test_shared_pair_time_not_merged(self):
        """У 1 и 4 курса пары 1–3 с одинаковым временем.

        Это две разные строки списка для одного и того же времени: пара
        относится к обоим курсам, но объединять их нельзя — иначе исчезнет
        либо один из курсов, либо само время.
        """
        html = self.html()
        self.assertEqual(html.count('09:00 – 10:20'), 2)
        self.assertEqual(self.pairs_in(self.html(course=1)), [1, 2, 3])
        self.assertEqual(self.pairs_in(self.html(course=4)), [1, 2, 3])

    # ----------------------------------------------- вместе с поиском

    def test_search_still_works(self):
        """Поиск по паре работает вместе с фильтром курса.

        Раньше строка поиска была в окне пар, но список не менялся: человек
        набирал «9:00» и получал то же самое. Теперь ищет и по времени.
        """
        self.assertEqual(self.pairs_in(self.html(q='9:00')), [1, 1])
        self.assertEqual(self.pairs_in(self.html(q='17:00')), [6, 6])

    def test_search_combines_with_course(self):
        """Курс и поиск — независимые вещи, вместе работают как надо."""
        html = self.html(course=1, q='09:00')
        self.assertEqual(self.pairs_in(html), [1])
        self.assertEqual(self.pairs_in(self.html(course=2, q='09:00')), [])

    def test_search_in_groups_window(self):
        html = self.client.get(reverse('schedule:picker_list'),
                               {'kind': 'group', 'q': '6301'}).content.decode()
        self.assertIn('6301', html)
        self.assertNotIn('5303', html)

    def test_groups_window_also_filters_by_course(self):
        """Тот же фильтр в «Групах»: окна не должны отличаться."""
        html = self.client.get(reverse('schedule:picker_list'),
                               {'kind': 'group', 'course': 4}).content.decode()
        self.assertIn('3305', html)
        self.assertNotIn('6301', html)

    def test_unknown_course_falls_back_to_all(self):
        """Курс, которого нет, не должен молча показать пустой список.

        Порядок курсов — по возрастанию, внутри курса по номеру пары.
        """
        self.assertEqual(self.pairs_in(self.html(course=9)),
                         [1, 2, 3, 4, 5, 6, 4, 5, 6, 1, 2, 3])

    def test_teachers_window_has_no_course_filter(self):
        """У преподавателей курса нет, значит и фильтра быть не должно."""
        html = self.client.get(reverse('schedule:picker_list'),
                               {'kind': 'teacher'}).content.decode()
        self.assertNotIn('picker-filter', html)

    def test_group_without_group_filtered_by_lesson_kept_out(self):
        """Пара без группы не принадлежит ни одному курсу."""
        Lesson.objects.create(
            group=None, subject=self.subject, day=1, lesson_time=self.times[2])
        self.assertEqual(self.pairs_in(self.html(course=1)), [1, 2, 3])


class PickerFilterStyleTests(SimpleTestCase):
    """Оформление фильтров."""

    def setUp(self):
        from django.contrib.staticfiles import finders

        with open(finders.find('schedule/style.css'), encoding='utf-8') as fh:
            self.css = fh.read()

    def block(self, selector):
        import re
        match = re.search(re.escape(selector) + r'\s*\{([^}]*)\}', self.css)
        self.assertIsNotNone(match, f'нет стилей {selector}')
        return match.group(1)

    def test_buttons_are_compact(self):
        body = self.block('.picker-filter')
        self.assertRegex(body, r'padding:\s*\d+px')
        self.assertRegex(body, r'font-size:\s*1[0-4](\.\d+)?px')

    def test_active_is_soft_blue_not_solid(self):
        """Активный фильтр — светлый голубой, а не тёмная заливка.

        Тёмная плашка под строкой списка перетягивает на себя внимание и
        выглядит тяжелее, чем сам список. Проверяем, что фон активного —
        переменная --blue-soft, а не --blue.
        """
        body = self.block('.picker-filter.active')
        self.assertIn('var(--blue-soft)', body)
        self.assertNotIn('background: var(--blue)', body)

    def test_inactive_is_light(self):
        body = self.block('.picker-filter')
        self.assertIn('var(--surface-2)', body)

    def test_hover_is_noticeable(self):
        body = self.block('.picker-filter:hover')
        self.assertIn('var(--blue-soft)', body)
        self.assertIn('var(--blue)', body)

    def test_wraps_on_narrow_screens(self):
        body = self.block('.picker-filters')
        self.assertIn('flex-wrap', body)

    def test_course_heading_is_not_a_clickable_row(self):
        """Заголовок курса не должен выглядеть как строка списка."""
        body = self.block('.picker-course-head')
        self.assertIn('font-weight: 700', body)