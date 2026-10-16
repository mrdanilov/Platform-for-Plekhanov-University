"""Черновики правок: предпросмотр и публикация.

Сотрудница готовит правку отдельно от расписания. Пока правка в черновиках,
сайт показывает опубликованное — никто из посетителей не видит недоделанную
работу. Проверяется именно это, а вместе с тем главное свойство: то, что
показано в предпросмотре, и то, что окажется на сайте после публикации,
должно совпадать.

Если эти два расчёта разойдутся, сотрудница опубликует правку и увидит не
то, что одобрила, — поэтому правило применения описано один раз в
``drafts._apply_to`` и используется обоими путями.
"""

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from schedule.drafts import pending_drafts, preview_lessons, publish
from schedule.grid import monday_of
from schedule.models import (
    Group,
    Lesson,
    LessonDraft,
    LessonTime,
    Room,
    Subject,
    Teacher,
)


class DraftFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.slot1 = LessonTime.objects.create(number=1, start='09:00', end='10:20')
        cls.slot2 = LessonTime.objects.create(number=2, start='10:30', end='11:50')
        cls.subject = Subject.objects.create(name='Матан', short_name='Матем')
        cls.other = Subject.objects.create(name='Физика', short_name='Физ')
        cls.group = Group.objects.create(name='6301', course=1)
        cls.teacher = Teacher.objects.create(full_name='Соколова С.Р.')
        cls.substitute = Teacher.objects.create(full_name='Васильева Е.Р.')
        cls.room = Room.objects.create(name='201')
        cls.lesson = Lesson.objects.create(
            group=cls.group, subject=cls.subject, lesson_time=cls.slot1,
            day=1, teacher=cls.teacher, room=cls.room,
        )
        cls.monday = monday_of(timezone.localdate())

    def new_draft(self, **kwargs):
        data = {'action': 'create', 'subject': self.subject,
                'group': self.group, 'day': 1, 'lesson_time': self.slot2,
                'teacher': self.teacher, 'room': self.room,
                'note': 'перенос'}
        data.update(kwargs)
        return LessonDraft.objects.create(**data)


class SiteStaysUntouchedTests(DraftFixture):
    """Черновик не должен менять то, что видит посетитель."""

    def test_draft_does_not_touch_site(self):
        self.new_draft()
        self.assertEqual(Lesson.objects.count(), 1)
        self.assertEqual(
            Lesson.objects.get(pk=self.lesson.pk).lesson_time_id, self.slot1.pk)

    def test_draft_does_not_touch_existing_lesson(self):
        LessonDraft.objects.create(
            action='update', lesson=self.lesson, subject=self.other,
            group=self.group, day=3, lesson_time=self.slot2,
            teacher=self.substitute, room=self.room,
        )
        lesson = Lesson.objects.get(pk=self.lesson.pk)
        self.assertEqual(lesson.subject_id, self.subject.pk)
        self.assertEqual(lesson.day, 1)

    def test_public_view_ignores_drafts(self):
        self.new_draft()
        html = self.client.get(
            f'/?view=group&id={self.group.pk}').content.decode()
        self.assertNotIn('draft-new', html)
        self.assertEqual(html.count('class="lesson"'), 1)


class PreviewTests(DraftFixture):
    def preview(self, drafts=None):
        return preview_lessons(Lesson.objects.all(),
                               drafts if drafts is not None
                               else list(pending_drafts()))

    def test_new_lesson_appears_in_preview(self):
        self.new_draft()
        self.assertEqual(len(self.preview()), 2)

    def test_new_lesson_is_marked(self):
        self.new_draft()
        new = [l for l in self.preview() if getattr(l, '_draft_new', False)]
        self.assertEqual(len(new), 1)

    def test_update_is_visible(self):
        LessonDraft.objects.create(
            action='update', lesson=self.lesson, subject=self.other,
            group=self.group, day=1, lesson_time=self.slot2,
            teacher=self.substitute, room=self.room,
        )
        changed = [l for l in self.preview() if l.pk == self.lesson.pk][0]
        self.assertEqual(changed.lesson_time_id, self.slot2.pk)
        self.assertTrue(getattr(changed, '_draft_changed', False))

    def test_update_is_marked(self):
        LessonDraft.objects.create(
            action='update', lesson=self.lesson, subject=self.other,
            group=self.group, day=1, lesson_time=self.slot2,
            teacher=self.substitute, room=self.room,
        )
        changed = [l for l in self.preview() if l.pk == self.lesson.pk][0]
        self.assertTrue(changed._draft_changed)

    def test_deleted_lesson_disappears(self):
        LessonDraft.objects.create(action='delete', lesson=self.lesson,
                                   note='отменили')
        self.assertEqual(self.preview(), [])

    def test_preview_does_not_write_to_database(self):
        self.new_draft()
        self.preview()
        self.assertEqual(Lesson.objects.count(), 1)

    def test_preview_matches_publish(self):
        """Главное свойство: предпросмотр совпадает с результатом публикации.

        Расхождение означало бы, что сотрудница одобрила одно, а сайт
        показал другое.
        """
        draft = self.new_draft()
        expected = [(l.subject_id, l.group_id, l.day, l.lesson_time_id,
                     l.teacher_id) for l in self.preview()]
        publish([draft], None)
        real = [(l.subject_id, l.group_id, l.day, l.lesson_time_id,
                 l.teacher_id) for l in Lesson.objects.all()]
        self.assertEqual(sorted(expected), sorted(real))

    def test_update_preview_matches_publish(self):
        draft = LessonDraft.objects.create(
            action='update', lesson=self.lesson, subject=self.other,
            group=self.group, day=2, lesson_time=self.slot2,
            teacher=self.substitute, room=self.room,
        )
        previewed = [l for l in self.preview() if l.pk == self.lesson.pk][0]
        publish([draft], None)
        real = Lesson.objects.get(pk=self.lesson.pk)
        self.assertEqual(previewed.day, real.day)
        self.assertEqual(previewed.lesson_time_id, real.lesson_time_id)
        self.assertEqual(previewed.subject_id, real.subject_id)
        self.assertEqual(previewed.teacher_id, real.teacher_id)

    def test_delete_preview_matches_publish(self):
        draft = LessonDraft.objects.create(action='delete',
                                           lesson=self.lesson, note='—')
        self.assertEqual(self.preview(), [])
        publish([draft], None)
        self.assertEqual(Lesson.objects.count(), 0)


class PublishTests(DraftFixture):
    def test_publish_creates_lesson(self):
        publish([self.new_draft()], None)
        self.assertEqual(Lesson.objects.count(), 2)

    def test_publish_applies_update(self):
        draft = LessonDraft.objects.create(
            action='update', lesson=self.lesson, subject=self.other,
            group=self.group, day=4, lesson_time=self.slot2,
            teacher=self.substitute, room=self.room,
        )
        publish([draft], None)
        lesson = Lesson.objects.get(pk=self.lesson.pk)
        self.assertEqual(lesson.day, 4)
        self.assertEqual(lesson.subject_id, self.other.pk)

    def test_publish_applies_delete(self):
        draft = LessonDraft.objects.create(action='delete',
                                           lesson=self.lesson, note='—')
        publish([draft], None)
        self.assertFalse(Lesson.objects.filter(pk=self.lesson.pk).exists())

    def test_publish_marks_draft(self):
        draft = self.new_draft()
        publish([draft], None)
        draft.refresh_from_db()
        self.assertIsNotNone(draft.published_at)
        self.assertTrue(draft.is_published)

    def test_publish_records_author(self):
        user = get_user_model().objects.create_superuser(
            'boss', 'boss@example.com', 'pass-12345678')
        draft = self.new_draft()
        publish([draft], user)
        draft.refresh_from_db()
        self.assertEqual(draft.published_by_id, user.pk)

    def test_published_drafts_leave_pending(self):
        publish([self.new_draft()], None)
        self.assertEqual(pending_drafts().count(), 0)

    def test_draft_is_not_applied_twice(self):
        draft = self.new_draft()
        publish([draft], None)
        publish([draft], None)
        self.assertEqual(Lesson.objects.count(), 2)

    def test_failed_draft_rolls_the_whole_batch_back(self):
        """Ошибка на второй правке отменяет и первую.

        Половина применённых правок хуже, чем ни одной: сотрудница увидит
        одно, а расписание будет другим.
        """
        good = self.new_draft(day=5, lesson_time=self.slot2)
        # Вторая правка встанет на занятие, которое уже есть: та же группа,
        # день, пара и предмет. Ограничение уникальности не даст этому
        # пройти — публикация упадёт и откатит первую правку тоже.
        Lesson.objects.create(group=self.group, subject=self.subject,
                              lesson_time=self.slot1, day=3,
                              teacher=self.teacher, room=self.room)
        clash = LessonDraft.objects.create(
            action='update', lesson=self.lesson, subject=self.subject,
            group=self.group, day=3, lesson_time=self.slot1,
            teacher=self.teacher, room=self.room,
        )
        with self.assertRaises(IntegrityError):
            publish([good, clash], None)
        self.assertEqual(Lesson.objects.count(), 2)
        good.refresh_from_db()
        clash.refresh_from_db()
        self.assertIsNone(good.published_at)
        self.assertIsNone(clash.published_at)


class DraftModelTests(DraftFixture):
    def test_create_must_not_reference_lesson(self):
        draft = self.new_draft()
        self.assertIsNone(draft.lesson_id)
        self.assertTrue(draft.pending)

    def test_new_draft_shows_what_it_changes(self):
        text = self.new_draft().describe()
        self.assertIn('Матан', text)
        self.assertIn('6301', text)
        self.assertIn('2-я пара', text)

    def test_delete_describes_the_lesson(self):
        draft = LessonDraft.objects.create(action='delete',
                                           lesson=self.lesson, note='—')
        self.assertIn('Отменить', draft.describe())

    def test_label_is_readable(self):
        self.assertEqual(self.new_draft().get_action_display(), 'Новое занятие')

    def test_pending_only_counts_unpublished(self):
        draft = self.new_draft()
        self.assertIn(draft, pending_drafts())
        publish([draft], None)
        self.assertNotIn(draft, pending_drafts())


class DraftAdminTests(DraftFixture):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            'boss', 'boss@example.com', 'pass-12345678')
        self.client.force_login(self.user)

    def test_list_is_openable(self):
        resp = self.client.get('/admin/schedule/lessondraft/')
        self.assertEqual(resp.status_code, 200)

    def test_preview_link_on_list(self):
        html = self.client.get('/admin/schedule/lessondraft/').content.decode()
        self.assertIn('/admin/schedule/lessondraft/preview/', html)

    def test_preview_shows_draft_marked(self):
        self.new_draft()
        html = self.client.get(
            '/admin/schedule/lessondraft/preview/').content.decode()
        self.assertIn('draft-new', html)

    def test_preview_says_nothing_published_yet(self):
        self.new_draft()
        html = self.client.get(
            '/admin/schedule/lessondraft/preview/').content.decode()
        self.assertIn('предпросмотр', html.lower())

    def test_preview_creates_nothing(self):
        self.new_draft()
        self.client.get('/admin/schedule/lessondraft/preview/')
        self.assertEqual(Lesson.objects.count(), 1)

    def test_preview_closed_for_anonymous(self):
        self.client.logout()
        resp = self.client.get('/admin/schedule/lessondraft/preview/')
        self.assertIn(resp.status_code, (302, 403))

    def test_publish_action_is_registered(self):
        """Кнопка публикации зарегистрирована у модели.

        Проверяется через сам admin, а не по тексту в HTML: разметка
        подменяется вместе с шаблонами, а наличие действия — это контракт,
        на который опирается сотрудница.
        """
        from django.contrib import admin as dj_admin
        from django.test import RequestFactory

        model_admin = dj_admin.site._registry[LessonDraft]
        request = RequestFactory().get('/admin/schedule/lessondraft/')
        request.user = self.user
        self.assertIn('publish_selected', model_admin.get_actions(request))
        self.assertIn('discard_selected', model_admin.get_actions(request))

    def test_publish_hidden_from_non_superusers(self):
        """Публиковать может не всякий: это правка живого расписания."""
        from django.contrib import admin as dj_admin
        from django.test import RequestFactory

        staff = get_user_model().objects.create_user(
            'staff', 'staff@example.com', 'pass-12345678', is_staff=True)
        model_admin = dj_admin.site._registry[LessonDraft]
        request = RequestFactory().get('/admin/schedule/lessondraft/')
        request.user = staff
        self.assertNotIn('publish_selected', model_admin.get_actions(request))