from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from .health import healthz

urlpatterns = [
    # Health-check объявлен первым и живёт вне приложения schedule: он должен
    # отвечать, даже если приложение не загрузилось из-за ошибки. Путь без
    # имени приложения, чтобы мониторинг не зависел от /schedule/.
    path('healthz/', healthz, name='healthz'),
    # «Админка расписания» → «Расписание»
    path('admin/schedule/', RedirectView.as_view(pattern_name='schedule:index')),
    # Кто вправе открыть админку, решает AdminGateMiddleware: админка — это
    # подключение с десятками адресов, а закрыть их должна одна проверка.
    path('admin/', admin.site.urls),
    path('', include('schedule.urls')),
]
