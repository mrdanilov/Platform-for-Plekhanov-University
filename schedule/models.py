import re

import json

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.urls import reverse

from .normkeys import norm_key

# Цвета типов занятий. Основные три названы явно, а не по остатку от хеша:
# «Лекция» должна быть голубой в любой базе, иначе вид карточки зависел бы
# от того, в каком порядке строки попали в файл. Остальные типы берут цвет из
# палитры предметов, чтобы новый тип не остался серым на сером.
LESSON_TYPE_COLORS = {
    'лекция': '#b8d4f2',
    'практика': '#bfe3bc',
    'лабораторная работа': '#d6c4ee',
    'лабораторные занятия': '#d6c4ee',
    'семинар': '#9fded4',
}

_TYPE_PALETTE = ('#b8d4f2', '#bfe3bc', '#ffe08a', '#d6c4ee', '#ffc79a',
                 '#9fded4', '#f7c2d4', '#dcea9e')


def lesson_type_color(name=''):
    """Цвет подложки для типа занятия по его названию."""
    key = norm_key(name)
    if key in LESSON_TYPE_COLORS:
        return LESSON_TYPE_COLORS[key]
    return _TYPE_PALETTE[hash(key) % len(_TYPE_PALETTE)]


class NormalizedKeyMixin(models.Model):
    """Держит нормализованный ключ имени в синхроне с самим именем.

    Поле ``norm_*`` заполняется при каждом ``save()``, поэтому импорт,
    админка и management-команды не могут его рассинхронизировать: забыть
    про него нельзя. Поиск идёт по ключу, а не по точному значению —
    см. ``schedule/normkeys.py``.
    """

    class Meta:
        abstract = True

    @classmethod
    def norm_field_names(cls):
        return tuple(
            f.name for f in cls._meta.fields if f.name.startswith('norm_')
        )

    def fill_norm(self):
        """Проставляет нормализованные ключи из обычных полей."""
        raise NotImplementedError

    def clean(self):
        # Ключ обязан быть посчитан до валидации, иначе проверка
        # уникальности сравнит пустое поле с сохранённым и пропустит дубль:
        # fill_norm() вызывается только в save(), а full_clean() до него
        # ещё не дошёл.
        self.fill_norm()
        super().clean()

    def full_clean(self, *args, **kwargs):
        # Поле norm_* помечено editable=False, поэтому в админ-форму оно не
        # попадает и уходит в exclude. А Django проверяет уникальность только
        # по неисключённым полям: для исключённого norm_name проверка молча
        # пропускается, дубль доходит до базы и пользователь видит 500 вместо
        # понятного сообщения. Поэтому ключ из exclude вынимается.
        exclude = kwargs.get('exclude')
        if exclude:
            kwargs['exclude'] = (
                set(exclude) - set(type(self).norm_field_names())
            )
        super().full_clean(*args, **kwargs)

    def save(self, *args, **kwargs):
        self.fill_norm()
        # save(update_fields=[...]) не примет изменения ключа, если его
        # не назвать явно, — а иначе ключ молча разъедется с именем.
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            kwargs['update_fields'] = (
                set(update_fields) | set(type(self).norm_field_names())
            )
        super().save(*args, **kwargs)


class Teacher(NormalizedKeyMixin):
    full_name = models.CharField('ФИО', max_length=200)
    position = models.CharField('Должность', max_length=150, blank=True)
    department = models.CharField('Кафедра', max_length=150, blank=True)
    phone = models.CharField('Телефон', max_length=40, blank=True)
    email = models.EmailField('Email', blank=True)
    active = models.BooleanField('Активен', default=True)
    norm_name = models.CharField('Ключ ФИО', max_length=200, editable=False,
                                 blank=True, default='')

    class Meta:
        verbose_name = 'Преподаватель'
        verbose_name_plural = 'Преподаватели'
        ordering = ['full_name']
        constraints = [
            models.UniqueConstraint(
                fields=['norm_name'], name='uniq_teacher_norm_name',
                violation_error_message=(
                    'Преподаватель с таким ФИО уже есть — отличается только '
                    'регистр или буква «ё».'
                ),
            ),
        ]

    def __str__(self):
        return self.full_name

    def get_absolute_url(self):
        return reverse('schedule:teacher_detail', args=[self.pk])

    def fill_norm(self):
        self.norm_name = norm_key(self.full_name)

    @classmethod
    def norm_lookup(cls, full_name=''):
        return {'norm_name': norm_key(full_name)}


class Group(NormalizedKeyMixin):
    name = models.CharField('Группа', max_length=50)
    course = models.PositiveSmallIntegerField('Курс', default=1)
    specialty = models.CharField('Специальность', max_length=200, blank=True)
    norm_name = models.CharField('Ключ группы', max_length=50, editable=False,
                                 blank=True, default='')

    class Meta:
        verbose_name = 'Учебная группа'
        verbose_name_plural = 'Учебные группы'
        ordering = ['-course', 'name']
        unique_together = [('name', 'course')]
        constraints = [
            models.UniqueConstraint(
                fields=['norm_name', 'course'], name='uniq_group_norm_name_course',
                violation_error_message=(
                    'Группа с таким названием и курсом уже есть — отличается '
                    'только регистр или буква «ё».'
                ),
            ),
        ]

    def __str__(self):
        return f'{self.name} ({self.course} курс)'

    def get_absolute_url(self):
        return reverse('schedule:group_detail', args=[self.pk])

    def fill_norm(self):
        self.norm_name = norm_key(self.name)

    @classmethod
    def norm_lookup(cls, name='', course=1):
        return {'norm_name': norm_key(name), 'course': course}


class Subject(NormalizedKeyMixin):
    name = models.CharField('Предмет', max_length=200)
    short_name = models.CharField('Сокращение', max_length=30, blank=True)
    color = models.CharField(
        'Цвет', max_length=7, default='#e3f2fd',
        help_text='HEX-цвет подложки, например #e3f2fd',
    )
    norm_name = models.CharField('Ключ предмета', max_length=200, editable=False,
                                 blank=True, default='')

    class Meta:
        verbose_name = 'Предмет'
        verbose_name_plural = 'Предметы'
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(
                fields=['norm_name'], name='uniq_subject_norm_name',
                violation_error_message=(
                    'Предмет с таким названием уже есть — отличается только '
                    'регистр или буква «ё».'
                ),
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def label(self):
        return self.short_name or self.name

    def clean(self):
        # Цвет подставляется в шаблон инлайн-стилем:
        # style="background: {{ subject.color }}". Без проверки туда можно
        # записать что угодно: «синий» молча превратится в пустой фон, а
        # строка с «}» вообще закроет атрибут и сломает разметку.
        if self.color and not re.fullmatch(r'#[0-9a-fA-F]{6}', self.color):
            raise ValidationError({
                'color': 'Цвет должен быть вида #dcea9e: знак # и шесть '
                         'знаков из цифр и букв a–f. Проще выбрать цвет '
                         'образцом или пипеткой.',
            })

    def fill_norm(self):
        self.norm_name = norm_key(self.name)

    @classmethod
    def norm_lookup(cls, name=''):
        return {'norm_name': norm_key(name)}


class Room(NormalizedKeyMixin):
    name = models.CharField('Аудитория', max_length=50)
    building = models.CharField('Корпус', max_length=50, blank=True)
    capacity = models.PositiveSmallIntegerField('Вместимость', blank=True, null=True)
    norm_building = models.CharField('Ключ корпуса', max_length=50, editable=False,
                                     blank=True, default='')
    norm_name = models.CharField('Ключ аудитории', max_length=50, editable=False,
                                 blank=True, default='')

    class Meta:
        verbose_name = 'Аудитория'
        verbose_name_plural = 'Аудитории'
        ordering = ['building', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['norm_building', 'norm_name'], name='uniq_room_norm',
                violation_error_message=(
                    'Такая аудитория уже есть — отличается только регистр '
                    'или буква «ё».'
                ),
            ),
        ]

    def __str__(self):
        if self.building:
            return f'{self.building} - {self.name}'
        return self.name

    def fill_norm(self):
        self.norm_building = norm_key(self.building)
        self.norm_name = norm_key(self.name)

    @classmethod
    def norm_lookup(cls, name='', building=''):
        return {'norm_building': norm_key(building), 'norm_name': norm_key(name)}


class LessonType(NormalizedKeyMixin):
    name = models.CharField('Тип занятия', max_length=100)
    color = models.CharField(
        'Цвет', max_length=7, default='#e3f2fd',
        help_text='HEX-цвет подложки, например #e3f2fd',
    )
    norm_name = models.CharField('Ключ типа', max_length=100, editable=False,
                                 blank=True, default='')

    def __init__(self, *args, **kwargs):
        # Цвет подставляется по названию, если его не задали: иначе новый
        # тип, созданный из файла или из консоли, получил бы пустую подложку
        # и остался бы нечитаемым, пока кто-то не откроет админку.
        if 'color' not in kwargs and len(args) < 2:
            kwargs['color'] = lesson_type_color(kwargs.get('name', ''))
        super().__init__(*args, **kwargs)

    class Meta:
        verbose_name = 'Тип занятия'
        verbose_name_plural = 'Типы занятий'
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(
                fields=['norm_name'], name='uniq_lessontype_norm_name',
                violation_error_message=(
                    'Тип занятия с таким названием уже есть — отличается только '
                    'регистр или буква «ё».'
                ),
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        # Тот же случай, что и у предмета: цвет уходит в шаблон инлайн-стилем,
        # поэтому в него можно записать что угодно. Пустая подложка молча
        # оставила бы тип серым на сером — его и так почти не видно.
        if self.color and not re.fullmatch(r'#[0-9a-fA-F]{6}', self.color):
            raise ValidationError({
                'color': 'Цвет должен быть вида #dcea9e: знак # и шесть '
                         'знаков из цифр и букв a–f. Проще выбрать цвет '
                         'образцом или пипеткой.',
            })

    def fill_norm(self):
        self.norm_name = norm_key(self.name)

    @classmethod
    def norm_lookup(cls, name=''):
        return {'norm_name': norm_key(name)}


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
    # Сколько пар подряд занимает занятие. Обычно одна, и тогда поле
    # ни на что не влияет. Значение больше единицы появляется у занятий,
    # которые сотрудница растянула на две-три пары кнопкой в админке.
    #
    # Само занятие по-прежнему занимает одну пару: хвосты лежат
    # отдельными записями и связаны через extend_of. Так сделано потому,
    # что вуз ведёт расписание по парам, и на сервере «одна запись на три
    # часа» не напечатать.
    spans = models.PositiveSmallIntegerField(
        'Занимает пар подряд', default=1,
        help_text='Сколько пар подряд занимает занятие. Больше 1 ставится '
                  'кнопкой «Растянуть», вручную менять не нужно.',
    )

    # Автоматическая склейка одинаковых соседних пар. Работает только в
    # окне кабинета, где видно, что одна лекция идёт через две-три пары
    # подряд. Галочку снимают, когда такое объединение не нужно.
    auto_join = models.BooleanField(
        'Склеивать с такой же парой рядом', default=True,
        help_text='В окне кабинета одинаковые пары одного преподавателя '
                  'рисуются одной карточкой. Снимите галочку, если нужно '
                  'видеть их раздельно.',
    )

    # Занятие, которому это подчинено: хвост растянутой пары. У корня
    # ссылаются все хвосты, поэтому «свернуть» знает, что удалять.
    extend_of = models.ForeignKey(
        'self', verbose_name='Продолжение занятия', related_name='extensions',
        on_delete=models.CASCADE, blank=True, null=True,
        help_text='Задаётся кнопкой «Растянуть». Вручную не трогать.',
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
            # Предмет входит в ключ сознательно. Раньше ограничение было
            # (группа, день, пара, неделя), и оно запрещало реальную вещь:
            # у одной группы в один день в одну пару могут стоять два разных
            # занятия — например, когда пара делится между преподавателями.
            # Такое расписание рендерится и показывается, ячейка просто
            # содержит два блока друг под другом.
            #
            # Точная копия занятия по-прежнему запрещена: если добавить
            # «Микроэкономику» дважды, это опечатка, а не расписание.
            models.UniqueConstraint(
                fields=['group', 'day', 'lesson_time', 'week_parity', 'subject'],
                name='uniq_lesson_slot',
                condition=models.Q(group__isnull=False),
                violation_error_message=(
                    'Это занятие уже есть: у группы та же пара в этот день, '
                    'с тем же предметом и признаком недели.'
                ),
            ),
        ]

    def __str__(self):
        if self.group_id is None:
            # Без группы строка выглядела бы как «None · 2 · пара 1 · …» —
            # и в списке занятий, и в админке, и в выгрузке Excel. Такие
            # записи — это забытая при ручном вводе группа, и это не ошибка
            # кода, а брак ввода: исходный текст лежит в raw_text.
            day = self._day_name()
            text = self.raw_text or 'без текста'
            return f'без группы · день {day} · пара {self.lesson_time_id} · {text}'
        return f'{self.group} · {self.day} · пара {self.lesson_time_id} · {self.subject}'

    def _day_name(self):
        """Название дня недели вместо числа."""
        try:
            return dict(settings.WEEKDAYS).get(self.day, self.day)
        except Exception:
            return self.day

    def get_absolute_url(self):
        """Адрес занятия на сайте.

У занятия может не быть группы — такое занятие можно создать вручную,
        не выбрав группу (например, когда в поле опечатка или его не
        заметили). Раньше такие записи создавал импорт, разбиравший ячейки
        Excel, но импорт удалён, а поле группы в форме осталось
        необязательным.

        Раньше здесь жёстко подставлялся ``group_id``, и на такой записи
        reverse падал с ``NoReverseMatch``: клик «Посмотреть на сайте» в
        админке давал 500.

        Раз группа пустая, страницы группы не существует. Но занятие всё
        равно где-то показывается, и ссылка должна вести туда, где оно
        видно, а не в никуда:

        * есть преподаватель — его расписание содержит это занятие;
        * есть аудитория — её расписание тоже;
        * ничего нет — главная страница, где есть выбор из списка.

        Пустой строки не возвращается: в админке такая ссылка ведёт себя
        непредсказуемо — может отрисоваться как относительный путь и
        увести на несуществующий адрес.
        """
        if self.group_id is not None:
            return reverse('schedule:group_detail', args=[self.group_id])
        if self.teacher_id is not None:
            return reverse('schedule:teacher_detail', args=[self.teacher_id])
        if self.room_id is not None:
            return reverse('schedule:room_detail', args=[self.room_id])
        return reverse('schedule:index')


class SubstitutionQuerySet(models.QuerySet):
    """Записи замен, которые не могут разойтись с расписанием.

    Переопределены ``bulk_create`` и ``update`` потому, что они не зовут
    ``Model.save()`` и потому обходят проверку дня недели. Замена с чужой
    датой записывалась в базу и там пропадала: ``grid.build_grid`` ищет её
    по ключу ``(lesson_id, date.isoweekday())``, поэтому в таблице она не
    появлялась — и никакой ошибки не показывалось.

    Оба метода не вызывают ``save()``, поэтому проверять нечего: объекты
    проверяются напрямую перед записью.
    """

    DAY_MISMATCH = 'Дата замены должна совпадать с днём недели занятия.'

    def _reject_wrong_day(self, objects):
        for sub in objects:
            sub.clean()

    def bulk_create(self, objs, *args, **kwargs):
        self._reject_wrong_day(objs)
        return super().bulk_create(objs, *args, **kwargs)

    def update(self, **kwargs):
        """Обновление пачкой тоже проверяет день недели.

        Проверяются только записи, у которых меняется ``lesson`` или
        ``date``: остальные правилом не затронуты.

        Важно: выборка идёт по текущему состоянию базы, а не по значениям
        из ``kwargs``. Если фильтровать по новым значениям, запись, которую
        сейчас правят, просто не найдётся — проверка молча ничего не
        сделает, и дыра останется. Поэтому сначала читаются подходящие
        записи, затем каждая проверяется в том виде, в каком она будет
        после обновления.
        """
        if not {'lesson', 'date'} & set(kwargs):
            return super().update(**kwargs)

        # Состояние собирается из базы и kwargs целиком: нельзя полагаться
        # на один лишь Substitution(**kwargs). У такого объекта не задано
        # занятие, а clean() начинает с проверки self.lesson_id — и молча
        # выходит, решив, что проверять нечего. Правило срабатывало бы
        # только когда kwargs содержал оба значения.
        for existing in self.select_related('lesson'):
            merged = Substitution(
                **{f.name: getattr(existing, f.name)
                   for f in Substitution._meta.fields
                   if not f.primary_key},
            )
            for key, value in kwargs.items():
                setattr(merged, key, value)
            merged.clean()

        return super().update(**kwargs)


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
        # Почему нет CHECK на согласованность даты и дня недели: SQLite не
        # поддерживает strftime в CHECK, а день недели из даты без календаря
        # не выводится. Поэтому инвариант держит код — clean() и методы
        # QuerySet выше, — а не ограничение в схеме.
        #
        # Про QuerySet: bulk_create() и queryset.update() идут мимо save()
        # и потому мимо clean(). Замена, созданная ими с чужой датой,
        # попадала в базу и там терялась: build_grid ищет её по ключу
        # (lesson_id, date.isoweekday()), поэтому в расписании она просто не
        # появлялась. Сейчас любой путь записи приводит к проверке.

    objects = SubstitutionQuerySet.as_manager()

    def __str__(self):
        return f'{self.lesson} на {self.date:%d.%m.%Y}'

    def clean(self):
        if self.lesson_id and self.date and self.lesson.day != self.date.weekday() + 1:
            raise ValidationError({
                'date': SubstitutionQuerySet.DAY_MISMATCH,
            })

    def save(self, *args, **kwargs):
        """Проверяет согласованность даты и дня недели при любом сохранении.

        Почему не только ``clean()``. ``clean()`` вызывается через
        ``full_clean()``, а его зовёт только ModelForm — то есть админка.
        ``Substitution.objects.create(...)`` правило пропускал, и запись
        молча расходилась с расписанием: ``grid.build_grid`` ищет замену по
        ключу ``(lesson_id, date.isoweekday())``, поэтому запись с чужой датой
        не находилась вообще. Замена лежала в базе, а в таблице её не было,
        и нигде не появлялось сообщение об ошибке.

        На уровне базы это не лечится: SQLite не поддерживает ``strftime`` в
        CHECK, а даты без календаря в арифметике не переводятся в день недели.
        Поэтому проверка живёт здесь — в единственном месте, через которое
        проходит любое сохранение, и обойти его нельзя.

        Проверяется ``self.clean()``, а не ``full_clean()``: нужно ровно
        одно правило, а полная валидация каждого поля на каждом сохранении
        тут лишняя.
        """
        self.clean()
        return super().save(*args, **kwargs)

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


class LessonDraft(models.Model):
    """Черновик правки расписания.

    Сотрудница готовит правку отдельно от расписания. Пока правка в
    черновиках, сайт показывает ровно то, что опубликовано, — ни один
    посетитель не видит недоделанную работу. Публикация переносит правку в
    расписание целиком, одной кнопкой и одной транзакцией.

    Черновик — это одна правка, а не копия недели: публикация задевает
    ровно то, что сотрудница отметила, и ничего больше. Иначе одно
    нажатие «опубликовать неделю» снесло бы чужие правки.

    Вид ``action``:

    * ``create`` — новое занятие (перенос, добавленная пара);
    * ``update`` — изменение существующего;
    * ``delete`` — занятие отменяется.

    Для ``update`` и ``delete`` обязательна ссылка ``lesson``. Для ``create``
    её быть не должно: новое занятие ещё не лежит в расписании.

    Поле ``week_parity`` у черновика не хранится: новые занятия заводятся на
    текущую неделю («каждую неделю»), а изменение недели у существующего
    занятия — редкость, которую проще сделать через отдельную правку в
    опубликованном расписании.
    """

    ACTIONS = [
        ('create', 'Новое занятие'),
        ('update', 'Изменить занятие'),
        ('delete', 'Отменить занятие'),
    ]

    action = models.CharField('Что делаем', max_length=10, choices=ACTIONS,
                              default='create')
    lesson = models.ForeignKey(
        Lesson, verbose_name='Занятие', related_name='drafts',
        on_delete=models.CASCADE, blank=True, null=True,
        help_text='Заполняется для «изменить» и «отменить»',
    )

    group = models.ForeignKey(
        Group, verbose_name='Группа', related_name='drafts',
        on_delete=models.CASCADE, blank=True, null=True,
    )
    subject = models.ForeignKey(
        Subject, verbose_name='Предмет', related_name='drafts',
        on_delete=models.PROTECT, blank=True, null=True,
    )
    lesson_type = models.ForeignKey(
        LessonType, verbose_name='Тип', related_name='drafts',
        on_delete=models.SET_NULL, blank=True, null=True,
    )
    teacher = models.ForeignKey(
        Teacher, verbose_name='Преподаватель', related_name='drafts',
        on_delete=models.SET_NULL, blank=True, null=True,
    )
    room = models.ForeignKey(
        Room, verbose_name='Аудитория', related_name='drafts',
        on_delete=models.SET_NULL, blank=True, null=True,
    )
    day = models.PositiveSmallIntegerField('День недели', choices=settings.WEEKDAYS,
                                           blank=True, null=True)
    lesson_time = models.ForeignKey(
        LessonTime, verbose_name='Пара', related_name='drafts',
        on_delete=models.PROTECT, blank=True, null=True,
    )

    # Заметка черновика — это объяснение сотрудницы, а не заметка занятия.
    # В занятие она не переносится: иначе правка стирала бы то, что
    # преподаватель написал о своём занятии. В расписание она попадает
    # только через журнал публикаций.
    note = models.TextField(
        'Что меняем', blank=True,
        help_text='Почему правка нужна. Это увидит тот, кто публикует, '
                  'и останется в журнале навсегда.',
    )

    # Явные «стереть». Без них пустое поле пришлось бы читать как «удали
    # значение», и правка одного дня молча сносила бы аудиторию или
    # преподавателя у всех остальных занятий подряд.
    clear_room = models.BooleanField(
        'Убрать аудиторию', default=False,
        help_text='Ставится, когда аудиторию нужно снять осознанно',
    )
    clear_teacher = models.BooleanField(
        'Убрать преподавателя', default=False,
    )
    clear_lesson_type = models.BooleanField(
        'Убрать тип занятия', default=False,
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='Кто подготовил',
        related_name='lesson_drafts', on_delete=models.SET_NULL,
        blank=True, null=True,
    )
    created_at = models.DateTimeField('Создан', auto_now_add=True)
    # Когда черновик меняли в последний раз. Нужен, чтобы поймать случай,
    # когда двое правили одну правку: без сверки даты второй сохранением
    # молча затирает работу первого, и пропадает именно то, что он вписал.
    updated_at = models.DateTimeField('Изменён', auto_now=True)
    published_at = models.DateTimeField('Опубликован', blank=True, null=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='Кто опубликовал',
        related_name='published_lesson_drafts', on_delete=models.SET_NULL,
        blank=True, null=True,
    )

    class Meta:
        verbose_name = 'Черновик правки'
        verbose_name_plural = 'Черновики правок'
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(
                condition=~Q(action='create', lesson__isnull=False),
                name='draft_create_has_no_lesson',
            ),
            models.CheckConstraint(
                condition=~Q(action__in=('update', 'delete'),
                             lesson__isnull=True),
                name='draft_update_delete_needs_lesson',
            ),
        ]

    def __str__(self):
        return f'{self.get_action_display()}: {self.subject or self.lesson}'

    @property
    def is_published(self):
        return self.published_at is not None

    @property
    def pending(self):
        """Не опубликованные черновики — именно они попадают на сайт."""
        return self.pk is not None and self.published_at is None

    def describe(self):
        """Человеческое описание правки — для списка и подтверждений."""
        if self.action == 'delete':
            return f'Отменить: {self.lesson}'
        where = []
        if self.day:
            where.append(dict(settings.WEEKDAYS)[self.day])
        if self.lesson_time_id:
            where.append(f'{self.lesson_time.number}-я пара')
        head = f'{self.subject or "—"}'
        if self.group:
            head += f', {self.group.name}'
        return f'{head} — {" ".join(where) or "пара не указана"}'


class DraftPublication(models.Model):
    """Журнал опубликованных корректировок.

    Корректировки бывают частыми, и через месяц никто не вспомнит, кто и
    когда передвинул пару. Запись журнала хранит снимок занятия до и после
    правки, поэтому по любой строке видно, что изменилось, и это же
    состояние можно вернуть обратно.

    Черновик не удаляется после публикации, а журнал ссылается на него
    через SET_NULL: черновик может быть стёрт, но запись о публикации
    обязана остаться.
    """

    draft = models.ForeignKey(
        LessonDraft, verbose_name='Черновик', related_name='publications',
        on_delete=models.SET_NULL, blank=True, null=True,
    )
    action = models.CharField('Действие', max_length=10,
                              choices=LessonDraft.ACTIONS)
    lesson = models.ForeignKey(
        Lesson, verbose_name='Занятие', related_name='publications',
        on_delete=models.SET_NULL, blank=True, null=True,
    )
    before = models.TextField('Было', blank=True,
                              help_text='Снимок занятия до правки в JSON')
    after = models.TextField('Стало', blank=True,
                             help_text='Снимок занятия после правки в JSON')
    note = models.TextField('Пояснение', blank=True)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='Кто опубликовал',
        related_name='draft_publications', on_delete=models.SET_NULL,
        blank=True, null=True,
    )
    published_at = models.DateTimeField('Опубликовано', auto_now_add=True)
    undone_at = models.DateTimeField('Возвращено', blank=True, null=True)
    undone_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='Кто вернул',
        related_name='undone_draft_publications', on_delete=models.SET_NULL,
        blank=True, null=True,
    )

    class Meta:
        verbose_name = 'Публикация правки'
        verbose_name_plural = 'Журнал публикаций'
        ordering = ['-published_at']

    def __str__(self):
        return f'{self.get_action_display()} — {self.published_at:%d.%m.%Y %H:%M}'

    def snapshot(self):
        return json.loads(self.before or '{}'), json.loads(self.after or '{}')
