"""
Removes student/teacher accounts that have no profile (made by the old sign-up bug:
the account was saved, then making the profile failed). They cannot use the app,
and they block their email from signing up again.

    python manage.py cleanup_broken_signups            # dry run: lists them
    python manage.py cleanup_broken_signups --apply    # removes them
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.users.models import User
from apps.users.services import blacklist_refresh_tokens


def broken_signups():
    return User.objects.filter(
        Q(role=User.Role.STUDENT, student_profile__isnull=True)
        | Q(role=User.Role.TEACHER, teacher_profile__isnull=True)
    ).order_by('date_joined')


class Command(BaseCommand):
    help = 'Remove student/teacher accounts without a profile (dry run unless --apply).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Actually remove the accounts.')

    def handle(self, *args, **options):
        users = list(broken_signups())
        if not users:
            self.stdout.write(self.style.SUCCESS('No broken sign-ups: nothing to do.'))
            return

        self.stdout.write(f'Accounts without a profile: {len(users)}')
        for user in users:
            last_login = timezone.localtime(user.last_login).strftime('%Y-%m-%d') if user.last_login else 'never'
            self.stdout.write(
                f'  {user.email} (role {user.role}, joined {timezone.localtime(user.date_joined):%Y-%m-%d}, '
                f'last login {last_login}{", deleted" if user.deleted else ""})'
            )

        if not options['apply']:
            self.stdout.write(self.style.WARNING('Dry run: nothing removed. Add --apply to remove them.'))
            return

        with transaction.atomic():
            for user in users:
                # Block login tokens first: refreshing a removed account's token would answer 500.
                blacklist_refresh_tokens(user)
                user.delete()
        self.stdout.write(self.style.SUCCESS(f'Removed {len(users)} account(s).'))
