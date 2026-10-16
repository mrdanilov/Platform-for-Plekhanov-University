from django.contrib import admin
from django.urls import path

from . import views

app_name = 'schedule'

urlpatterns = [
    path('', views.index, name='index'),
    path('lang/', views.set_language, name='set_language'),
    path('today/', views.day_view, name='day'),
    path('block/', views.block, name='block'),
    path('list/', views.picker_list, name='picker_list'),
    path('groups/<int:pk>/', views.group_detail, name='group_detail'),
    path('teachers/<int:pk>/', views.teacher_detail, name='teacher_detail'),
    path('rooms/<int:pk>/', views.room_detail, name='room_detail'),
    path('university/', views.university, name='university'),
    path('university/captcha.png', views.university_captcha, name='university_captcha'),
    path('university/contact/', views.university_contact, name='university_contact'),
    # Импорт расписания удалён: сотрудницы заполняют базу вручную через
    # админку, а импорт был не только не нужен, но и опасен — кнопка
    # «Проверить файл» писала данные в базу, пока показывала «будет
    # создано» (audit_todos/01). Вместе с ним ушли и /import/, и
    # /import/template.csv: шаблон CSV существовал только как эталон
    # формата для загрузки.
    path('export/timetable.csv', views.export_csv_view, name='export'),
    path('export/timetable.xlsx', views.export_xlsx_view, name='export_xlsx'),
    # Раньше здесь был ещё path('export/current.xlsx', ...) с именем
    # export_xlsx_current — на ту же вьюху. Второй адрес удалён: одно имя на
    # ресурс, как у CSV. Ни в шаблонах, ни в тестах, ни в README адрес не
    # встречался, а наружу сайт не выставлен (задача 14).
]

admin.site.site_header = 'Расписание — администрирование'
admin.site.site_title = 'Расписание'
admin.site.index_title = 'Ввод данных'