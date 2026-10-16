"""Разбор занятий, у которых не определилась группа.

Импорт расписания удалён, поэтому теперь такие записи могут появиться
только при ручном вводе: сотрудница создала занятие и не выбрала группу
— опечатка, пустое поле или «добавлю позже». Поле осталось
необязательным намеренно: иногда занятие правят раньше, чем известна
группа.

Такие записи опасны тем, что их **почти не видно**. ``_teacher_lessons``
отбирает занятия по преподавателю, поэтому запись без группы, но **с
преподавателем** всё же попадёт в расписание этого преподавателя. А вот
запись без преподавателя не попадает никуда: ни группы, ни преподавателя,
ни аудитории у неё нет. В базе она есть, на сайте её нет, и заметить
можно только здесь.

Этот модуль даёт ответ на два вопроса:

* **что** не разобралось — с исходным текстом, чтобы решить, группа
  это или предмет, и написать нужное;
* **как починить** — проставить группу одной кнопкой либо удалить
  запись, если она явно лишняя.

Правила починки:

* группа ставится только если её текст совпадает с существующей
  существенно — сравнение идёт по нормализованному ключу, а не «по
 х��дному совпадению подстроки»;
* если подходящей группы нет, запись не чинится автоматически: группа
  создаётся вручную, потому что номер курса в названии догадываться
  опасно;
* удаление требует подтверждения и показывает, что именно пропадёт.
"""

import re

from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import path, reverse
from django.utils.html import format_html

from .models import Group, Lesson
from .normkeys import norm_key

# Разделители, которыми в ячейке перечисляют части занятия.
_SPLIT_RE = re.compile(r'\s*(?:/|·|,|;|\n)\s*')

# Хвост, который в файле дописывают к номеру группы: «4301 (26 ст.)».
_GROUP_TAIL_RE = re.compile(r'\s*\(.*\)\s*$')


def _parts_of(text):
    """Части ячейки, приведённые к сравнимому виду.

    Хвост «(26 ст.)» отбрасывается: он описывает количество студентов и
    к номеру группы отношения не имеет. Без этого «4301 (26 ст.)» не
    совпало бы ни с одной группой, хотя в файле группа названа прямо.
    """
    parts = []
    for part in _SPLIT_RE.split(text or ''):
        part = part.strip()
        if not part:
            continue
        parts.append(_GROUP_TAIL_RE.sub('', part))
    return [p for p in parts if p]


def candidate_groups(lesson):
    """Подходящие группы для занятия, отсортированные по убыванию совпадения.

    Возвращает список пар ``(Group, score)``, где score — сколько
    совпало частей из ``raw_text``. Пустой список означает «подобрать
    нечего, разбирать вручную».

    Сравнение **строгое**, по частям целиком. Раньше проверялось вхождение
    подстроки, и это давало ложные подсказки: ``G1`` считалась
    подходящей к тексту ``EnG1 / …``, потому что ``g1`` входит в ``eng1``.
    Сотрудница нажал бы «проставить G1» — и занятие уехало бы не в ту
    группу.

    Нормализация всё же нужна: ``EnG1``, ``eng-1`` и ``ЭНГ 1`` — это одна
    группа, и импорт как раз такие различия не узнаёт.
    """
    if lesson.group_id is not None:
        return []

    parts = [norm_key(part) for part in _parts_of(lesson.raw_text)]
    if not parts:
        return []

    scored = []
    for group in Group.objects.all().order_by('name'):
        key = norm_key(group.name)
        score = sum(1 for part in parts if part and part == key)
        if score:
            scored.append((group, score))

    # Сначала те, что совпали большим числом частей. При равенстве — по
    # имени: список должен быть стабильным между запусками, иначе
    # подсказка «возможно, это» менялась бы сама по себе.
    scored.sort(key=lambda pair: (-pair[1], pair[0].name))
    return scored


def groupless_lessons():
    """Все занятия без группы, новые сверху."""
    return (
        Lesson.objects
        .filter(group__isnull=True)
        .select_related('lesson_time', 'subject', 'teacher', 'room')
        .order_by('-day', 'lesson_time__number')
    )


def suggestions_for(lesson):
    """Группы, которые, скорее всего, имелись в виду.

    Только если совпадение единственное и точное: если подходит пять
    групп, это не подсказка, а шум, и сотрудница выберет наугад.
    """
    scored = candidate_groups(lesson)
    if len(scored) != 1:
        return None
    group, score = scored[0]
    return group if score else None


class GrouplessLessonsAdmin(admin.ModelAdmin):
    """Страница разбора занятий без группы.

    Это не замена стандартному списку занятий, а очередь на разбор:
    сюда попадают только те записи, которые не видны в расписании и
    потому иначе просто забыты.
    """

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        """Список только для чтения: правка — через карточку занятия.

        Иначе «быстрое редактирование» в таблице стёрло бы ``raw_text``,
        а это единственная копия того, что было в файле.
        """
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_staff

    def changelist_view(self, request, extra_context=None):
        """Список с подсказками групп.

        Сама страница — обычный changelist, но с добавленным
        ``get_extra_context``: Django не умеет считать подсказки на
        этапе формирования queryset, а здесь нужен разбор текста
        каждой записи.
        """
        lessons = list(groupless_lessons())
        rows = []
        for lesson in lessons:
            scored = candidate_groups(lesson)
            exact = suggestions_for(lesson)
            rows.append({
                'lesson': lesson,
                'suggestion': exact,
                'alternatives': [g for g, _ in scored[:5]] if not exact else [],
                'partially_visible': lesson.teacher_id is not None,
            })

        context = {
            **self.admin_site.each_context(request),
            'title': 'Сырые занятия: не определилась группа',
            'cl': None,
            'rows': rows,
            'total': len(lessons),
            'invisible': sum(1 for r in rows if not r['partially_visible']),
            'opts': self.model._meta,
            'changelist_form': False,
            **(extra_context or {}),
        }
        return render(request, 'admin/schedule_groupless.html', context)

    def _redirect_back(self):
        return HttpResponseRedirect(reverse('admin:schedule_lesson_changelist'))

    def assign_group(self, request, lesson_id, group_id):
        """Проставляет группу одному занятию.

        Группа проверяется ещё раз: между показом страницы и нажатием
        кнопки она могла быть удалена, и тогда занятие осталось бы с
        несуществующей ссылкой.
        """
        lesson = groupless_lessons().filter(pk=lesson_id).first()
        if lesson is None:
            messages.error(request, 'Занятие уже не в списке сырых.')
            return self._redirect_back()

        group = Group.objects.filter(pk=group_id).first()
        if group is None:
            messages.error(request, 'Группа не найдена — возможно, её удалили.')
            return self._redirect_back()

        lesson.group = group
        lesson.save()
        messages.success(
            request,
            f'Группа «{group.name}» проставлена: занятие больше не сырое.',
        )
        return self._redirect_back()

    def delete_lesson(self, request, lesson_id):
        """Удаляет запись по кнопке.

        Раньше удаление было запрещено вовсе (см. ``LessonAdmin``): текст
        неразобранной ячейки — единственная копия из файла. Но очередь
        без выхода бесполезна: записи копятся годами. Поэтому удаление
        доступно, но явно и с показом того, что пропадёт.
        """
        lesson = groupless_lessons().filter(pk=lesson_id).first()
        if lesson is None:
            messages.error(request, 'Занятие уже не в списке сырых.')
            return self._redirect_back()

        if request.method != 'POST':
            messages.warning(request, 'Удаление требует подтверждения.')
            return self._redirect_back()

        lesson.delete()
        messages.success(request, 'Запись удалена из базы.')
        return self._redirect_back()

    def get_urls(self):
        return [
            path(
                'assign-group/<int:lesson_id>/<int:group_id>/',
                self.admin_site.admin_view(self.assign_group),
                name='schedule_lesson_assign_group',
            ),
            path(
                'delete-raw/<int:lesson_id>/',
                self.admin_site.admin_view(self.delete_lesson),
                name='schedule_lesson_delete_raw',
            ),
        ] + super().get_urls()


# Страница не регистрируется через admin.register: иначе она заняла бы
# место модели Lesson в меню и обычный список занятий стал бы недоступен.
# Поэтому это отдельный URL на том же AdminSite.
admin.site._groupless_admin = GrouplessLessonsAdmin(Lesson, admin.site)

_orig_admin_get_urls = admin.site.get_urls


def _get_urls_with_groupless():
    """Добавляет URL разбора к списку админки.

    Подменяется метод ``get_urls`` у экземпляра, а не классом: смена
    класса у ``admin.site`` затронула бы все приложения сразу и потребовала
    бы соблюдения чужих соглашений.

Важно, куда вставляются адреса. У админки есть два «всё подряд»
    маршрута: ``app_list`` (``<app_label>/``) и ``catch_all``
    (``<url>`` без имени), который молча переводит на главную. Свои адреса
    ставятся строго между ними:

    * после ``catch_all`` — до разбора не дойдёт, перехватит он;
    * до ``app_list`` — ``schedule/groupless/`` разберётся как
      ``app_label='schedule'`` и получится редирект.

    ``catch_all`` в Django 5 добавлен последним и **без имени**, поэтому
    найти его по ``name`` нельзя. Ориентир — позиция: он всегда последний
    в списке, и свои адреса ставятся перед ним.
    """
    from django.urls import path

    extra = [
        path(
            'schedule/groupless/',
            admin.site.admin_view(admin.site._groupless_admin.changelist_view),
            name='schedule_groupless',
        ),
        path(
            'schedule/groupless/assign/<int:lesson_id>/<int:group_id>/',
            admin.site.admin_view(admin.site._groupless_admin.assign_group),
            name='schedule_lesson_assign_group',
        ),
        path(
            'schedule/groupless/delete/<int:lesson_id>/',
            admin.site.admin_view(admin.site._groupless_admin.delete_lesson),
            name='schedule_lesson_delete_raw',
        ),
    ]
    urls = list(_orig_admin_get_urls())
    # Django всегда ставит catch_all последним: https://docs.djangoproject.com
    # /en/5.2/ref/contrib/admin/#overriding-the-base-websites
    return urls[:-1] + extra + urls[-1:] if urls else urls + extra


admin.site.get_urls = _get_urls_with_groupless