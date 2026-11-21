import os

if os.environ.get('DJANGO_SETTINGS_MODULE', 'config.settings') == 'config.settings':
    from .development import *  # noqa: F401,F403
