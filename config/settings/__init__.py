import os

# "config.settings" on its own means the development settings. When another
# module here is chosen (e.g. --settings=config.settings.test), skip this so
# that module can load without the values from .env.
if os.environ.get('DJANGO_SETTINGS_MODULE', 'config.settings') == 'config.settings':
    from .development import *  # noqa: F401,F403
