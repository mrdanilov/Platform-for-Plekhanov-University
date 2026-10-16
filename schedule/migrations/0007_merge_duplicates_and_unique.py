"""Слияние дублей справочников и включение уникальности по нормализованному ключу.

Порядок операций внутри одной миграции выбран не случайно:

1. ``fill_normalized_keys`` — проставить ключи всем существующим строкам.
2. ``merge_duplicates`` — слить то, что уже разошлось регистром или «ё».
3. Только затем ``AddConstraint`` — ограничение ставится на уже чистые данные.

Если поставить ограничение вторым шагом, миграция упадёт на первой же паре
дублей, а на живой базе это означает остановку деплоя на середине. Откатить
слитые дубли нельзя, поэтому перед этой миграцией обязателен бэкап базы.
"""

from django.db import migrations, models

from schedule.merge_duplicates import fill_normalized_keys, merge_duplicates


def forwards(apps, schema_editor):
    """Заполняет ключи, сливает дубли, печатает отчёт.

    Здесь исторические модели: ``apps.get_model`` отдаёт то состояние схемы,
    которое было на этом шаге истории. Живые модели здесь ломали бы сборку
    базы с нуля — добавленное позже поле попало бы в запрос и упало с «no
    such column». Само слияние от этого не меняется: нормализованные ключи
    считаются тем же правилом, что и в ``schedule/normkeys.py``.
    """
    fill_normalized_keys(apps=apps)
    report = merge_duplicates(verbose=False, apps=apps)
    for kind, groups in sorted(report.items()):
        for group in groups:
            # без стрелки: консоль Windows может быть в cp1251 и не примет
            # символы вроде «←»
            print(
                f'  слито дублей [{kind}]: «{group["kept"]}» '
                f'(pk {group["kept_pk"]}) из {group["merged_pks"]}'
            )


def backwards(apps, schema_editor):
    """Ограничения снимаются, данные не восстанавливаются.

    Вернуть слитые дубли можно только из бэкапа: объекты удалены, а
    уникальность на точных значениях их не вернёт.
    """


class Migration(migrations.Migration):

    dependencies = [
        ('schedule', '0006_add_normalized_keys'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
        migrations.AddConstraint(
            model_name='teacher',
            constraint=models.UniqueConstraint(
                fields=('norm_name',), name='uniq_teacher_norm_name',
                violation_error_message=(
                    'Преподаватель с таким ФИО уже есть — отличается только '
                    'регистр или буква «ё».'
                ),
            ),
        ),
        migrations.AddConstraint(
            model_name='subject',
            constraint=models.UniqueConstraint(
                fields=('norm_name',), name='uniq_subject_norm_name',
                violation_error_message=(
                    'Предмет с таким названием уже есть — отличается только '
                    'регистр или буква «ё».'
                ),
            ),
        ),
        migrations.AddConstraint(
            model_name='lessontype',
            constraint=models.UniqueConstraint(
                fields=('norm_name',), name='uniq_lessontype_norm_name',
                violation_error_message=(
                    'Тип занятия с таким названием уже есть — отличается только '
                    'регистр или буква «ё».'
                ),
            ),
        ),
        migrations.AddConstraint(
            model_name='room',
            constraint=models.UniqueConstraint(
                fields=('norm_building', 'norm_name'), name='uniq_room_norm',
                violation_error_message=(
                    'Такая аудитория уже есть — отличается только регистр '
                    'или буква «ё».'
                ),
            ),
        ),
        migrations.AddConstraint(
            model_name='group',
            constraint=models.UniqueConstraint(
                fields=('norm_name', 'course'),
                name='uniq_group_norm_name_course',
                violation_error_message=(
                    'Группа с таким названием и курсом уже есть — отличается '
                    'только регистр или буква «ё».'
                ),
            ),
        ),
    ]
