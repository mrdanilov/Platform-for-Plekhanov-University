"""Возврат публикации: расписание возвращается к состоянию «было».

Корректировок много, и среди них обязательно будут ошибки. Откат должен
быть точным и безопасным:

* возвращается именно та правка, а не всё подряд;
* состояние «было» восстанавливается целиком, включая чётность недели;
* откатить более раннюю правку, пока поздняя ещё действует, нельзя —
  иначе на расписании окажется состояние, которого не публиковали;
* вернуть уже возвращённое нельзя повторно;
* действие доступно суперпользователю: оно переписывает живое расписание.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from schedule.drafts import CannotUndo, publish, restore
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


class UndoFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.slot = LessonTime.objects.create(number=1, start='09:00', end='10:20')
        cls.subject = Subject.objects.create(name='Матан', short_name='Матем')
        cls.group = Group.objects.create(name='6301', course=1)
        cls.teacher = Teacher.objects.create(full_name='Холбаева С.Р.')
        cls.room = Room.objects.create(name='201')
        cls.lesson = Lesson.objects.create(
            group=cls.group, subject=cls.subject, lesson_time=cls.slot,
            day=1, teacher=cls.teacher, room=cls.room, week_parity=0,
            note='ЗАМЕТКА ПРЕПОДАВАТЕЛЯ',
        )

    def move(self, day, note='перенос'):
        draft = LessonDraft.objects.create(
            action='update', lesson=self.lesson, subject=self.subject,
            group=self.group, day=day, lesson_time=self.slot,
            teacher=self.teacher, room=self.room, note=note)
        publish([draft], None)
        # Последняя публикация: их может быть несколько подряд.
        return DraftPublication.objects.order_by('published_at').last()

    def now(self):
        return Lesson.objects.get(pk=self.lesson.pk)


class UndoUpdateTests(UndoFixture):
    def test_returns_the_lesson(self):
        self.move(day=4)
        entry = DraftPublication.objects.get()
        restore(entry, None)
        self.assertEqual(self.now().day, 1)

    def test_returns_teacher_and_room(self):
        self.move(day=4)
        restore(DraftPublication.objects.get(), None)
        after = self.now()
        self.assertEqual(after.teacher_id, self.teacher.pk)
        self.assertEqual(after.room_id, self.room.pk)

    def test_restores_week_parity(self):
        self.move(day=4)
        restore(DraftPublication.objects.get(), None)
        self.assertEqual(self.now().week_parity, 0)

    def test_restores_note_of_the_lesson(self):
        """Пояснение сотрудницы не должно было попасть в заметку."""
        self.move(day=4, note='перенос на четверг')
        restore(DraftPublication.objects.get(), None)
        self.assertEqual(self.now().note, 'ЗАМЕТКА ПРЕПОДАВАТЕЛЯ')

    def test_marks_entry_as_undone(self):
        entry = self.move(day=4)
        restore(entry, None)
        entry.refresh_from_db()
        self.assertIsNotNone(entry.undone_at)

    def test_records_who_undid(self):
        user = get_user_model().objects.create_superuser(
            'boss', 'boss@example.com', 'pass-12345678')
        entry = self.move(day=4)
        restore(entry, user)
        entry.refresh_from_db()
        self.assertEqual(entry.undone_by_id, user.pk)

    def test_cannot_undo_twice(self):
        entry = self.move(day=4)
        restore(entry, None)
        with self.assertRaises(CannotUndo):
            restore(entry, None)

    def test_undoing_leaves_no_trace_in_schedule(self):
        self.move(day=4)
        restore(DraftPublication.objects.get(), None)
        self.assertEqual(Lesson.objects.get(pk=self.lesson.pk).day, 1)


class UndoOrderTests(UndoFixture):
    """Возвращать можно только последнюю правку."""

    def test_earlier_publication_is_blocked(self):
        first = self.move(day=3, note='первый перенос')
        second = self.move(day=5, note='второй перенос')
        with self.assertRaises(CannotUndo):
            restore(first, None)
        # расписание осталось там, где было
        self.assertEqual(self.now().day, 5)

    def test_newest_first_then_earlier_works(self):
        first = self.move(day=3, note='первый')
        second = self.move(day=5, note='второй')
        restore(second, None)
        restore(first, None)
        self.assertEqual(self.now().day, 1)

    def test_undone_publication_does_not_block(self):
        first = self.move(day=3)
        second = self.move(day=5)
        restore(second, None)
        restore(first, None)
        self.assertEqual(self.now().day, 1)

    def test_refusal_explains_what_to_do(self):
        self.move(day=3)
        self.move(day=5)
        with self.assertRaises(CannotUndo) as caught:
            restore(DraftPublication.objects.order_by('published_at').first(),
                    None)
        self.assertIn('Сначала верните', str(caught.exception))


class UndoCreateTests(UndoFixture):
    def test_undo_removes_created_lesson(self):
        draft = LessonDraft.objects.create(
            action='create', subject=self.subject, group=self.group,
            day=2, lesson_time=self.slot, teacher=self.teacher,
            room=self.room, note='добавленная пара')
        publish([draft], None)
        self.assertEqual(Lesson.objects.count(), 2)
        restore(DraftPublication.objects.get(), None)
        self.assertEqual(Lesson.objects.count(), 1)

    def test_cannot_undo_if_lesson_already_gone(self):
        draft = LessonDraft.objects.create(
            action='create', subject=self.subject, group=self.group,
            day=2, lesson_time=self.slot, teacher=self.teacher,
            room=self.room, note='добавленная пара')
        publish([draft], None)
        entry = DraftPublication.objects.get()
        created = Lesson.objects.exclude(pk=self.lesson.pk).get()
        created.delete()
        with self.assertRaises(CannotUndo):
            restore(entry, None)


class UndoDeleteTests(UndoFixture):
    def cancel(self):
        draft = LessonDraft.objects.create(action='delete',
                                           lesson=self.lesson, note='отмена')
        publish([draft], None)
        return DraftPublication.objects.get()

    def test_undo_brings_the_lesson_back(self):
        self.cancel()
        self.assertEqual(Lesson.objects.count(), 0)
        restore(DraftPublication.objects.get(), None)
        self.assertEqual(Lesson.objects.count(), 1)

    def test_restored_lesson_keeps_all_fields(self):
        self.cancel()
        restore(DraftPublication.objects.get(), None)
        back = Lesson.objects.get()
        self.assertEqual(back.group_id, self.group.pk)
        self.assertEqual(back.subject_id, self.subject.pk)
        self.assertEqual(back.teacher_id, self.teacher.pk)
        self.assertEqual(back.room_id, self.room.pk)
        self.assertEqual(back.day, 1)
        self.assertEqual(back.note, 'ЗАМЕТКА ПРЕПОДАВАТЕЛЯ')

    def test_blocked_when_slot_already_taken(self):
        """Если на место уже поставили другое — возвращать нельзя."""
        self.cancel()
        Lesson.objects.create(group=self.group, subject=self.subject,
                              lesson_time=self.slot, day=1,
                              teacher=self.teacher, room=self.room)
        with self.assertRaises(CannotUndo):
            restore(DraftPublication.objects.get(), None)
        self.assertEqual(Lesson.objects.count(), 1)


class UndoAdminTests(UndoFixture):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            'boss', 'boss@example.com', 'pass-12345678')
        self.client.force_login(self.user)

    def test_journal_page_lists_entries(self):
        self.move(day=4)
        resp = self.client.get('/admin/schedule/draftpublication/')
        self.assertEqual(resp.status_code, 200)

    def test_undo_action_registered_for_superuser(self):
        from django.contrib import admin as dj_admin
        from django.test import RequestFactory

        from schedule.models import DraftPublication as Journal
        request = RequestFactory().get('/admin/schedule/draftpublication/')
        request.user = self.user
        model_admin = dj_admin.site._registry[Journal]
        self.assertIn('undo_selected', model_admin.get_actions(request))

    def test_undo_hidden_from_regular_staff(self):
        from django.contrib import admin as dj_admin
        from django.test import RequestFactory

        from schedule.models import DraftPublication as Journal
        staff = get_user_model().objects.create_user(
            'staff', 'staff@example.com', 'pass-12345678', is_staff=True)
        request = RequestFactory().get('/admin/schedule/draftpublication/')
        request.user = staff
        model_admin = dj_admin.site._registry[Journal]
        self.assertNotIn('undo_selected', model_admin.get_actions(request))

    def test_journal_is_read_only(self):
        """Руками записи журнала не трогают: руками их нельзя и создать."""
        resp = self.client.get('/admin/schedule/draftpublication/add/')
        self.assertIn(resp.status_code, (302, 403))

    def test_state_column_shows_undone(self):
        self.move(day=4)
        entry = DraftPublication.objects.get()
        restore(entry, None)
        html = self.client.get('/admin/schedule/draftpublication/').content.decode()
        self.assertIn('возвращено', html)