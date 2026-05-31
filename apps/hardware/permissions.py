from rest_framework.permissions import BasePermission
from .models import HardwareDevice


class IsHardwareDevice(BasePermission):
    """
    Grants access only if the request was authenticated via HardwareAPIKeyAuthentication.
    request.auth will be a HardwareDevice instance.
    """
    message = 'Valid hardware API key required.'

    def has_permission(self, request, view):
        return isinstance(request.auth, HardwareDevice)
