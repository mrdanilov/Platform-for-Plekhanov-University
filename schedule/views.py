"""Представления: главное меню с блоком расписания, импорт, выгрузка, день."""

import time
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods

from . import captcha, upload_guard
from .import_lock import ImportLocked, exclusive_import_lock
from .contacts import UNIVERSITY
from .excel import DAY_NAMES, import_workbook
from .throttle import client_ip, throttle_ok
from .grid import build_grid, grid_has_content
from .i18n import LANGS, get_language, set_language_cookie, translate
from .importer import (
    MAX_UPLOAD_BYTES,
    ImportResult,
    export_csv,
    export_template_csv,
)
from .models import (
    ContactMessage,
    Group,
    ImportLog,
    Lesson,
    LessonTime,
    Room,
    Substitution,
    Teacher,
)

VIEWS = ('group', 'pair', 'teacher', 'room')

# Сколько записей отдаёт живой поиск /list/ за один раз. Без среза бот
# перебором букв выкачал бы всю базу университета.
PICKER_LIMIT = 50
# Не больше 30 запросов в минуту с одного адреса (см. schedule/throttle.py)
PICKER_RATE_LIMIT = 30
PICKER_RATE_WINDOW = 60


# ---------------------------------------------------------------- служебное

def _monday_of(d):
    return d - timedelta(days=d.weekday())


def _iso_week(monday):
    year, week, _ = monday.isocalendar()
    return f'{year}-W{week:02d}'


def _parse_week(value):
    """'YYYY-Www' или ISO-дата -> понедельник. Мусор -> текущая неделя."""
    today = timezone.localdate()
    default = _monday_of(today)
    if not value:
        return default
    value = str(value).strip()
    try:
        if '-W' in value.upper():
            year, week = value.upper().split('-W')
            return date.fromisocalendar(int(year), int(week), 1)
        return _monday_of(date.fromisoformat(value))
    except (ValueError, TypeError):
        return default


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _teacher_lessons(teacher, week_monday):
    """У преподавателя — свои занятия плюс те, где он ставит замену.

    Если на этой неделе его заменили, те занятия из его расписания уходят.
    """
    substitutions = list(
        Substitution.objects.filter(
            date__gte=week_monday,
            date__lte=week_monday + timedelta(days=6),
        ).select_related('lesson')
    )
    replace_ids = [s.lesson_id for s in substitutions if s.teacher_id == teacher.pk]
    replaced_out = [
        s.lesson_id for s in substitutions
        if s.teacher_id != teacher.pk and s.lesson.teacher_id == teacher.pk
    ]
    return Lesson.objects.filter(
        Q(teacher=teacher) | Q(pk__in=replace_ids)
    ).exclude(pk__in=replaced_out)


def _resolve(request):
    """Разбирает ?view=…&id=…&week=… -> (view, объект/номер пары, неделя)."""
    view = request.GET.get('view', '')
    if view not in VIEWS:
        return '', None, _parse_week(request.GET.get('week'))

    week_monday = _parse_week(request.GET.get('week'))
    pk = _int_or_none(request.GET.get('id'))

    if view == 'group':
        subject = get_object_or_404(Group, pk=pk) if pk else None
    elif view == 'teacher':
        subject = get_object_or_404(Teacher, pk=pk) if pk else None
    elif view == 'room':
        subject = get_object_or_404(Room, pk=pk) if pk else None
    else:
        subject = None
        if pk is None:
            times = list(LessonTime.objects.order_by('number'))
            pk = times[0].pk if times else None
        subject = get_object_or_404(LessonTime, pk=pk) if pk else None

    if subject is None:
        return '', None, week_monday
    return view, subject, week_monday


def _queryset_for(view, subject, week_monday):
    if view == 'group':
        return Lesson.objects.filter(group=subject)
    if view == 'teacher':
        return _teacher_lessons(subject, week_monday)
    if view == 'room':
        return Lesson.objects.filter(room=subject)
    return Lesson.objects.filter(lesson_time=subject)


def _title_for(view, subject, request=None):
    if view == 'pair':
        return f'{subject.number}{translate(request, "pair_of")}'
    if view == 'teacher':
        return subject.full_name
    if view == 'room':
        return str(subject)
    return f'{subject.name}'


# ------------------------------------------------------------------ блок

def _build_grid(view, subject, week_monday, request=None):
    """Общая сборка сетки. Для «Пар» включаем альтернативную раскладку."""
    return build_grid(queryset=_queryset_for(view, subject, week_monday),
                      week_monday=week_monday, request=request,
                      cards=(view == 'pair'))


def _block_context(view, subject, week_monday, request=None):
    grid = _build_grid(view, subject, week_monday, request)
    today = timezone.localdate()
    context = {
        'view': view,
        'selected': subject,
        'selected_id': subject.pk if subject else None,
        'title': _title_for(view, subject, request),
        'grid': grid,
        'grid_is_empty': not grid_has_content(grid),
        'week_monday': week_monday,
        'week_label': (
            f'{week_monday:%d.%m} – {week_monday + timedelta(days=5):%d.%m.%Y}'
        ),
        'week_param': _iso_week(week_monday),
        'prev_week': _iso_week(week_monday - timedelta(weeks=1)),
        'next_week': _iso_week(week_monday + timedelta(weeks=1)),
        'is_current_week': week_monday == _monday_of(today),
        'today': today,
        'empty': False,
    }
    context.update(_text(request))
    return context


# ------------------------------------------------------------- главная

def _empty_grid(week_monday, request=None):
    """Каркас таблицы без единого занятия — до выбора группы/пары/преподавателя."""
    grid = build_grid(queryset=Lesson.objects.none(), week_monday=week_monday,
                      request=request)
    for row in grid['rows']:
        for lesson_time in grid['lesson_times']:
            row['cells'][lesson_time.pk] = []
    grid['is_empty'] = True
    return grid


def index(request):
    """Главная: кнопки переключения и блок расписания.

    Без выбора показывается пустая сетка — дни и пары на месте,
    ячейки пустые, ничего не подставляется, пока не выбрали.
    """
    view, subject, week_monday = _resolve(request)
    if view and subject is not None:
        grid = _build_grid(view, subject, week_monday, request)
    else:
        view, subject, grid = '', None, _empty_grid(week_monday, request)

    context = _menu_context(week_monday, request)
    context.update({
        'view': view,
        'empty': not view,
        'grid': grid,
        'grid_is_empty': not grid_has_content(grid),
        'selected': subject,
        'selected_id': subject.pk if subject else '',
        'title': _title_for(view, subject, request) if view else '',
        'search': request.GET.get('q', '').strip(),
    })
    return render(request, 'schedule/index.html', context)


def university(request):
    """Отдельная страница «Информация о университете».

    Контакты и форма лежат на своей странице, а не в модальном окне:
    адрес можно отправить ссылкой, страница индексируется и работает без JS.
    """
    if request.GET.get('refresh') == '1':
        captcha.issue(request.session)
    token, _question = captcha.current(request.session)

    context = _menu_context(request=request)
    context.update({
        'uni': UNIVERSITY,
        'sent': request.GET.get('sent') == '1',
        'captcha_token': token,
    })
    return render(request, 'schedule/university.html', context)


def university_captcha(request):
    """Картинка с примером для проверки.

    Рисуется на сервере без внешних сервисов и без Pillow — см. schedule/captcha.py.
    Ответ клиенту не отдаётся: он лежит в сессии и проверяется при отправке формы.
    """
    _token, question = captcha.current(request.session)
    response = HttpResponse(captcha.render(question), content_type='image/png')
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    return response


def university_contact(request):
    """POST формы «Связаться с нами».

    Обычный POST без JavaScript: сохраняет обращение и перерисовывает
    страницу с подтверждением `?sent=1`, а при ошибке — с подсказкой, что
    заполнить. CSRF — штатный, cookie и токен идут вместе с формой.
    """
    if request.method != 'POST':
        return redirect('schedule:university')

    name = request.POST.get('name', '').strip()
    email = request.POST.get('email', '').strip()
    message = request.POST.get('message', '').strip()
    consent = request.POST.get('consent') == '1'

    errors = []
    if not name:
        errors.append('name')
    if not email or '@' not in email:
        errors.append('email')
    if not message:
        errors.append('message')
    if not consent:
        errors.append('consent')

    # проверка примера: вопрос одноразовый, поэтому дальше он уже выдан заново
    if not captcha.verify(request.session,
                          request.POST.get('captcha_token', ''),
                          request.POST.get('captcha', '')):
        errors.append('captcha')

    if errors:
        token, _question = captcha.current(request.session)
        context = _menu_context(request=request)
        context.update({
            'uni': UNIVERSITY,
            'sent': False,
            'form_errors': errors,
            'captcha_token': token,
        })
        return render(request, 'schedule/university.html', context, status=400)

    ContactMessage.objects.create(
        name=name[:120], email=email[:254], message=message[:4000], consent=True,
    )
    return redirect(f'{reverse("schedule:university")}?sent=1')


def _safe_return_url(value, request):
    """Адрес возврата из пользовательского ввода или None, если он небезопасен.

    Проверки ``startswith('/')`` мало: «//evil.example» им удовлетворяется, но
    браузер читает такое значение как protocol-relative URL и уводит на чужой
    сайт. Канонический хелпер Django отсекает чужие хосты и схемы (javascript:),
    управляющие символы, ``///host`` и абсурдную длину; ``//`` без хоста он
    считает допустимым, поэтому этот случай отсекаем отдельно.
    """
    if not value or value.startswith('//'):
        return None
    if not url_has_allowed_host_and_scheme(value, {request.get_host()}):
        return None
    return value


def set_language(request):
    """Переключатель RU/UZ в шапке.

    Ссылка в шапке ведёт сюда: /lang/?lang=uz&next=/today/
    Сначала вычисляем адрес возврата, потом ставим cookie — иначе вернём
    пользователя на «чистую» страницу и он не заметит смены языка.
    """
    lang = request.GET.get('lang', '')
    if lang not in LANGS:
        lang = get_language(request)

    # И next, и Referer приходят из пользовательского ввода, поэтому проходят
    # одну и ту же проверку; иначе referer вроде «//evil.example» ушёл бы в Location.
    target = (
        _safe_return_url(request.GET.get('next'), request)
        or _safe_return_url(request.META.get('HTTP_REFERER'), request)
        or '/'
    )

    response = redirect(target)
    set_language_cookie(response, lang)
    return response


def _text(request):
    """Часто используемые переводы, чтобы не звать translate() в каждом месте."""
    return {
        'week_now': translate(request, 'now'),
        'room_prefix': f'{translate(request, "room")} ',
        'pair_of': translate(request, 'pair_of'),
        'course_word': translate(request, 'course'),
    }


def _menu_context(week_monday=None, request=None):
    today = timezone.localdate()
    if week_monday is None:
        week_monday = _monday_of(today)
    context = {
        'week_monday': week_monday,
        'week_param': _iso_week(week_monday),
        'week_label': f'{week_monday:%d.%m} – {week_monday + timedelta(days=5):%d.%m.%Y}',
        'prev_week': _iso_week(week_monday - timedelta(weeks=1)),
        'next_week': _iso_week(week_monday + timedelta(weeks=1)),
        'is_current_week': week_monday == _monday_of(today),
        'today': today,
        'today_short': translate(request, f'dow_{today.isoweekday()}'),
        'group_count': Group.objects.count(),
        'teacher_count': Teacher.objects.filter(active=True).count(),
        'lesson_count': Lesson.objects.count(),
        'has_lesson_times': LessonTime.objects.exists(),
    }
    context.update(_text(request))
    return context


@require_GET
def block(request):
    """Фрагмент блока расписания — подгружается без перезагрузки страницы.

    Публичный: именно этим главная страница переключает фильтры. Записи
    в базу здесь нет, поэтому авторизация не нужна.
    """
    view, subject, week_monday = _resolve(request)
    if not view or subject is None:
        raise Http404('Не выбрана группа, пара или преподаватель.')
    return render(
        request, 'schedule/_block.html',
        _block_context(view, subject, week_monday, request),
    )


@require_GET
def picker_list(request):
    """Фрагмент списка для всплывающего окна.

    Лимиты и rate limit — чтобы бот перебором букв не выкачал всю базу
    университета (см. schedule/throttle.py).
    """
    kind = request.GET.get('kind', 'group')
    if kind not in ('group', 'teacher', 'pair'):
        raise Http404('Неизвестный раздел.')

    ip = client_ip(request)
    if not throttle_ok(f'picker:{ip}', PICKER_RATE_LIMIT, PICKER_RATE_WINDOW):
        return HttpResponse(
            '<p class="picker-empty">Слишком много запросов. Попробуйте позже.</p>',
            content_type='text/html; charset=utf-8',
            status=429,
        )

    query = request.GET.get('q', '').strip()[:100]

    if kind == 'teacher':
        # only(): в списке нужны только четыре поля. Телефон и почта
        # преподавателя в публичном виде не показываются — незачем их читать.
        items = Teacher.objects.filter(active=True).only(
            'pk', 'full_name', 'position', 'department')
        if query:
            items = items.filter(full_name__icontains=query)
        items = items.order_by('full_name')[:PICKER_LIMIT]
        rows = [{
            'id': t.pk,
            'label': t.full_name,
            'meta': ' · '.join(x for x in (t.position, t.department) if x),
        } for t in items]
    elif kind == 'pair':
        rows = [{
            'id': lt.pk,
            'label': f'{lt.number}{translate(request, "pair_of")}',
            'meta': f'{lt.start:%H:%M} – {lt.end:%H:%M}',
        } for lt in LessonTime.objects.order_by('number')]
    else:
        items = Group.objects.all()
        if query:
            items = items.filter(
                Q(name__icontains=query) | Q(specialty__icontains=query)
            )
        items = items.order_by('-course', 'name')[:PICKER_LIMIT]
        rows = [{
            'id': g.pk,
            'label': g.name,
            'meta': f'{g.course} {translate(request, "course")}'
                    + (f' · {g.specialty}' if g.specialty else ''),
        } for g in items]

    titles = {
        'group': 'menu_groups',
        'teacher': 'menu_teachers',
        'pair': 'menu_pairs',
    }
    return render(request, 'schedule/_picker_list.html', {
        'kind': kind, 'rows': rows, 'query': query,
        'picker_title': translate(request, titles[kind]),
    })


# --------------------------------------------- старые адреса (работают и дальше)

def group_detail(request, pk):
    return redirect(f'/?view=group&id={pk}{_week_suffix(request)}')


def teacher_detail(request, pk):
    return redirect(f'/?view=teacher&id={pk}{_week_suffix(request)}')


def room_detail(request, pk):
    return redirect(f'/?view=room&id={pk}{_week_suffix(request)}')


def _week_suffix(request):
    """Значение week для редиректа — только валидное.

    Сырое из GET в Location не подставляем: мусорный параметр не должен
    попадать в адрес, который потом увидит пользователь.
    """
    raw = request.GET.get('week')
    if not raw:
        return ''
    return f'&week={_iso_week(_parse_week(raw))}'


# ------------------------------------------------------------------ день

def day_view(request):
    today = timezone.localdate()
    day_number = _int_or_none(request.GET.get('day')) or today.isoweekday()
    day_number = max(1, min(day_number, len(settings.WEEKDAYS)))
    week_monday = _parse_week(request.GET.get('week'))
    day_date = week_monday + timedelta(days=day_number - 1)

    parity = 1 if week_monday.isocalendar()[1] % 2 else 2
    lessons = list(
        Lesson.objects.filter(day=day_number)
        .filter(Q(week_parity=0) | Q(week_parity=parity))
        .select_related(
            'subject', 'lesson_type', 'teacher', 'room', 'lesson_time', 'group',
        )
        .order_by('lesson_time__number', 'group__name')
    )

    subs = {
        sub.lesson_id: sub
        for sub in Substitution.objects.filter(
            lesson__in=lessons, date=day_date,
        ).select_related('teacher', 'room')
    }

    rows = []
    for lesson in lessons:
        sub = subs.get(lesson.pk)
        rows.append({
            'lesson': lesson,
            'subject': lesson.subject,
            'subject_label': lesson.subject.label if lesson.subject
                             else (lesson.raw_text or '—'),
            'group': lesson.group,
            'color': lesson.subject.color if lesson.subject else '#f5f5f5',
            'type': lesson.lesson_type.name if lesson.lesson_type else '',
            'teacher': sub.effective_teacher if sub else lesson.teacher,
            'room': sub.effective_room if sub else lesson.room,
            'changed': bool(sub),
            'reason': sub.reason if sub else '',
        })

    return render(
        request, 'schedule/day.html',
        {
            'title': f'Расписание на {day_date:%d.%m.%Y}',
            'day_date': day_date,
            'day_name': DAY_NAMES.get(day_number, ''),
            'day_number': day_number,
            'week_monday': week_monday,
            'week_param': _iso_week(week_monday),
            'this_monday': _monday_of(today),
            'rows': rows,
            'today': today,
            'selected_group': request.GET.get('group', ''),
            'day_tabs': [{
                'num': num,
                'short': DAY_NAMES[num][:2],
                'date_label': f'{week_monday + timedelta(days=num - 1):%d.%m}',
            } for num, _ in settings.WEEKDAYS],
        },
    )


# --------------------------------------------------------------- импорт

def _tmp_dir():
    path = settings.IMPORT_TMP_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _discard_upload(name):
    if not name:
        return
    try:
        (_tmp_dir() / name).unlink(missing_ok=True)
    except OSError:
        pass


def _store_upload(upload):
    """Сохранить загруженный файл во временную папку.

    Расширение берётся только из белого списка, а не из имени файла: иначе
    можно было бы положить на диск .py или .html. Содержимое тоже сверяется с
    расширением по магическим байтам — MIME из браузера не проверяем,
    он подделывается одной строкой.
    """
    suffix = upload_guard.allowed_suffix(upload.name)
    if not suffix:
        raise ValueError(
            'Поддерживаются только файлы .xlsx, .xlsm, .xls и .csv.'
        )

    data = upload.read()
    if not upload_guard.looks_like(suffix, data):
        raise ValueError(
            'Содержимое файла не соответствует расширению. '
            'Проверьте, что это настоящая таблица Excel или CSV.'
        )
    if suffix in ('.xlsx', '.xlsm'):
        upload_guard.guard_zip(data)

    _clear_stale_uploads()
    name = f'{uuid4().hex}{suffix}'
    (_tmp_dir() / name).write_bytes(data)
    return name


def _load_upload(name):
    path = _tmp_dir() / name
    if not path.exists():
        return None
    return path.read_bytes(), path.name


def _clear_stale_uploads(max_age_hours=6):
    cutoff = time.time() - max_age_hours * 3600
    for path in _tmp_dir().glob('*'):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def _log_import(request, filename, result):
    """Запись в журнал: кто, когда и сколько записей затронул.

    Логин дублируется строкой, чтобы запись осталась читаемой, даже если
    учётную запись потом удалят (у user стоит SET_NULL).
    """
    ImportLog.objects.create(
        user=request.user if request.user.is_authenticated else None,
        username=getattr(request.user, 'username', '')[:150],
        filename=(filename or '')[:255],
        created_count=result.created,
        updated_count=result.updated,
        skipped_count=result.skipped,
        summary=result.summary()[:300],
    )


@staff_member_required
def import_view(request):
    """Загрузка файла: сначала проверка, потом применение.

    Только для сотрудников: импорт переписывает расписание всего
    университета, а раньше сюда мог зайти кто угодно.
    """
    result = None
    uploaded_name = ''
    checked_only = True
    stored = request.session.get('import_upload')

    if request.method == 'POST':
        uploaded = request.FILES.get('file')
        if uploaded is not None:
            if uploaded.size > MAX_UPLOAD_BYTES:
                result = ImportResult(errors=[
                    f'Файл больше {MAX_UPLOAD_BYTES // (1024 * 1024)} МБ. '
                    'Разбейте расписание на части.'
                ])
                stored = None
            else:
                _discard_upload(request.session.get('import_upload'))
                try:
                    stored = _store_upload(uploaded)
                except ValueError as exc:
                    result = ImportResult(errors=[str(exc)])
                    stored = None
                if stored:
                    request.session['import_upload'] = stored
                    # исходное имя держим отдельно: на диске файл уже под
                    # uuid, а в журнале должно быть видно, что загрузили
                    request.session['import_filename'] = uploaded.name[:255]
                    uploaded_name = uploaded.name
                else:
                    uploaded_name = ''
        elif not stored:
            result = ImportResult(errors=['Файл не выбран.'])
            stored = None
        else:
            uploaded_name = request.session.get('import_filename', '') \
                or Path(stored).name

        if stored and result is None:
            apply_now = 'apply' in request.POST
            checked_only = not apply_now
            payload = _load_upload(stored)
            if payload is None:
                result = ImportResult(errors=[
                    'Проверенный файл устарел, загрузите его заново.'
                ])
                request.session.pop('import_upload', None)
                request.session.pop('import_filename', None)
                _discard_upload(stored)
                stored = None
            else:
                data, filename = payload
                if apply_now:
                    # один импорт за раз: SQLite не выдержит параллельной записи,
                    # второй запрос получит внятный отказ вместо 500
                    try:
                        with exclusive_import_lock(_tmp_dir()):
                            result = import_workbook(data, filename, dry_run=False)
                    except ImportLocked as exc:
                        result = ImportResult(errors=[str(exc)])
                else:
                    result = import_workbook(data, filename, dry_run=True)
                if apply_now and result.ok:
                    request.session.pop('import_upload', None)
                    request.session.pop('import_filename', None)
                    _discard_upload(stored)
                    _log_import(request, uploaded_name, result)
                    messages.success(request, f'Импорт завершён: {result.summary}.')
                    return redirect(f"{reverse('schedule:import')}?ok=1")

    return render(
        request, 'schedule/import.html',
        {
            'result': result,
            'uploaded_name': uploaded_name,
            'checked_only': checked_only,
            'can_apply': bool(stored) and result is not None and result.ok,
            'just_imported': request.GET.get('ok') == '1',
            'has_lesson_times': LessonTime.objects.exists(),
            'existing_count': Lesson.objects.count(),
        },
    )


# -------------------------------------------------------------- выгрузка

@staff_member_required
def import_template(request):
    """Пустой шаблон для заполнения. Взят рядом с импортом — только дляstaff."""
    return _csv_response(export_template_csv(), 'template.csv')


@require_GET
def export_csv_view(request):
    """Выгрузка всего расписания в CSV.

    Публичная: это ссылка «Выгрузка» в меню, ею пользуются студенты.
    Дамп расписания университета — не персональные данные.
    """
    return _csv_response(export_csv(), 'timetable.csv')


@staff_member_required
@require_GET
def export_xlsx_view(request):
    """Готовая таблица Excel в стиле «Расписание_как_на_фото.xlsx».

    Только для сотрудников: книгу собирает openpyxl, это дорогая операция,
    и её удобно готовить себе, а не отдавать наружу каждому клику.
    """
    view, subject, week_monday = _resolve(request)
    queryset = _queryset_for(view, subject, week_monday) if subject else Lesson.objects.none()
    return build_xlsx(
        queryset,
        subtitle=_title_for(view, subject, request) if subject else '',
        week_monday=week_monday,
        request=request,
    )


def build_xlsx(queryset, subtitle='', week_monday=None, request=None):
    import io

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    thin = Side(style='thin', color='A6A6A6')

    def argb(hex_color):
        value = (hex_color or '').lstrip('#')
        return 'FF' + value if len(value) == 6 else value

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Расписание'

    grid = build_grid(queryset=queryset,
                      week_monday=week_monday or _monday_of(timezone.localdate()),
                      request=request)
    lesson_times = grid['lesson_times']

    sheet['A1'] = 'Расписание'
    sheet['A1'].font = Font(bold=True, size=28)
    sheet['A1'].fill = PatternFill('solid', fgColor=argb('#e7e6e6'))
    sheet['A2'] = subtitle or '—'
    sheet['A2'].font = Font(bold=True, size=14)

    header_font = Font(bold=True, size=10)
    header_align = Alignment(horizontal='center', vertical='center', wrap_text=True)
    cell_align = Alignment(horizontal='center', vertical='center', wrap_text=True)
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    # угол: пустая ячейка с серой заливкой, как в шаблоне
    corner = sheet['A3']
    corner.fill = PatternFill('solid', fgColor=argb('#e7e6e6'))
    corner.border = border

    for column, lesson_time in enumerate(lesson_times, start=2):
        cell = sheet.cell(row=3, column=column)
        cell.value = f'{lesson_time.number}\n{lesson_time.start:%H:%M} - {lesson_time.end:%H:%M}'
        cell.fill = PatternFill('solid', fgColor=argb('#e7e6e6'))
        cell.font = header_font
        cell.alignment = header_align
        cell.border = border

    for row_index, row in enumerate(grid['rows'], start=4):
        head = sheet.cell(row=row_index, column=1, value=row['name'])
        head.fill = PatternFill('solid', fgColor=argb('#f2f2f2'))
        head.font = Font(bold=True, size=11)
        head.alignment = header_align
        head.border = border

        for column, lesson_time in enumerate(lesson_times, start=2):
            items = row['cells'].get(lesson_time.pk, [])
            cell = sheet.cell(row=row_index, column=column)
            if items:
                blocks = []
                for item in items:
                    parts = [item['subject'].name if item['subject']
                             else item['subject_label']]
                    if item['type']:
                        parts.append(item['type'])
                    if item['room']:
                        parts.append(f'Ауд. {item["room"]}')
                    if item['teacher']:
                        parts.append(str(item['teacher']))
                    if item['group']:
                        parts.append(str(item['group']))
                    blocks.append('\n'.join(parts))
                cell.value = '\n\n'.join(blocks)
            cell.font = Font(size=10, bold=bool(items))
            cell.alignment = cell_align
            cell.border = border
        sheet.row_dimensions[row_index].height = 62

    sheet.column_dimensions['A'].width = 10
    for column in range(2, len(lesson_times) + 2):
        sheet.column_dimensions[get_column_letter(column)].width = 18
    sheet.row_dimensions[1].height = 34
    sheet.row_dimensions[3].height = 34
    for row_index in range(4, 4 + len(grid['rows'])):
        sheet.row_dimensions[row_index].height = 62

    stream = io.BytesIO()
    workbook.save(stream)
    stream.seek(0)
    response = HttpResponse(
        stream.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = 'attachment; filename="raspisanie.xlsx"'
    return response


def _csv_response(text, filename):
    # BOM, чтобы Excel открыл кириллицу без плясок с кодировкой
    response = HttpResponse('\ufeff' + text, content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response