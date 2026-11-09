from django.urls import path
from apps.users.views.config import AppConfigView, CreditsConfigView, FacultiesConfigView, DepartmentsConfigView

urlpatterns = [
    path('app/', AppConfigView.as_view(), name='config-app'),
    path('credits/', CreditsConfigView.as_view(), name='config-credits'),
    path('faculties/', FacultiesConfigView.as_view(), name='config-faculties'),
    path('departments/', DepartmentsConfigView.as_view(), name='config-departments'),
]
