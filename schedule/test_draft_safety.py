"""Черновик не должен терять данные.

Проверяется то, что ломается при правках «по мелочи», когда сотрудница
заходит в черновик каждый день:

* пустое поле в правке не стирает заполненное в расписании;
* осознанное удаление значения возможно, но только по явному требованию;
* пояснение сотрудницы не затирает заметку самого занятия;
* два человека, правящие один черновик, не затирают друг друга;
* каждая публикация остаётся в журнале со снимком «было» и «стало».

Без этих проверок потеря обнаруживалась бы на живом расписании, и заметить
её можно было бы только через неделю — когда заметку уже никто не помнит.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from schedule.drafts import publish
from schedule.models import (
    DraftPublication,
    Group,
    Lesson,
    LessonDraft,
    LessonTime,
    Room,
    Subject,
    Teacher,
)


class NoDataLossTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.slot = LessonTime.objects.create(number=1, start='09:00', end='10:20')
        cls.subject = Subject.objects.create(name='Матан', short_name='Матем')
        cls.group = Group.objects.create(name='6301', course=1)
        cls.teacher = Teacher.objects.create(full_name='Соколова С.Р.')
        cls.room = Room.objects.create(name='201')
        cls.lesson = Lesson.objects.create(
            group=cls.group, subject=cls.subject, lesson_time=cls.slot,
            day=1, teacher=cls.teacher, room=cls.room,
            note='ЗАМЕТКА ПРЕПОДАВАТЕЛЯ',
        )

    def update(self, **kwargs):
        """Черновик «изменить». Пустые поля — то, что не выбрано в форме."""
        data = {'action': 'update', 'lesson': self.lesson,
                'subject': self.subject, 'group': self.group, 'day': 1,
                'lesson_time': self.slot, 'teacher': self.teacher,
                'room': self.room, 'note': 'перенос'}
        data.update(kwargs)
        return LessonDraft.objects.create(**data)

    def lesson_now(self):
        return Lesson.objects.get(pk=self.lesson.pk)

    # ------------------------------------------------- пустое ≠ «стереть»

    def test_empty_room_does_not_clear_it(self):
        publish([self.update(day=3, room=None)], None)
        self.assertEqual(self.lesson_now().room_id, self.room.pk)

    def test_empty_teacher_does_not_clear_it(self):
        publish([self.update(day=3, teacher=None)], None)
        self.assertEqual(self.lesson_now().teacher_id, self.teacher.pk)

    def test_empty_lesson_type_does_not_clear_it(self):
        from schedule.models import LessonType
        kind = LessonType.objects.create(name='Лекция')
        Lesson.objects.filter(pk=self.lesson.pk).update(lesson_type=kind)
        publish([self.update(day=3, lesson_type=None)], None)
        self.assertEqual(self.lesson_now().lesson_type_id, kind.pk)

    def test_only_touched_field_changes(self):
        publish([self.update(day=3, room=None, teacher=None)], None)
        after = self.lesson_now()
        self.assertEqual(after.day, 3)
        self.assertEqual(after.room_id, self.room.pk)
        self.assertEqual(after.teacher_id, self.teacher.pk)

    def test_draft_note_does_not_touch_lesson_note(self):
        """Пояснение сотрудницы не затирает заметку занятия."""
        publish([self.update(day=3, note='аудитория занята')], None)
        self.assertEqual(self.lesson_now().note, 'ЗАМЕТКА ПРЕПОДАВАТЕЛЯ')

    # ------------------------------------------------- осознанная очистка

    def test_clear_room_removes_it(self):
        publish([self.update(clear_room=True, room=None)], None)
        self.assertIsNone(self.lesson_now().room_id)

    def test_clear_teacher_removes_it(self):
        publish([self.update(clear_teacher=True, teacher=None)], None)
        self.assertIsNone(self.lesson_now().teacher_id)

    def test_clear_flag_does_not_touch_other_fields(self):
        publish([self.update(clear_room=True, room=None)], None)
        self.assertEqual(self.lesson_now().teacher_id, self.teacher.pk)

    def test_new_lesson_still_takes_blank_as_blank(self):
        """У нового занятия пустое поле — это и правда пусто.

        Там нечего сохранять: занятия ещё не существует, и оставлять
        «не трогать» нечего.
        """
        draft = LessonDraft.objects.create(
            action='create', subject=self.subject, group=self.group,
            day=2, lesson_time=self.slot, teacher=self.teacher,
            room=None, note='новое')
        publish([draft], None)
        created = Lesson.objects.exclude(pk=self.lesson.pk).get()
        self.assertIsNone(created.room_id)


class ConcurrentEditingTests(TestCase):
    """Двое правят один черновик — работа первого не должна пропасть."""

    @classmethod
    def setUpTestData(cls):
        cls.slot = LessonTime.objects.create(number=1, start='09:00', end='10:20')
        cls.subject = Subject.objects.create(name='Матан', short_name='Матем')
        cls.group = Group.objects.create(name='6301', course=1)
        cls.teacher = Teacher.objects.create(full_name='Соколова С.Р.')
        cls.room = Room.objects.create(name='201')
        cls.lesson = Lesson.objects.create(
            group=cls.group, subject=cls.subject, lesson_time=cls.slot,
            day=1, teacher=cls.teacher, room=cls.room,
        )

    def test_updated_at_moves_on_save(self):
        first = LessonDraft.objects.filter(action='create').create(
            subject=self.subject, group=self.group, day=1,
            lesson_time=self.slot, teacher=self.teacher, room=self.room)
        first_mark = first.updated_at
        first.note = 'правка'
        first.save()
        first.refresh_from_db()
        self.assertGreater(first.updated_at, first_mark)

    def test_stale_form_is_refused(self):
        """Форма, открытая до чужой правки, не должна её затирать."""
        from django.contrib import admin as dj_admin
        from django.core.exceptions import ValidationError

        user = get_user_model().objects.create_superuser(
            'boss', 'boss@example.com', 'pass-12345678')
        draft = LessonDraft.objects.create(
            subject=self.subject, group=self.group, day=1,
            lesson_time=self.slot, teacher=self.teacher, room=self.room)

        opened_at = draft.updated_at
        # Пока форма открыта, черновик поменял другой человек. Сохранение
        # идёт через save(), а не через update(): именно save() двигает
        # updated_at, и именно его ловит сверка.
        other = LessonDraft.objects.get(pk=draft.pk)
        other.note = 'чужая правка'
        other.save()
        self.assertGreater(other.updated_at, opened_at)

        form = type('FakeForm', (), {
            'initial': {'updated_at': opened_at},
            'cleaned_data': {'note': 'моя правка'},
        })()
        model_admin = dj_admin.site._registry[LessonDraft]
        with self.assertRaises(ValidationError):
            model_admin.save_model(_FakeRequest(user), draft, form, change=True)

    def test_second_save_without_conflict_goes_through(self):
        from django.contrib import admin as dj_admin
        user = get_user_model().objects.create_superuser(
            'boss', 'boss@example.com', 'pass-12345678')
        draft = LessonDraft.objects.create(
            subject=self.subject, group=self.group, day=1,
            lesson_time=self.slot, teacher=self.teacher, room=self.room)
        form = type('FakeForm', (), {
            'initial': {'updated_at': draft.updated_at},
            'cleaned_data': {'note': 'правка'},
        })()
        # save_form кладёт значения из формы в объект — так же, как это
        # делает сама админка перед save_model.
        form.instance = draft
        model_admin = dj_admin.site._registry[LessonDraft]
        for field, value in form.cleaned_data.items():
            setattr(draft, field, value)
        model_admin.save_model(_FakeRequest(user), draft, form, change=True)
        draft.refresh_from_db()
        self.assertEqual(draft.note, 'правка')


class _FakeRequest:
    """Минимальный запрос: save_model смотрит только на пользователя."""

    def __init__(self, user):
        self.user = user


class PublicationJournalTests(TestCase):
    """Корректировок много: по каждой должен остаться след."""

    @classmethod
    def setUpTestData(cls):
        cls.slot = LessonTime.objects.create(number=1, start='09:00', end='10:20')
        cls.slot2 = LessonTime.objects.create(number=2, start='10:30',
                                              end='11:50')
        cls.subject = Subject.objects.create(name='Матан', short_name='Матем')
        cls.group = Group.objects.create(name='6301', course=1)
        cls.teacher = Teacher.objects.create(full_name='Соколова С.Р.')
        cls.room = Room.objects.create(name='201')
        cls.lesson = Lesson.objects.create(
            group=cls.group, subject=cls.subject, lesson_time=cls.slot,
            day=1, teacher=cls.teacher, room=cls.room,
        )

    def test_journal_row_per_publication(self):
        drafts = [
            LessonDraft.objects.create(
                action='update', lesson=self.lesson, subject=self.subject,
                group=self.group, day=3, lesson_time=self.slot,
                teacher=self.teacher, room=self.room, note='перенос'),
            LessonDraft.objects.create(
                action='create', subject=self.subject, group=self.group,
                day=4, lesson_time=self.slot2, teacher=self.teacher,
                room=self.room, note='добавленная пара'),
        ]
        publish(drafts, None)
        self.assertEqual(DraftPublication.objects.count(), 2)

    def test_journal_keeps_before_and_after(self):
        draft = LessonDraft.objects.create(
            action='update', lesson=self.lesson, subject=self.subject,
            group=self.group, day=5, lesson_time=self.slot,
            teacher=self.teacher, room=self.room, note='перенос')
        publish([draft], None)
        before, after = DraftPublication.objects.get().snapshot()
        self.assertEqual(before['day'], 1)
        self.assertEqual(after['day'], 5)

    def test_journal_keeps_explanation(self):
        draft = LessonDraft.objects.create(
            action='update', lesson=self.lesson, subject=self.subject,
            group=self.group, day=2, lesson_time=self.slot,
            teacher=self.teacher, room=self.room,
            note='аудиторию заняли, перенесли к 302')
        publish([draft], None)
        self.assertIn('перенесли к 302',
                      DraftPublication.objects.get().note)

    def test_journal_survives_deleted_draft(self):
        """Стёртый черновик не уносит с собой запись журнала."""
        draft = LessonDraft.objects.create(
            action='update', lesson=self.lesson, subject=self.subject,
            group=self.group, day=2, lesson_time=self.slot,
            teacher=self.teacher, room=self.room, note='перенос')
        publish([draft], None)
        draft.delete()
        self.assertEqual(DraftPublication.objects.count(), 1)

    def test_journal_records_cancellation(self):
        draft = LessonDraft.objects.create(action='delete',
                                           lesson=self.lesson, note='отмена')
        publish([draft], None)
        entry = DraftPublication.objects.get()
        before, after = entry.snapshot()
        self.assertEqual(before['day'], 1)
        self.assertEqual(after, {})

    def test_journal_records_author(self):
        user = get_user_model().objects.create_superuser(
            'boss', 'boss@example.com', 'pass-12345678')
        draft = LessonDraft.objects.create(
            action='update', lesson=self.lesson, subject=self.subject,
            group=self.group, day=2, lesson_time=self.slot,
            teacher=self.teacher, room=self.room, note='перенос')
        publish([draft], user)
        self.assertEqual(DraftPublication.objects.get().author_id, user.pk)

    def test_note_field_is_not_truncated(self):
        """Пояснение может быть длинным — обрезать его нельзя."""
        long_note = 'причина ' * 200
        draft = LessonDraft.objects.create(
            action='update', lesson=self.lesson, subject=self.subject,
            group=self.group, day=2, lesson_time=self.slot,
            teacher=self.teacher, room=self.room, note=long_note)
        publish([draft], None)
        self.assertEqual(DraftPublication.objects.get().note, long_note)