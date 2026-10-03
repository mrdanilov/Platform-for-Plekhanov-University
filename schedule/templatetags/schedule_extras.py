from django import template

register = template.Library()


@register.simple_tag
def get_row_cells(cells, lesson_time_id):
    """cells -> {lesson_time_id: [..]} ; отдаёт список ячеек для колонки."""
    return cells.get(lesson_time_id) or []