from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    # «В расписание» из админки
    path('admin/schedule/', RedirectView.as_view(pattern_name='schedule:index')),
    path('admin/', admin.site.urls),
    path('', include('schedule.urls')),
]