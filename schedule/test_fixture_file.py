"""Разбор файла расписания на настоящем файле, а не на строке из теста.

Строки в других проверках берутся из этого же файла, но сам разбор кода на
нём целиком не прогонялся: файл в репозитории не лежал. Теперь лежит —
`schedule/fixtures/example_week.xlsx`, собирается скриптом
`schedule/tests_support/build_fixture.py`.

В файле намеренно оставлены опечатки настоящего расписания: склейка
``бизнеселекц.)``, слипшиеся инициалы ``АрхиповаЗ.З.``, разное написание
времени в одной паре, кураторский час и тип занятия не в первой скобке.
Каждая из них ломала тихую загрузку по отдельности.
"""

from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from schedule.models import Group, Lesson, LessonType, Room, Teacher
from schedule.management.commands.load_week_schedule import read_file

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'example_week.xlsx'


class FixtureReadTests(TestCase):
    """Читается ли файл целиком и без потерь."""

    def test_fixture_is_committed(self):
        self.assertTrue(
            FIXTURE.exists(),
            f'нет файла {FIXTURE.name}: проверять разбор не на чем. '
            f'Собрать: python -m schedule.tests_support.build_fixture',
        )

    def test_every_lesson_is_found(self):
        """Одиннадцать строк в файле, двенадцатая — кураторский час.

        Молча пропущенная строка выглядит как «в расписании так и есть»,
        поэтому количество сверяется, а не «больше нуля».
        """
        records, skipped, pair_times = read_file(str(FIXTURE))
        self.assertEqual(len(records), 11)
        self.assertEqual(skipped['кураторский час'], 1)

    def test_curator_hour_never_becomes_a_lesson(self):
        records, _, _ = read_file(str(FIXTURE))
        for record in records:
            self.assertNotIn('ураторск', record['subject'])

    def test_week_parity_is_taken_from_the_header(self):
        """В шапке «8 неделя», и даты 19–23 октября — тоже чётные.

        Если бы разбор смотрел только на строку «на 1 семестр», файл встал бы
        как нечётная неделя и показывался не в ту неделю.
        """
        records, _, _ = read_file(str(FIXTURE))
        self.assertEqual({r['parity'] for r in records}, {2})

    def test_pair_time_takes_the_most_frequent(self):
        """В файле встречаются «9.00-10.20» и «9.05-10.25».

        Пара в системе одна на неделю, поэтому берётся самое частое, а не
        последнее встретившееся.
        """
        _, _, pair_times = read_file(str(FIXTURE))
        start, end = pair_times[1]
        self.assertEqual((start.strftime('%H:%M'), end.strftime('%H:%M')),
                         ('09:00', '10:20'))

    def test_glued_type_is_recognised(self):
        """«Гражданское право  (лпракт.зан)» — склейка из файла вуза.

        Тип виден, но открывающая скобка не на месте, поэтому искать его надо
        во всех скобках подряд, а не разбирать строку по одному шаблону.
        """
        records, _, _ = read_file(str(FIXTURE))
        record = next(r for r in records if r['subject'] == 'Гражданское право')
        self.assertEqual(record['lesson_type'], 'Практика')
        self.assertEqual(record['teacher'], 'Афанасьева Е.Г.')

    def test_duplicated_position_does_not_reach_the_subject(self):
        """В файле вуза есть «ст. ст. преп. Гаврилова А.Н.».

        Регулярное выражение находит «ст. преп.», и без дополнительной
        чистки в названии предмета оставалось «... ресторанного бизнеса ст».
        Такое название попадало в базу и потом размножалось на все занятия
        этого предмета.
        """
        records, _, _ = read_file(str(FIXTURE))
        record = next(r for r in records
                      if r['subject'].startswith('Бизнес-планирование'))
        self.assertEqual(
            record['subject'],
            'Бизнес-планирование и прогнозирование деятельности предприятий '
            'ресторанного бизнеса',
        )
        self.assertEqual(record['teacher'], 'Гаврилова А.Н.')
        self.assertEqual(record['position'], 'Старший преподаватель')

    def test_no_subject_ends_with_position_remnant(self):
        """Общая проверка на остатки должности в любом предмете файла."""
        remnants = ('ст', 'доц', 'проф', 'асс', 'преп')
        for record in read_file(str(FIXTURE))[0]:
            tail = record['subject'].split()[-1].lower().rstrip('.,;')
            self.assertNotIn(
                tail, remnants,
                f'в предмете «{record["subject"]}» осталась должность',
            )

    def test_type_is_found_in_a_later_bracket(self):
        """«История экономических учений (на английском языке) (лекц.)»."""
        records, _, _ = read_file(str(FIXTURE))
        record = next(r for r in records
                      if r['subject'].startswith('История экономических учений'))
        # Первая скобка — часть названия, вторая — тип. Тип обязан взяться
        # из второй, а не из первой.
        self.assertEqual(record['lesson_type'], 'Лекция')

    def test_profile_is_taken_from_merged_cell_above_codes(self):
        """Профиль лежит строкой выше кодов групп, в объединённой ячейке."""
        records, _, _ = read_file(str(FIXTURE))
        for record in records:
            self.assertIn(record['group'], ('6301', '6303'))
            self.assertTrue(
                record['specialty'].strip(),
                f'у группы {record["group"]} пустой профиль',
            )

    def test_course_is_taken_from_the_group_code(self):
        records, _, _ = read_file(str(FIXTURE))
        self.assertEqual({r['course'] for r in records}, {1})


class FixtureLoadTests(TestCase):
    """Файл доходит до базы целиком."""

    def test_load_fills_everything(self):
        call_command('load_week_schedule', str(FIXTURE), stdout=None)

        self.assertEqual(Lesson.objects.count(), 11)
        self.assertEqual(
            sorted(Group.objects.values_list('name', flat=True)),
            ['6301', '6303'],
        )
        self.assertEqual(Lesson.objects.filter(week_parity=2).count(), 11)

        # Профиль и раздельные аудитории: «3/308» и «спорт зал» — разные записи.
        self.assertEqual(Room.objects.count(), 8)
        self.assertTrue(Room.objects.filter(name='спорт зал').exists())
        self.assertTrue(
            Room.objects.filter(name='308', building='3').exists(),
            'аудитория 308 из «3/308» не разобралась на корпус и номер',
        )

        # Тип занятия: склейка и вторая скобка дали один и тот же «Лекция».
        lecture = LessonType.objects.get(norm_name='лекция')
        self.assertGreaterEqual(Lesson.objects.filter(lesson_type=lecture).count(), 2)

    def test_repeated_load_does_not_duplicate(self):
        """Тот же файл второй раз не должен удвоить расписание."""
        call_command('load_week_schedule', str(FIXTURE), stdout=None)
        call_command('load_week_schedule', str(FIXTURE), stdout=None)
        self.assertEqual(Lesson.objects.count(), 11)
        self.assertEqual(Teacher.objects.count(), 11)

    def test_dry_run_writes_nothing(self):
        call_command('load_week_schedule', str(FIXTURE), '--dry-run',
                     stdout=None)
        self.assertEqual(Lesson.objects.count(), 0)
