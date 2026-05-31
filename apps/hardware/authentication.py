"""
ESP32 devices authenticate with a static API key in the request header:
    X-Hardware-Key: <api_key>

Usage on views:
    from apps.hardware.authentication import HardwareAPIKeyAuthentication
"""
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from .models import HardwareDevice


class HardwareAPIKeyAuthentication(BaseAuthentication):
    """
    Custom DRF authenticator for ESP32 hardware devices.
    Reads X-Hardware-Key header and matches it to a HardwareDevice.
    Sets request.auth = HardwareDevice instance on success.
    """
    keyword = 'X-Hardware-Key'

    def authenticate(self, request):
        api_key = request.headers.get(self.keyword)
        if not api_key:
            return None  # Let other authenticators try

        try:
            device = HardwareDevice.objects.get(api_key=api_key, is_active=True)
        except HardwareDevice.DoesNotExist:
            raise AuthenticationFailed('Invalid or inactive hardware API key.')

        # Update last ping
        from django.utils import timezone
        HardwareDevice.objects.filter(pk=device.pk).update(last_ping=timezone.now())

        # Return (user=None, auth=device) — hardware requests have no user
        return (None, device)

    def authenticate_header(self, request):
        return self.keyword
