from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny

from apps.users.constants import APP_NAME, DEPARTMENT_NAME, DOWNLOAD_URL, FACULTY_NAME, LEVELS, TERMS
from apps.users.services import email_reset_enabled


CREDIT_ENUM_MAP = {
    '1.00': 'CREDIT_1_00',
    '1.50': 'CREDIT_1_50',
    '2.00': 'CREDIT_2_00',
    '3.00': 'CREDIT_3_00',
}

FACULTIES = [
    'COMPUTER_SCIENCE_AND_ENGINEERING',
    'ENGINEERING',
    'AGRICULTURE',
    'BUSINESS_STUDIES',
]

DEPARTMENTS = [
    'COMPUTER_SCIENCE_AND_ENGINEERING',
    'INFORMATION_AND_COMMUNICATION_TECHNOLOGY',
    'ELECTRICAL_AND_ELECTRONIC_ENGINEERING',
    'AGRICULTURE_CHEMISTRY',
]


class CreditsConfigView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({
            'success': True,
            'creditEnumMap': CREDIT_ENUM_MAP,
            'revCreditMap': {v: k for k, v in CREDIT_ENUM_MAP.items()},
        })


class FacultiesConfigView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({'success': True, 'faculties': FACULTIES})


class DepartmentsConfigView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({'success': True, 'departments': DEPARTMENTS})


class AppConfigView(APIView):
    """Public app settings: names, the attendance minimum, app versions, level/term lists."""
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        return Response({
            'success': True,
            'app_name': APP_NAME,
            'faculty': FACULTY_NAME,
            'department': DEPARTMENT_NAME,
            'attendance_min_percent': settings.ATTENDANCE_MIN_PERCENT,
            'email_reset_enabled': email_reset_enabled(),
            'min_app_version': settings.MIN_APP_VERSION,
            'latest_app_version': settings.LATEST_APP_VERSION,
            'download_url': DOWNLOAD_URL,
            'levels': LEVELS,
            'terms': TERMS,
        })
