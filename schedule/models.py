from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse


class Teacher(models.Model):
    full_name = models.CharField('ФИО', max_length=200)
    position = models.CharField('Должность', max_length=150, blank=True)
    department = models.CharField('Кафедра', max_length=150, blank=True)
    phone = models.CharField('Телефон', max_length=40, blank=True)
    email = models.EmailField('Email', blank=True)
    active = models.BooleanField('Активен', default=True)

    class Meta:
        verbose_name = 'Преподаватель'
        verbose_name_plural = 'Преподаватели'
        ordering = ['full_name']

    def __str__(self):
        return self.full_name

    def get_absolute_url(self):
        return reverse('schedule:teacher_detail', args=[self.pk])


class Group(models.Model):
    name = models.CharField('Группа', max_length=50)
    course = models.PositiveSmallIntegerField('Курс', default=1)
    specialty = models.CharField('Специальность', max_length=200, blank=True)

    class Meta:
        verbose_name = 'Учебная группа'
        verbose_name_plural = 'Учебные группы'
        ordering = ['-course', 'name']
        unique_together = [('name', 'course')]

    def __str__(self):
        return f'{self.name} ({self.course} курс)'

    def get_absolute_url(self):
        return reverse('schedule:group_detail', args=[self.pk])


class Subject(models.Model):
    name = models.CharField('Предмет', max_length=200)
    short_name = models.CharField('Сокращение', max_length=30, blank=True)
    color = models.CharField(
        'Цвет', max_length=7, default='#e3f2fd',
        help_text='HEX-цвет подложки, например #e3f2fd',
    )

    class Meta:
        verbose_name = 'Предмет'
        verbose_name_plural = 'Предметы'
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def label(self):
        return self.short_name or self.name


class Room(models.Model):
    name = models.CharField('Аудитория', max_length=50)
    building = models.CharField('Корпус', max_length=50, blank=True)
    capacity = models.PositiveSmallIntegerField('Вместимость', blank=True, null=True)

    class Meta:
        verbose_name = 'Аудитория'
        verbose_name_plural = 'Аудитории'
        ordering = ['building', 'name']

    def __str__(self):
        if self.building:
            return f'{self.building} - {self.name}'
        return self.name


class LessonType(models.Model):
    name = models.CharField('Тип занятия', max_length=100)

    class Meta:
        verbose_name = 'Тип занятия'
        verbose_name_plural = 'Типы занятий'
        ordering = ['name']

    def __str__(self):
        return self.name


class LessonTime(models.Model):
    """Пара по времени: номер + интервал. Общий для всего расписания."""

    number = models.PositiveSmallIntegerField('Номер пары', unique=True)
    start = models.TimeField('Начало')
    end = models.TimeField('Конец')

    class Meta:
        verbose_name = 'Время пар'
        verbose_name_plural = 'Время пар'
        ordering = ['number']

    def __str__(self):
        return f'{self.number} пара: {self.start:%H:%M}–{self.end:%H:%M}'

    def clean(self):
        if self.start and self.end and self.start >= self.end:
            raise ValidationError({'end': 'Время окончания должно быть позже времени начала.'})


class Lesson(models.Model):
    """Одно занятие в регулярном расписании."""

    WEEK_CHOICES = [
        (0, 'Каждую неделю'),
        (1, 'Нечётные недели'),
        (2, 'Чётные недели'),
    ]

    group = models.ForeignKey(
        Group, verbose_name='Группа', related_name='lessons',
        on_delete=models.CASCADE, blank=True, null=True,
    )
    subject = models.ForeignKey(
        Subject, verbose_name='Предмет', related_name='lessons',
        on_delete=models.PROTECT,
    )
    lesson_type = models.ForeignKey(
        LessonType, verbose_name='Тип', related_name='lessons',
        on_delete=models.SET_NULL, blank=True, null=True,
    )
    teacher = models.ForeignKey(
        Teacher, verbose_name='Преподаватель', related_name='lessons',
        on_delete=models.SET_NULL, blank=True, null=True,
    )
    room = models.ForeignKey(
        Room, verbose_name='Аудитория', related_name='lessons',
        on_delete=models.SET_NULL, blank=True, null=True,
    )
    day = models.PositiveSmallIntegerField('День недели', choices=settings.WEEKDAYS)
    lesson_time = models.ForeignKey(
        LessonTime, verbose_name='Пара', related_name='lessons',
        on_delete=models.PROTECT,
    )
    week_parity = models.PositiveSmallIntegerField(
        'Недели', choices=WEEK_CHOICES, default=0,
        help_text='0 — каждую неделю, 1 — нечётные, 2 — чётные',
    )
    note = models.CharField('Примечание', max_length=200, blank=True)
    raw_text = models.TextField(
        'Исходный текст ячейки', blank=True,
        help_text='Как выглядело занятие в Excel-файле, если не удалось разобрать на части',
    )

    class Meta:
        verbose_name = 'Занятие'
        verbose_name_plural = 'Занятия'
        ordering = ['day', 'lesson_time__number', 'group__name']
        constraints = [
            models.UniqueConstraint(
                fields=['group', 'day', 'lesson_time', 'week_parity'],
                name='uniq_lesson_slot',
                condition=models.Q(group__isnull=False),
                violation_error_message=(
                    'В это время у группы уже есть занятие с таким же признаком недели.'
                ),
            ),
        ]

    def __str__(self):
        return f'{self.group} · {self.day} · пара {self.lesson_time_id} · {self.subject}'

    def get_absolute_url(self):
        return reverse('schedule:group_detail', args=[self.group_id])


class Substitution(models.Model):
    """Замена/перенос на конкретную дату."""

    lesson = models.ForeignKey(
        Lesson, verbose_name='Занятие', related_name='substitutions',
        on_delete=models.CASCADE,
    )
    date = models.DateField('Дата')
    teacher = models.ForeignKey(
        Teacher, verbose_name='Преподаватель (замена)', related_name='substitutions',
        on_delete=models.SET_NULL, blank=True, null=True,
    )
    room = models.ForeignKey(
        Room, verbose_name='Аудитория', related_name='substitutions',
        on_delete=models.SET_NULL, blank=True, null=True,
    )
    reason = models.CharField('Причина', max_length=200, blank=True)

    class Meta:
        verbose_name = 'Замена'
        verbose_name_plural = 'Замены'
        ordering = ['-date']
        constraints = [
            models.UniqueConstraint(
                fields=['lesson', 'date'], name='uniq_substitution_per_day',
            ),
        ]

    def __str__(self):
        return f'{self.lesson} на {self.date:%d.%m.%Y}'

    def clean(self):
        if self.lesson_id and self.date and self.lesson.day != self.date.weekday() + 1:
            raise ValidationError({
                'date': 'Дата замены должна совпадать с днём недели занятия.',
            })

    @property
    def effective_teacher(self):
        return self.teacher or self.lesson.teacher

    @property
    def effective_room(self):
        return self.room or self.lesson.room


class ContactMessage(models.Model):
    """Обращение из формы «Связаться с нами» в разделе об университете."""

    name = models.CharField('Имя Фамилия', max_length=120)
    email = models.EmailField('Почта')
    message = models.TextField('Сообщение')
    consent = models.BooleanField('Согласие на обработку персональных данных')
    created_at = models.DateTimeField('Получено', auto_now_add=True)

    class Meta:
        verbose_name = 'Обращение'
        verbose_name_plural = 'Обращения'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.name} — {self.created_at:%d.%m.%Y %H:%M}'


class ImportLog(models.Model):
    """Кто и когда загружал расписание из файла.

    Импорт переписывает расписание всего университета, поэтому важно
    знать, кто это сделал и сколько записей затронул. Само содержимое файла
    не сохраняем: файл остаётся во временной папке и удаляется через 6 часов.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='Кто загрузил',
        related_name='schedule_imports', on_delete=models.SET_NULL,
        blank=True, null=True,
    )
    username = models.CharField('Логин на момент импорта', max_length=150,
                                blank=True)
    filename = models.CharField('Файл', max_length=255, blank=True)
    created_at = models.DateTimeField('Когда', auto_now_add=True)
    created_count = models.PositiveIntegerField('Добавлено', default=0)
    updated_count = models.PositiveIntegerField('Обновлено', default=0)
    skipped_count = models.PositiveIntegerField('Пропущено', default=0)
    summary = models.CharField('Итог', max_length=300, blank=True)

    class Meta:
        verbose_name = 'Импорт расписания'
        verbose_name_plural = 'Импорты расписания'
        ordering = ['-created_at']

    def __str__(self):
        who = self.username or 'без пользователя'
        return f'{who}: {self.filename} ({self.created_at:%d.%m.%Y %H:%M})'