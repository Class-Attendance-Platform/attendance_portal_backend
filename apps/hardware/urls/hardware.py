from django.urls import path
from apps.hardware.views.hardware import (
    HardwareDeviceListCreateView,
    HardwareDeviceDetailView,
    HardwareSyncView,
    EnrollStartView,
    EnrollCompleteView,
    EnrollStatusView,
    PendingEnrollmentsView,
)

urlpatterns = [
    # Admin — device management
    path('devices/', HardwareDeviceListCreateView.as_view(), name='hardware-devices'),
    path('devices/<uuid:uuid>/', HardwareDeviceDetailView.as_view(), name='hardware-device-detail'),

    # ESP32 webhooks (API key auth)
    path('sync/', HardwareSyncView.as_view(), name='hardware-sync'),
    path('enroll/complete/', EnrollCompleteView.as_view(), name='hardware-enroll-complete'),
    path('enroll/pending/', PendingEnrollmentsView.as_view(), name='hardware-enroll-pending'),

    # Student — initiate enrollment
    path('enroll/start/', EnrollStartView.as_view(), name='hardware-enroll-start'),

    # Student/Admin — poll enrollment status
    path('enroll/status/<uuid:uuid>/', EnrollStatusView.as_view(), name='hardware-enroll-status'),
]
