"""Settings from the environment (API v2 section 10) and the keys listed in .env.example."""
import importlib.util
import os
import re
from unittest import mock

import decouple
from django.conf import settings
from django.test import SimpleTestCase

BASE_SETTINGS = settings.BASE_DIR / 'config' / 'settings' / 'base.py'
ENV_EXAMPLE = settings.BASE_DIR / '.env.example'
APP_KEYS = (
    'WEB_URL', 'MIN_APP_VERSION', 'LATEST_APP_VERSION', 'ATTENDANCE_MIN_PERCENT',
    'EMAIL_HOST_USER', 'EMAIL_HOST_PASSWORD', 'DEFAULT_FROM_EMAIL',
    'THROTTLE_LOGIN', 'THROTTLE_REGISTER', 'THROTTLE_PASSWORD_FORGOT', 'THROTTLE_CHECK_IN', 'NUM_PROXIES',
)


def load_base_settings(**env):
    """Runs config/settings/base.py with only `env` set for the app keys (no .env file read)."""
    spec = importlib.util.spec_from_file_location('base_settings_probe', BASE_SETTINGS)
    module = importlib.util.module_from_spec(spec)
    no_env_file = decouple.Config(decouple.RepositoryEmpty())
    with mock.patch.dict(os.environ, env), mock.patch('decouple.config', no_env_file):
        for key in APP_KEYS:
            if key not in env:
                os.environ.pop(key, None)
        spec.loader.exec_module(module)
    return module


class SettingsTests(SimpleTestCase):
    def test_defaults(self):
        s = load_base_settings()
        self.assertEqual(s.WEB_URL, 'https://attendanceportal.sakibkx.tech')
        self.assertEqual((s.MIN_APP_VERSION, s.LATEST_APP_VERSION), ('1.0.0', '1.0.0'))
        self.assertEqual(s.ATTENDANCE_MIN_PERCENT, 75)
        self.assertEqual((s.EMAIL_HOST, s.EMAIL_PORT, s.EMAIL_USE_TLS), ('smtp.gmail.com', 587, True))
        self.assertEqual(s.EMAIL_HOST_USER, '')  # empty = password reset by email is off
        self.assertEqual(s.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'], {
            'login': '10/min', 'register': '5/hour', 'password_forgot': '5/hour', 'check_in': '30/min',
        })

    def test_values_from_env(self):
        s = load_base_settings(
            WEB_URL='https://portal.example/', MIN_APP_VERSION='1.2.0', LATEST_APP_VERSION='1.4.1',
            ATTENDANCE_MIN_PERCENT='80', EMAIL_HOST_USER='portal@gmail.com', EMAIL_HOST_PASSWORD='not-a-real-one',
            THROTTLE_CHECK_IN='60/min',
        )
        self.assertEqual(s.WEB_URL, 'https://portal.example')  # no trailing slash
        self.assertEqual((s.MIN_APP_VERSION, s.LATEST_APP_VERSION), ('1.2.0', '1.4.1'))
        self.assertEqual(s.ATTENDANCE_MIN_PERCENT, 80)
        self.assertEqual(s.EMAIL_HOST_PASSWORD, 'not-a-real-one')
        self.assertEqual(s.DEFAULT_FROM_EMAIL, 'HSTU Attendance Portal <portal@gmail.com>')
        self.assertEqual(s.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']['check_in'], '60/min')

    def test_env_example_lists_the_keys_with_comments_on_their_own_lines(self):
        lines = ENV_EXAMPLE.read_text(encoding='utf-8').splitlines()
        listed = {re.match(r'#?\s*([A-Z_]+)=', line).group(1) for line in lines if re.match(r'#?\s*[A-Z_]+=', line)}
        self.assertLessEqual(set(APP_KEYS), listed)
        for line in lines:
            if re.match(r'[A-Z_]+=', line):  # a value line: python-decouple would keep a trailing comment
                self.assertNotIn(' #', line, line)
