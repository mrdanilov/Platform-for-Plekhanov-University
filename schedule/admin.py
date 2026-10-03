"""Админка расписания.

Полный контроль над данными: группы, пары, преподаватели, предметы,
аудитории, типы занятий, время пар, само расписание и замены.
"""

from django.contrib import admin, messages
from django.db import IntegrityError
from django.utils.html import format_html

from .models import (
    ContactMessage,
    Group,
    ImportLog,
    Lesson,
    LessonTime,
    LessonType,
    Room,
    Substitution,
    Subject,
    Teacher,
)

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
    list_display = ('name', 'lesson_count')
    search_fields = ('name',)

    @admin.display(description='Занятий')
    def lesson_count(self, obj):
        return obj.lessons.count()


@admin.register(LessonTime)
class LessonTimeAdmin(admin.ModelAdmin):
    """Пары по времени. Задаются один раз на всё расписание."""

    list_display = ('number', 'start', 'end')
    ordering = ('number',)

    def has_add_permission(self, request):
        return not LessonTime.objects.exists()


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
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
    actions = ['shift_day_forward', 'shift_day_back']
    fieldsets = (
        (None, {'fields': ('group', 'subject', 'lesson_type')}),
        ('Когда и где', {
            'fields': ('day', 'lesson_time', 'room', 'week_parity'),
        }),
        ('Кто ведёт', {'fields': ('teacher',)}),
        ('Прочее', {'classes': ('collapse',), 'fields': ('note', 'raw_text')}),
    )

    @admin.display(description='День', ordering='day')
    def day_short(self, obj):
        return obj.get_day_display()[:2]

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
        return obj.lesson.get_day_display()[:2]

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


@admin.register(ImportLog)
class ImportLogAdmin(admin.ModelAdmin):
    """Журнал загрузок расписания: кто, когда и сколько записей затронул.

    Только чтение — запись появляется сама при успешном импорте.
    Ручками добавлять или править смысла нет: событие уже произошло.
    """

    list_display = (
        'created_at', 'username', 'filename',
        'created_count', 'updated_count', 'skipped_count',
    )
    list_filter = ('created_at',)
    search_fields = ('username', 'filename', 'summary')
    date_hierarchy = 'created_at'
    readonly_fields = (
        'user', 'username', 'filename', 'created_at',
        'created_count', 'updated_count', 'skipped_count', 'summary',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


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