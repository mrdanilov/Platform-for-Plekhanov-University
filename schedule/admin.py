"""Админка расписания.

Полный контроль над данными: группы, пары, преподаватели, предметы,
аудитории, типы занятий, время пар, само расписание и замены.
"""

from datetime import timedelta

from django.contrib import admin, messages
from django.db import IntegrityError
from django.shortcuts import render
from django.urls import path
from django.utils.html import format_html, mark_safe

from .drafts import (CannotUndo, build_draft_grid, pending_drafts,
                     publish, restore)
from .grid import parse_week, week_param
from .i18n import day_short as day_abbrev
from .spans import CannotStretch, changed_extensions, collapse, stretch
from .models import (  # noqa: F401  (Subject нужен для formfield_overrides)
    ContactMessage,
    Group,
    Lesson,
    LessonDraft,
    DraftPublication,
    LessonTime,
    LessonType,
    Room,
    Subject,
    Substitution,
    Teacher,
)
from .widgets import ColorPickerWidget

# Импорт в конце — намеренно. Модуль raw_lessons подменяет get_urls у
# admin.site, поэтому должен загрузиться после того, как admin.site
# создан. Если перенести выше, подмена не применится: Django кэширует
# список URL при первом обращении к админке.
from . import raw_lessons  # noqa: E402,F401  (нужен ради подмены URL)

admin.site.site_header = 'Панель управления расписанием'
admin.site.site_title = 'Расписание'
admin.site.index_title = 'Данные расписания'
admin.site.enable_nav_sidebar = True


@admin.register(Teacher)
class TeacherAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'position', 'department', 'lesson_count', 'active')
    list_filter = ('active', 'department')
    search_fields = ('full_name', 'position', 'department')
    list_editable = ('active',)
    ordering = ('full_name',)
    fieldsets = (
        (None, {'fields': ('full_name', 'position', 'department')}),
        ('Контакты', {'fields': ('phone', 'email'), 'classes': ('collapse',)}),
        ('Прочее', {'fields': ('active',)}),
    )

    @admin.display(description='Занятий')
    def lesson_count(self, obj):
        return obj.lessons.count()


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'course', 'specialty', 'lesson_count')
    list_filter = ('course',)
    search_fields = ('name', 'specialty')
    ordering = ('-course', 'name')

    @admin.display(description='Занятий в неделю')
    def lesson_count(self, obj):
        return obj.lessons.count()


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'short_name', 'swatch', 'lesson_count')
    search_fields = ('name', 'short_name')
    ordering = ('name',)
    fieldsets = (
        (None, {
            'fields': ('name', 'short_name'),
            'description': (
                '<p><b>Предмет должен существовать ровно один раз.</b> Если '
                'название уже есть — выберите его из выпадающего списка, а не '
                'вписывайте заново. Два предмета с одинаковым смыслом '
                '(«Философия» и «Philosophy Leksiya») разъедутся по таблице: '
                'у каждого свой цвет, и занятия одного предмета окажутся в '
                'разных блоках.</p>'
            ),
        }),
        ('Как выглядит в расписании', {'fields': ('color',)}),
    )

    class Media:
        css = {'all': ('admin/color_picker.css',)}

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        """Виджет выбора цвета ставится только на само поле «Цвет».

        Именно этот метод, а не ``formfield_overrides``: там ключом стоит
        класс поля, а ``color`` — обычный ``CharField``, как и ``name`` с
        ``short_name``. Пипетка прилипла бы ко всем трём строковым полям.
        Метод ``formfield_callback`` тоже не подходит: Django его не
        вызывает, ``get_form`` идёт через
        ``partial(self.formfield_for_dbfield, ...)``.
        """
        if db_field.name == 'color':
            kwargs['widget'] = ColorPickerWidget
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    @admin.display(description='Цвет')
    def swatch(self, obj):
        return format_html(
            '<span style="display:inline-block;width:44px;height:18px;'
            'border:1px solid #bbb;border-radius:3px;background:{}"></span>',
            obj.color,
        )

    @admin.display(description='Занятий')
    def lesson_count(self, obj):
        return obj.lessons.count()


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ('name', 'building', 'capacity', 'lesson_count')
    list_filter = ('building',)
    search_fields = ('name', 'building')
    ordering = ('building', 'name')

    @admin.display(description='Занятий')
    def lesson_count(self, obj):
        return obj.lessons.count()


@admin.register(LessonType)
class LessonTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'swatch', 'lesson_count')
    search_fields = ('name',)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        # Пипетка, как у предметов: сотруднице не нужно знать, что такое HEX.
        # Именно экземпляр, а не класс: у формы поле уже собрано, и Django
        # больше не создаёт виджет сам.
        field = form.base_fields.get('color')
        if field is not None and not isinstance(field.widget, ColorPickerWidget):
            field.widget = ColorPickerWidget()
        return form

    @admin.display(description='Цвет')
    def swatch(self, obj):
        return format_html(
            '<span style="display:inline-block;width:44px;height:18px;'
            'border:1px solid var(--line);border-radius:3px;'
            'background:{}"></span>', obj.color or '#f5f5f5')

    @admin.display(description='Занятий')
    def lesson_count(self, obj):
        return obj.lessons.count()


@admin.register(LessonTime)
class LessonTimeAdmin(admin.ModelAdmin):
    """Пары по времени: общий справочник для всего расписания.

    Раньше здесь стоял запрет на добавление, пока существует хоть одна пара.
    Смысл был в том, чтобы список не засоряли, но на деле это ломало
    реальную работу: учебный план может не совпадать с уже заведёнными
    парами, и новую пару было не завести. Часы в вузе свои, поэтому
    добавлять и править время нужно свободно.

    Удаление не сломано: у ``Lesson.lesson_time`` стоит ``on_delete=PROTECT``,
    так что пару, на которую ссылаются занятия, удалить не даст — сработает
    защита на уровне базы, с понятным сообщением.
    """

    list_display = ('number', 'start', 'end', 'lessons_count')
    list_editable = ('start', 'end')   # время правится прямо в списке
    list_display_links = ('number',)   # клик по номеру открывает карточку
    ordering = ('number',)
    search_fields = ('number',)
    # Подробное объяснение раздела — на странице списка пар. Поле
    # ``verbose_name`` тут намеренно оставлено прежним: «Время пар» нужно
    # в выпадающем списке у занятия, где это подпись ForeignKey, и там она
    # читается верно. Переименование сюда попало бы и туда, и сбило бы
    # смысл поля «Пара» в самом занятии.
    change_list_template = 'admin/lesson_time_changelist.html'
    fieldsets = (
        (None, {
            'fields': ('number', 'start', 'end'),
            'description': (
                '<p><b>Здесь задаётся только время пар</b> — сколько их в '
                'день и по сколько они идут. Это общий справочник, свои '
                'уроки сюда не добавляются.</p>'
                '<p>Чтобы поставить группе занятие на время, откройте '
                '<a href="../lesson/">Занятия</a> и выберите там поле '
                '<b>«Пара»</b> из выпадающего списка — там видно и номер, и '
                'часы, например «3 пара: 11:00–12:20».</p>'
                '<p>Сюда заходят, только когда нужен новый интервал: '
                'добавили, например, девятую пару 19:00–20:20 — создали её '
                'здесь, и она сразу появилась в списках у занятий.</p>'
                '<p>Правка времени в строке меняет его сразу у всех занятий '
                'на этой паре.</p>'
            ),
        }),
    )

    @admin.display(description='Занятий')
    def lessons_count(self, obj):
        return obj.lessons.count()


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    def get_urls(self):
        """Сводка по парам — служебная страница деканата.

        Отдельная вкладка в меню админки, а не пункт в моделях: считать
        распределение занятий по парам и дням нельзя через фильтры списка,
        это агрегат по всей таблице. Висит на «Занятиях», потому что
        расписание — её содержимое.
        """
        from django.urls import path

        from .analytics import analytics_view

        return [
            path('analytics/',
                 self.admin_site.admin_view(analytics_view),
                 name='schedule_analytics'),
        ] + super().get_urls()

    list_display = (
        'group', 'day_short', 'time_short', 'subject', 'lesson_type',
        'teacher', 'room', 'week_parity',
    )
    list_filter = ('group', 'day', 'week_parity', 'lesson_time', 'subject', 'teacher', 'room')
    search_fields = ('group__name', 'subject__name', 'teacher__full_name', 'room__name')
    autocomplete_fields = ('group', 'subject', 'lesson_type', 'teacher', 'room')
    list_editable = ('lesson_type', 'teacher', 'room')
    list_select_related = ('group', 'subject', 'lesson_time', 'teacher', 'room')
    ordering = ('day', 'lesson_time__number', 'group__name')
    actions = ['shift_day_forward', 'shift_day_back',
               'stretch_by_two', 'stretch_by_three', 'collapse_lessons']
    fieldsets = (
        (None, {'fields': ('group', 'subject', 'lesson_type')}),
        ('Когда и где', {
            'fields': ('day', 'lesson_time', 'room', 'week_parity'),
            'description': (
                '<p><b>Пара</b> — это время из справочника «Время пар», а не '
                'конкретный урок. В списке видно номер и часы: '
                '«3 пара: 11:00–12:20». Нужного интервала нет в списке — '
                'добавьте его в разделе <a href="../lessontime/">Время пар</a>, '
                'и он появится здесь.</p>'
            ),
        }),
        ('Кто ведёт', {'fields': ('teacher',)}),
        ('Прочее', {'classes': ('collapse',), 'fields': ('note', 'raw_text')}),
    )
# Занятия без группы видны в списке, и их не должно быть видно как
    # «None · Вторник · пара 1». Пометка прямо в списке объясняет, что это
    # за запись: импорт не смог разобрать ячейку и сохранил её текст
    # целиком. Иначе сотрудница решит, что это ошибка загрузки, и начнёт
    # перезагружать файл.
    list_display = (
        'group_label', 'day_short', 'time_short', 'subject', 'lesson_type',
        'teacher', 'room', 'week_parity', 'no_group',
    )

    @admin.display(description='Пара занимает')
    def span_label(self, obj):
        """Сколько пар подряд занимает занятие.

        Видно прямо в списке: сотрудница видит результат растяжки, не
        открывая каждое занятие.
        """
        if obj.spans > 1:
            return f'{obj.spans} пары'
        if obj.extend_of_id:
            return 'хвост'
        return '—'

    @admin.display(description='Группа', ordering='group__name')
    def group_label(self, obj):
        """Пустое поле, а не «без группы».

        Пустое выглядит как недозаполненная форма и вызывает вопрос «а где
        группа?» — и это правильный вопрос для обычной записи. Для импортной
        подпись должна говорить, что группа не разобрана, а не отсутствует.
        """
        if obj.group_id is not None:
            return obj.group.name
        return mark_safe('<span style="color:#ba2121">не разобрана</span>')

    @admin.display(boolean=True, description='Без группы')
    def no_group(self, obj):
        return obj.group_id is None

    def has_delete_permission(self, request, obj=None):
        """Удаление через стандартную кнопку запрещено.

        Такой объект хранит в себе текст неразобранной ячейки Excel —
        единственную копию того, что было в файле. Молчаливый обход этой
        защиты оставлен в разделе «Сырые занятия», где удаление идёт через
        явную кнопку и показывает, что именно пропадёт. Запрет здесь нужен
        затем, чтобы случайно не убрать запись из общего списка.
        """
        if obj is not None and obj.group_id is None:
            return False
        return super().has_delete_permission(request, obj)

    change_list_template = 'admin/schedule_lesson_changelist.html'

    def changelist_view(self, request, extra_context=None):
        """Показывает полосу «Сырые занятия: N — разобрать».

        Полоса живёт здесь, а не в меню приложения: ``/admin/schedule/``
        в Django редиректит на первую модель раздела, и пункт в меню
        туда не попал бы. А сотрудница всё равно приходит за занятиями.

        Число передаётся всегда, а показывается только при непустой
        очереди: постоянная надпись «0 — разбирать нечего» приучила бы
        её не читать, а когда очередь появится, полоса будет уже на
        привычном месте.
        """
        context = {
            **(extra_context or {}),
            'groupless_count': Lesson.objects.filter(group__isnull=True).count(),
        }
        return super().changelist_view(request, context)

    @admin.display(description='День', ordering='day')
    def day_short(self, obj):
        # было get_day_display()[:2] — давало «Че», «Пя», «По», «Су»
        return day_abbrev(obj.day)

    @admin.display(description='Время', ordering='lesson_time__number')
    def time_short(self, obj):
        return f'{obj.lesson_time.number} ({obj.lesson_time.start:%H:%M})'

    def _shift_day(self, request, queryset, delta):
        done, skipped = 0, 0
        for lesson in queryset:
            new_day = lesson.day + delta
            if not 1 <= new_day <= 6:
                skipped += 1
                continue
            lesson.day = new_day
            try:
                lesson.save()
            except IntegrityError:
                lesson.refresh_from_db()
                skipped += 1
                continue
            done += 1
        self.message_user(
            request,
            f'Перенесено: {done}. Пропущено (занято или выход за границы недели): {skipped}.',
            messages.WARNING if skipped else messages.SUCCESS,
        )

    @admin.action(description='Сдвинуть день недели +1')
    def shift_day_forward(self, request, queryset):
        self._shift_day(request, queryset, 1)

    @admin.action(description='Сдвинуть день недели -1')
    def shift_day_back(self, request, queryset):
        self._shift_day(request, queryset, -1)




    def _stretch(self, request, queryset, spans):
        """Растягивает отмеченные занятия. Отказ идёт предупреждением.

        Сотрудница жмёт кнопку на списке, видит отказ и читает, что делать:
        молчаливый пропуск выглядел бы как «ничего не произошло», и
        она решила бы, что кнопка не работает.
        """
        made, refused = 0, []
        for lesson in queryset.select_related('lesson_time', 'room'):
            if lesson.extend_of_id:
                refused.append(
                    f'{lesson}: это хвост растянутой пары, '
                    'растягивать нечего.')
                continue
            try:
                stretch(lesson, spans)
                made += 1
            except CannotStretch as error:
                refused.append(f'{lesson}: {error}')
        if made:
            self.message_user(request, f'Растянуто на {spans} пары: {made}.',
                              messages.SUCCESS)
        for line in refused:
            self.message_user(request, line, messages.WARNING)

    @admin.action(description='Растянуть отмеченные на 2 пары')
    def stretch_by_two(self, request, queryset):
        self._stretch(request, queryset, 2)

    @admin.action(description='Растянуть отмеченные на 3 пары')
    def stretch_by_three(self, request, queryset):
        self._stretch(request, queryset, 3)

    @admin.action(description='Свернуть отмеченные в одну пару')
    def collapse_lessons(self, request, queryset):
        """Убирает хвосты. Правленые вручную хвосты не трогает."""
        done, refused = 0, []
        for lesson in queryset:
            changed = changed_extensions(lesson)
            if changed:
                refused.append(
                    f'{lesson}: у хвоста свои правки '
                    f'({changed[0].lesson_time.number}-я пара), '
                    'свернуть нельзя — пропала бы работа.')
                continue
            collapse(lesson)
            done += 1
        if done:
            self.message_user(request, f'Свёрнуто: {done}.', messages.SUCCESS)
        for line in refused:
            self.message_user(request, line, messages.WARNING)

@admin.register(Substitution)




class SubstitutionAdmin(admin.ModelAdmin):
    list_display = (
        'date', 'group', 'day_short', 'time_short', 'subject',
        'teacher_was', 'teacher_now', 'room_now', 'reason',
    )
    list_filter = ('date', 'reason')
    search_fields = ('lesson__group__name', 'lesson__subject__name', 'teacher__full_name')
    autocomplete_fields = ('lesson', 'teacher', 'room')
    list_select_related = ('lesson', 'lesson__lesson_time', 'lesson__subject', 'teacher', 'room')
    ordering = ('-date',)
    date_hierarchy = 'date'

    @admin.display(description='Группа')
    def group(self, obj):
        return obj.lesson.group

    @admin.display(description='День')
    def day_short(self, obj):
        # было get_day_display()[:2] — давало «Че», «Пя», «По», «Су»
        return day_abbrev(obj.lesson.day)

    @admin.display(description='Пара')
    def time_short(self, obj):
        return obj.lesson.lesson_time.number

    @admin.display(description='Предмет')
    def subject(self, obj):
        return obj.lesson.subject

    @admin.display(description='Был')
    def teacher_was(self, obj):
        return obj.lesson.teacher or '—'

    @admin.display(description='Стало')
    def teacher_now(self, obj):
        return obj.effective_teacher or '—'

    @admin.display(description='Аудитория')
    def room_now(self, obj):
        return obj.effective_room or '—'


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    """Обращения из формы «Связаться с нами»."""

    list_display = ('created_at', 'name', 'email', 'short_message', 'consent')
    list_filter = ('consent', 'created_at')
    search_fields = ('name', 'email', 'message')
    ordering = ('-created_at',)
    readonly_fields = ('created_at',)
    date_hierarchy = 'created_at'

    @admin.display(description='Сообщение')
    def short_message(self, obj):
        text = ' '.join(obj.message.split())
        return text[:60] + '…' if len(text) > 60 else text


@admin.register(LessonDraft)
class LessonDraftAdmin(admin.ModelAdmin):
    """Черновики правок расписания.

    Здесь сотрудница готовит исправления. На сайт они не попадают, пока их
    не опубликовали: кнопка «Опубликовать» переносит отмеченные правки в
    расписание одной транзакцией.

    Предпросмотр обязателен, поэтому он вынесен наверх списка: сотрудница
    должна увидеть, что получится, ещё до публикации.
    """

    list_display = ('created_at', 'action_label', 'summary', 'author',
                    'note', 'state')
    list_filter = ('action', 'published_at', 'created_at', 'author')
    search_fields = ('subject__name', 'group__name', 'teacher__full_name',
                     'room__name', 'note')
    autocomplete_fields = ('group', 'subject', 'teacher', 'room')
    ordering = ('-created_at',)
    date_hierarchy = 'created_at'
    actions = ('publish_selected', 'discard_selected')
    change_list_template = ('admin/schedule_lessondraft_changelist.html')
    readonly_fields = ('created_at', 'updated_at', 'published_at',
                       'published_by', 'state')

    fieldsets = (
        (None, {'fields': ('action', 'lesson', 'note')}),
        ('Новое занятие', {
            'fields': ('group', 'subject', 'lesson_type', 'day',
                       'lesson_time', 'teacher', 'room'),
            'description': (
                'Для правки «изменить» это новое значение занятия. '
                'Пустое поле значит «не трогать», а не «стереть»: '
                'не выбрали аудиторию — останется прежняя. '
                'Снять значение осознанно — галочками ниже.'
            ),
        }),
        ('Осознанно снять', {
            'fields': ('clear_room', 'clear_teacher', 'clear_lesson_type'),
            'description': (
                'Ставьте только когда значение нужно убрать по-настоящему.'
            ),
        }),
        ('Кто и когда', {'fields': ('author', 'state')}),
    )

    def get_queryset(self, request):
        return (super().get_queryset(request)
                .select_related('lesson', 'group', 'subject', 'teacher',
                                'room', 'lesson_time', 'author')
                .prefetch_related('lesson__group'))

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.is_superuser:
            actions.pop('publish_selected', None)
        return actions

    def save_model(self, request, obj, form, change):
        """Не даёт одному человеку затереть правку другого.

        Правки заходят часто, и двое могут открыть один черновик. Без
        сверки второй по сохранению молча затирает то, что вписал первый, —
        и пропадает именно работа того, кто старался. Сверяем дату
        последнего изменения: если черновик трогали после того, как форма
        была открыта, показываем это прямо.
        """
        if change and obj.pk:
            was = form.initial.get('updated_at')
            fresh = LessonDraft.objects.filter(pk=obj.pk).first()
            if (fresh and was and fresh.updated_at != was
                    and fresh.updated_at != obj.updated_at):
                from django.core.exceptions import ValidationError
                raise ValidationError(
                    'Черновик уже изменил другой человек '
                    f'({fresh.updated_at:%d.%m.%Y %H:%M}). '
                    'Обновите страницу и посмотрите последнюю версию — '
                    'ваши правки пока не сохранены.',
                    code='stale',
                )
        super().save_model(request, obj, form, change)

    def changelist_view(self, request, extra_context=None):
        extra_context = dict(extra_context or {})
        extra_context['draft_preview_url'] = 'admin:schedule_lessondraft_preview'
        extra_context['pending_total'] = pending_drafts().count()
        return super().changelist_view(request, extra_context)

    def get_urls(self):
        return [
            path('preview/', self.admin_site.admin_view(self.preview_view),
                 name='schedule_lessondraft_preview'),
        ] + super().get_urls()

    @admin.display(description='Действие')
    def action_label(self, obj):
        return obj.get_action_display()

    @admin.display(description='Что получится')
    def summary(self, obj):
        return obj.describe()

    @admin.display(description='Состояние')
    def state(self, obj):
        if obj.is_published:
            return f'опубликован {obj.published_at:%d.%m.%Y}'
        return 'черновик'

    @admin.action(description='Опубликовать отмеченные правки')
    def publish_selected(self, request, queryset):
        drafts = [d for d in queryset if d.pending]
        if not drafts:
            self.message_user(request, 'Отмеченные правки уже опубликованы.')
            return
        try:
            done = publish(drafts, request.user)
        except LessonDraft.DoesNotExist as error:
            self.message_user(request, str(error), messages.ERROR)
            return
        self.message_user(
            request, f'Опубликовано правок: {len(done)}.',
            messages.SUCCESS)

    @admin.action(description='Отменить отмеченные черновики')
    def discard_selected(self, request, queryset):
        count, _ = queryset.filter(published_at__isnull=True).delete()
        self.message_user(request, f'Удалено черновиков: {count}.',
                          messages.SUCCESS)

    def preview_view(self, request):
        """Как расписание будет выглядеть после публикации.

        Показывает ту же сетку, что и сайт, но собранную из опубликованного
        расписания с наложенными черновиками. Запись не создаётся: это
        предпросмотр, а не публикация.
        """
        week_monday = parse_week(request.GET.get('week'))
        drafts = list(pending_drafts())
        grid = build_draft_grid(week_monday, request, drafts)
        return render(request, 'admin/schedule/draft_preview.html', {
            'grid': grid,
            'drafts': drafts,
            'week_monday': week_monday,
            'week_label': (f'{week_monday:%d.%m} – '
                           f'{week_monday + timedelta(days=5):%d.%m.%Y}'),
            'prev_week': week_param(week_monday - timedelta(weeks=1)),
            'next_week': week_param(week_monday + timedelta(weeks=1)),
        })


@admin.register(DraftPublication)
class DraftPublicationAdmin(admin.ModelAdmin):
    """Журнал опубликованных корректировок — только чтение.

    Записи заводятся публикацией, руками их не трогают: журнал и есть
    история расписания. Открыт сотруднику, потому что ответ на вопрос
    «кто вчера передвинул пару» должен быть безопасен для всех.
    """

    list_display = ('published_at', 'action_label', 'what_changed', 'note',
                    'author', 'state')
    list_filter = ('action', 'published_at', 'undone_at', 'author')
    search_fields = ('note', 'author__username')
    date_hierarchy = 'published_at'
    ordering = ('-published_at',)
    actions = ('undo_selected',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Записи журнала удалять можно только вместе с базой: стёртый
        # след правки не восстановить.
        return request.user.is_superuser

    def get_actions(self, request):
        actions = super().get_actions(request)
        # Возврат переписывает живое расписание — право суперпользователя.
        if not request.user.is_superuser:
            actions.pop('undo_selected', None)
        return actions

    @admin.display(description='Состояние')
    def state(self, obj):
        if obj.undone_at is not None:
            return 'возвращено'
        return 'действует'

    @admin.action(description='Вернуть расписание как было')
    def undo_selected(self, request, queryset):
        """Откатывает отмеченные публикации по снимку «было».

        Порядок жёсткий: первой откатывается самая поздняя правка.
        Откатывать более раннюю, пока поздняя ещё действует, нельзя —
        состояние «было» у неё уже не то.
        """
        done, refused = 0, []
        for publication in sorted(queryset, key=lambda p: p.published_at,
                                  reverse=True):
            try:
                restore(publication, request.user)
                done += 1
            except CannotUndo as error:
                refused.append(f'{publication.published_at:%d.%m %H:%M} — {error}')
        if done:
            self.message_user(
                request, f'Возвращено публикаций: {done}.', messages.SUCCESS)
        for line in refused:
            self.message_user(request, line, messages.WARNING)

    @admin.display(description='Действие')
    def action_label(self, obj):
        return obj.get_action_display()

    @admin.display(description='Что изменилось')
    def what_changed(self, obj):
        before, after = obj.snapshot()
        moved = [field for field in before
                 if before.get(field) != after.get(field)]
        return ', '.join(moved) if moved else '—'


# Панель действий нужна и сверху, и снизу.
#
# По умолчанию Django рисует её под таблицей. В списке занятий это
# несколько сотен строк, и сотрудница кнопки «растянуть на 2 пары» просто
# не докручивает: на экране их нет, и кажется, что их нет вовсе.
#
# Меняются настройки самого сайта, а не подменяется объект: подмена
# admin.site после регистрации моделей оставила бы все разделы пустыми.
admin.site.actions_on_top = True
admin.site.actions_on_bottom = True
