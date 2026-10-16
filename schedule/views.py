"""Представления: главное меню с блоком расписания, день, выгрузка."""

import time
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.cache import cache
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods

from . import captcha, i18n
from .contacts import UNIVERSITY
from .exports import export_csv
from .formula_guard import force_text_cell
from .throttle import client_ip, throttle_ok
from .grid import (
    _parity,
    build_grid,
    build_substitution_grid,
    grid_has_content,
    merge_combined,
    monday_of,
    parse_week,
    week_param,
)
from .i18n import (
    LANGS,
    day_short,
    get_language,
    set_language_cookie,
    translate,
    weekday_name_ru,
)
from .models import (
    ContactMessage,
    Group,
    Lesson,
    LessonTime,
    Room,
    Substitution,
    Teacher,
)

VIEWS = ('group', 'pair', 'teacher', 'room', 'substitution')

# Сколько записей отдаёт живой поиск /list/ за один раз. Без среза бот
# перебором букв выкачал бы всю базу университета.
PICKER_LIMIT = 50
# Не больше 30 запросов в минуту с одного адреса (см. schedule/throttle.py)
PICKER_RATE_LIMIT = 30
PICKER_RATE_WINDOW = 60
# Обращений через форму «Связаться с нами» — 5 в час с одного адреса. Captcha
# от спама не защищает: пример арифметический, ответы перебираются за секунды,
# а каждая удачная отправка — новая строка в ContactMessage без уведомления
# модератору. Лимит считается до проверки полей, иначе бот всё равно успевает
# перебрать примеры и набить базу.
CONTACT_RATE_LIMIT = 5
CONTACT_RATE_WINDOW = 3600
# Выгрузка книги Excel. Книга собирается openpyxl — замер на всей базе
# (6 групп, 120 занятий): около 100 мс и 1.3 МБ против 28 мс у CSV, то есть
# вчетверо дороже. Сама по себе это не нагрузка, но открытый эндпоинт без
# предела зовут ботом: 30 книг в минуту — это 3 секунды CPU, переживаются.
EXPORT_RATE_LIMIT = 30
EXPORT_RATE_WINDOW = 60
# Расписание между правками не меняется, а одноклассники нажимают «Excel»
# почти одновременно. Минуты хватает: книга после импорта устареет максимум
# на минуту, а сервер перестаёт пересобирать одно и то же десяток раз.
EXPORT_CACHE_SECONDS = 60


# ---------------------------------------------------------------- служебное
#
# Логика недели живёт в grid.py (monday_of, iso_week, parse_week, week_param).
# Здесь она была продублирована приватными копиями с подчёркиванием, и две
# реализации одной строки расписания разъезжаются при первой же правке —
# см. задачу 13 аудита. Импорт выше: используются публичные имена.


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
        return '', None, parse_week(request.GET.get('week'))

    week_monday = parse_week(request.GET.get('week'))
    pk = _int_or_none(request.GET.get('id'))

    if view == 'group':
        subject = get_object_or_404(Group, pk=pk) if pk else None
    elif view == 'teacher':
        subject = get_object_or_404(Teacher, pk=pk) if pk else None
    elif view == 'room':
        subject = get_object_or_404(Room, pk=pk) if pk else None
    elif view == 'substitution':
        # Замена — про человека, поэтому выбирается преподаватель: список
        # тот же, что у вкладки «Преподаватели», но показываются только его
        # замены — в обе стороны: кого заменили и кого он заменяет.
        subject = get_object_or_404(Teacher, pk=pk) if pk else None
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
    if view == 'substitution':
        return subject.full_name
    return f'{subject.name}'


# ------------------------------------------------------------------ блок

def _build_grid(view, subject, week_monday, request=None):
    """Общая сборка сетки.

    Для «Пар» включаем альтернативную раскладку, для кабинета — склейку
    подряд идущих пар одной лекции в одну ячейку. В расписании группы
    две одинаковые пары подряд означают две разные пары (так его ведёт вуз),
    а в расписании кабинета — одну лекцию, занятую на два-три часа.
    """
    if view == 'substitution':
        return build_substitution_grid(week_monday=week_monday,
                                       request=request,
                                       teacher=subject)
    return build_grid(queryset=_queryset_for(view, subject, week_monday),
                      week_monday=week_monday, request=request,
                      cards=(view == 'pair'),
                      merge_runs=(view == 'room'))


def _block_context(view, subject, week_monday, request=None):
    grid = _build_grid(view, subject, week_monday, request)
    today = timezone.localdate()
    context = {
        'view': view,
        'selected': subject,
        # Окно замен не про объект, поэтому «выбранного» у него нет: пустая
        # строка вместо номера, иначе шаблон искал бы несуществующий ключ.
        'selected_id': (subject.pk if hasattr(subject, 'pk') else ''),
        'title': _title_for(view, subject, request),
        'grid': grid,
        'grid_is_empty': not grid_has_content(grid),
        'week_monday': week_monday,
        'week_label': (
            f'{week_monday:%d.%m} – {week_monday + timedelta(days=5):%d.%m.%Y}'
        ),
        'week_param': week_param(week_monday),
        'prev_week': week_param(week_monday - timedelta(weeks=1)),
        'next_week': week_param(week_monday + timedelta(weeks=1)),
        'is_current_week': week_monday == monday_of(today),
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
    Частота отправок ограничена (CONTACT_RATE_LIMIT): captcha от спама
    не спасает, а лимит на таблицу ContactMessage не задан.
    """
    if request.method != 'POST':
        return redirect('schedule:university')

    # Лимит проверяем первым, до разбора формы: иначе бот успевает и перебрать
    # примеры, и оставить запись в базе — captcha от такого не спасает.
    ip = client_ip(request)
    if not throttle_ok(f'contact:{ip}', CONTACT_RATE_LIMIT, CONTACT_RATE_WINDOW):
        context = _menu_context(request=request)
        context.update({
            'uni': UNIVERSITY,
            'sent': False,
            'form_errors': ['rate'],
            'rate_limited': True,
            'captcha_token': captcha.current(request.session)[0],
        })
        return render(request, 'schedule/university.html', context, status=429)

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
        week_monday = monday_of(today)
    context = {
        'week_monday': week_monday,
        'week_param': week_param(week_monday),
        'week_label': f'{week_monday:%d.%m} – {week_monday + timedelta(days=5):%d.%m.%Y}',
        'prev_week': week_param(week_monday - timedelta(weeks=1)),
        'next_week': week_param(week_monday + timedelta(weeks=1)),
        'is_current_week': week_monday == monday_of(today),
        'today': today,
        'today_short': translate(request, f'dow_{today.isoweekday()}'),
        'group_count': Group.objects.count(),
        'teacher_count': Teacher.objects.filter(active=True).count(),
        'room_count': Room.objects.count(),
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
    if kind not in ('group', 'teacher', 'pair', 'room', 'substitution'):
        raise Http404('Неизвестный раздел.')

    ip = client_ip(request)
    if not throttle_ok(f'picker:{ip}', PICKER_RATE_LIMIT, PICKER_RATE_WINDOW):
        return HttpResponse(
            '<p class="picker-empty">Слишком много запросов. Попробуйте позже.</p>',
            content_type='text/html; charset=utf-8',
            status=429,
        )

    query = request.GET.get('q', '').strip()[:100]

    # Фильтр по курсу. Курсы берутся из реальных групп: выдумывать кнопку
    # «5 курс», которой нет в базе, незачем — при появлении пятого курса
    # кнопка появится сама.
    raw_course = request.GET.get('course', '').strip()
    course = None
    # order_by() обязателен. В Meta у группы задана сортировка по course и
    # name, и Django молча добавляет name в SELECT при values_list() с
    # distinct(). Тогда DISTINCT считает пары «курс + название», и вместо
    # четырёх курсов приходит сорок две строки — по одной на группу, то есть
    # фильтр показывал по десять кнопок «1 курс». Сортировку надо снять, а
    # нужную по курсу сделать явно.
    courses = sorted(
        Group.objects.order_by('course').values_list('course', flat=True)
        .distinct()
    )
    if raw_course.isdigit():
        course = int(raw_course)
        if course not in courses:
            course = None
    course_word = translate(request, 'course')
    places_word = translate(request, 'places')

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
    elif kind == 'substitution':
        # Список преподавателей, но с числом их замен за выбранную неделю.
        # Без счётчика список неотличим от вкладки «Преподаватели», и человек
        # не понимает, что вообще выбирает.
        items = Teacher.objects.filter(active=True)
        if query:
            items = items.filter(full_name__icontains=query)
        items = items.order_by('full_name')[:PICKER_LIMIT]
        ids = [t.pk for t in items]

        week_monday = parse_week(request.GET.get('week'))
        week_end = week_monday + timedelta(days=len(settings.WEEKDAYS) - 1)
        in_week = Substitution.objects.filter(date__gte=week_monday,
                                              date__lte=week_end)
        # Считаем обе стороны замены: где преподаватель ведёт занятие и где
        # его заменили. Сумма — сколько записей его касается.
        counts = {row['teacher_id']: row['n'] for row in
                  in_week.filter(teacher_id__in=ids)
                  .values('teacher_id').annotate(n=Count('id'))}
        for row in (in_week.filter(lesson__teacher_id__in=ids)
                    .values('lesson__teacher_id')
                    .annotate(n=Count('id'))):
            counts[row['lesson__teacher_id']] = (
                counts.get(row['lesson__teacher_id'], 0) + row['n'])

        subs_word = translate(request, 'subs_word')
        rows = []
        for t in items:
            meta = ' · '.join(x for x in (t.position, t.department) if x)
            n = counts.get(t.pk, 0)
            if n:
                marked = f'{n} {subs_word}'
                meta = f'{meta} · {marked}' if meta else marked
            rows.append({'id': t.pk, 'label': t.full_name, 'meta': meta})
    elif kind == 'pair':
        # Курс у пары в базе не хранится: LessonTime — это номер и интервал,
        # а он у вуза один на всех. Связь курса с парой восстанавливается по
        # фактическим занятиям: пара попадает в курс тогда, когда хотя бы
        # одна группа этого курса в ней занимается. Выдумывать отдельное
        # расписание звонков для курса нельзя — таких данных нет.
        used = (Lesson.objects.filter(group__isnull=False)
                .values('group__course', 'lesson_time__pk', 'lesson_time__number',
                        'lesson_time__start', 'lesson_time__end')
                .annotate(n=Count('id'))
                .order_by('group__course', 'lesson_time__number'))
        if course is not None:
            used = [r for r in used if r['group__course'] == course]

        rows = []
        previous = None
        for item in used:
            # Заголовок курса нужен только когда курсов несколько: если выбран
            # один, повторять его над единственным блоком незачем.
            first_of_course = (course is None
                               and item['group__course'] != previous)
            previous = item['group__course']
            label = f'{item["lesson_time__number"]}{translate(request, "pair_of")}'
            meta = (f'{item["lesson_time__start"]:%H:%M} – '
                    f'{item["lesson_time__end"]:%H:%M}')
            # Поиск по паре раньше ничего не делал: строка была, а список
            # не менялся, и человек считал, что поиск сломан. Теперь ищет и
            # по номеру, и по времени — «9:00» находит пару, которая в это
            # время начинается.
            if query and query.lower() not in f'{label} {meta}'.lower():
                continue
            rows.append({
                'id': item['lesson_time__pk'],
                'label': label,
                'meta': meta,
                'course': item['group__course'],
                'course_label': f'{item["group__course"]} {course_word}',
                'new_course': first_of_course,
            })
    elif kind == 'room':
        # Кабинеты. Курса у аудитории нет, поэтому фильтр по курсу здесь не
        # показывается: кабинетов два десятка, они помещаются и без него.
        # В строке — корпус и вместимость, если их завели: по одному номеру
        # «201» лекционную аудиторию от учебной не отличить.
        items = Room.objects.all()
        if query:
            items = items.filter(
                Q(name__icontains=query) | Q(building__icontains=query)
            )
        items = items.order_by('building', 'name')[:PICKER_LIMIT]
        rows = [{
            'id': room.pk,
            'label': room.name,
            'meta': ' · '.join(x for x in (
                room.building,
                (f'{room.capacity} {places_word}' if room.capacity else ''),
            ) if x),
        } for room in items]
    else:
        items = Group.objects.all()
        if course is not None:
            items = items.filter(course=course)
        if query:
            items = items.filter(
                Q(name__icontains=query) | Q(specialty__icontains=query)
            )
        items = items.order_by('-course', 'name')[:PICKER_LIMIT]
        rows = [{
            'id': g.pk,
            'label': g.name,
            'meta': f'{g.course} {course_word}'
                    + (f' · {g.specialty}' if g.specialty else ''),
        } for g in items]

    titles = {
        'group': 'menu_groups',
        'teacher': 'menu_teachers',
        'pair': 'menu_pairs',
        'room': 'menu_rooms',
        'substitution': 'menu_substitutions',
    }
    return render(request, 'schedule/_picker_list.html', {
        'kind': kind, 'rows': rows, 'query': query,
        'courses': courses, 'course': course,
        # У преподавателей курса нет: фильтр по нему был бы пустой набором
        # кнопок. Окна «Групп» и «Пар» — разные разделы об одном и том же
        # расписании, фильтр у них общий и одинаково оформленный.
        'show_course_filter': kind in ('group', 'pair'),
        'course_word': course_word,
        'all_courses': translate(request, 'all_courses'),
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
    return f'&week={week_param(parse_week(raw))}'


# ------------------------------------------------------------------ день

def day_view(request):
    today = timezone.localdate()
    day_number = _int_or_none(request.GET.get('day')) or today.isoweekday()
    day_number = max(1, min(day_number, len(settings.WEEKDAYS)))
    week_monday = parse_week(request.GET.get('week'))
    day_date = week_monday + timedelta(days=day_number - 1)

    parity = _parity(week_monday)
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
            'type_color': lesson.lesson_type.color if lesson.lesson_type else '',
            'teacher': sub.effective_teacher if sub else lesson.teacher,
            'room': sub.effective_room if sub else lesson.room,
            'changed': bool(sub),
        })

    # Объединённые группы — одно занятие на несколько групп. Слияние идёт
    # внутри одной пары: между разными парами сливать нечего.
    by_slot = {}
    for row in rows:
        by_slot.setdefault(row['lesson'].lesson_time_id, []).append(row)
    rows = [row for slot_rows in by_slot.values() for row in merge_combined(slot_rows)]

    return render(
        request, 'schedule/day.html',
        {
            'title': f'Расписание на {day_date:%d.%m.%Y}',
            'day_date': day_date,
            'day_name': weekday_name_ru(day_number),
            'day_number': day_number,
            'week_monday': week_monday,
            'week_param': week_param(week_monday),
            'this_monday': monday_of(today),
            'rows': rows,
            'today': today,
            'selected_group': request.GET.get('group', ''),
            'day_tabs': [{
                'num': num,
                # Не обрезка названия дня: обрезка давала «Че», «Пя», «По» и
                # «Су». Короткие формы лежат в словаре — как в остальном
                # интерфейсе, так и в выгрузках.
                'short': day_short(num, request),
                'date_label': f'{week_monday + timedelta(days=num - 1):%d.%m}',
            } for num, _ in settings.WEEKDAYS],
        },
    )


# -------------------------------------------------------------- выгрузка

@require_GET
def export_csv_view(request):
    """Выгрузка всего расписания в CSV.

    Публичная: это ссылка «Выгрузка» в меню, ею пользуются студенты.
    Дамп расписания университета — не персональные данные.
    """
    return _csv_response(export_csv(), 'timetable.csv')


@require_GET
def export_xlsx_view(request):
    """Готовая таблица Excel в стиле «Расписание_как_на_фото.xlsx».

    Публичная, как и CSV. Закрывать её смысла не было: в книге ровно те же
    данные, что и в публичном CSV, — группа, предмет, аудитория, преподаватель.
    Телефона и почты там нет ни там, ни там.

    Закрывать пришлось бы только от нагрузки, и это делается не запретом, а
    двумя средствами (оценка замера — в README, раздел «Выгрузка Excel»):

    * rate limit на IP — книга собирается openpyxl примерно вчетверо дороже
      CSV, и без ограничения бот способен положить сервер;
    * кэш на минуту — расписание между правками не меняется, а одноклассники
      запросят одну и ту же книгу одновременно.
    """
    ip = client_ip(request)
    if not throttle_ok(f'export:{ip}', EXPORT_RATE_LIMIT, EXPORT_RATE_WINDOW):
        return _too_many_exports()

    view, subject, week_monday = _resolve(request)
    # Язык входит в ключ: названия дней и «пара N» в книгу подставляются
    # переводами из cookie, и без него кэш отдал бы русскую книгу тому, кто
    # выбрал узбекский.
    key = 'xlsx:{}:{}:{}:{}'.format(
        view, subject.pk if subject else 0, week_monday, i18n.get_language(request),
    )
    content = cache.get(key)
    if content is None:
        queryset = (
            _queryset_for(view, subject, week_monday) if subject
            else Lesson.objects.none()
        )
        content = build_xlsx(
            queryset,
            subtitle=_title_for(view, subject, request) if subject else '',
            week_monday=week_monday,
            request=request,
        ).content
        cache.set(key, content, EXPORT_CACHE_SECONDS)
    return _xlsx_response(content)


def _too_many_exports():
    """Ответ на превышение лимита.

    Отдельная функция, а не тело прямо во вьюхе, потому что файла отдавать
    нечем: учебные сообщения на русском и узбекском, а сотрудник может
    читать лог на любом языке. Текст короткий и отличается от страницы
    приложения — это ответ транспорту, а не часть интерфейса.
    """
    response = HttpResponse(
        '<!doctype html><meta charset="utf-8"><title>429</title>'
        '<p>Слишком много запросов выгрузки. Подождите минуту и повторите.</p>',
        content_type='text/html; charset=utf-8',
        status=429,
    )
    # Счётчик лимита не должен сам лежать в кэше надолго: иначе после
    # пачки запросов адрес остался бы заблокирован.
    response['Retry-After'] = str(EXPORT_RATE_WINDOW)
    return response


def _xlsx_response(content):
    response = HttpResponse(
        content,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = 'attachment; filename="raspisanie.xlsx"'
    response['Cache-Control'] = 'private, max-age=60'
    return response



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
                      week_monday=week_monday or monday_of(timezone.localdate()),
                      request=request)
    lesson_times = grid['lesson_times']

    # Заголовки и подписи тоже могут прийти «вредными» (название пары
    # берётся из данных), поэтому идут через force_text_cell.
    title = force_text_cell(sheet['A1'], 'Расписание')
    title.font = Font(bold=True, size=28)
    title.fill = PatternFill('solid', fgColor=argb('#e7e6e6'))
    subtitle_cell = force_text_cell(sheet['A2'], subtitle or '—')
    subtitle_cell.font = Font(bold=True, size=14)

    header_font = Font(bold=True, size=10)
    header_align = Alignment(horizontal='center', vertical='center', wrap_text=True)
    cell_align = Alignment(horizontal='center', vertical='center', wrap_text=True)
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    # угол: пустая ячейка с серой заливкой, как в шаблоне
    corner = sheet['A3']
    corner.fill = PatternFill('solid', fgColor=argb('#e7e6e6'))
    corner.border = border

    for column, lesson_time in enumerate(lesson_times, start=2):
        # Номер и время пары приходят из настроек и «=» начаться не могут,
        # но идут тем же помощником: единый путь дешевле, чем оговорка
        # «тут безопасно» в следующем коде.
        cell = force_text_cell(
            sheet.cell(row=3, column=column),
            f'{lesson_time.number}\n{lesson_time.start:%H:%M} - {lesson_time.end:%H:%M}',
        )
        cell.fill = PatternFill('solid', fgColor=argb('#e7e6e6'))
        cell.font = header_font
        cell.alignment = header_align
        cell.border = border

    for row_index, row in enumerate(grid['rows'], start=4):
        # Название дня — из настроек, но идёт тем же путём, что и данные:
        # один и тот же помощник на все ячейки, чтобы не забыть про одну.
        head = force_text_cell(
            sheet.cell(row=row_index, column=1), row['name'])
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
                # Начало блока решает всё: Excel считает формулой ячейку,
                # если первая строка начинается с «=» или «+». Проверяется
                # весь склеенный текст, а не каждая часть по отдельности.
                force_text_cell(cell, '\n\n'.join(blocks))
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
    # Заголовки ответа собирает _xlsx_response: раньше строка с типом файла
    # и вложением была продублирована здесь и во вьюхе, и при правке одной
    # забывали другую (см. задачу 13 про два источника истины).
    return _xlsx_response(stream.getvalue())


def _csv_response(text, filename):
    # BOM, чтобы Excel открыл кириллицу без плясок с кодировкой
    response = HttpResponse('\ufeff' + text, content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
