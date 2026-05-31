from django.urls import path
from apps.reports.views.reports import ExportReportView

urlpatterns = [
    path('course-info/<uuid:uuid>/export/', ExportReportView.as_view(), name='export-report'),
]
