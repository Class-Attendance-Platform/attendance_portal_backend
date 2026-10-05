"""
Removes one account, after showing everything that goes with it.

    python manage.py remove_user someone@example.com            # dry run: shows what would change
    python manage.py remove_user someone@example.com --apply    # removes it

Refuses to remove the last active admin, so nobody gets locked out.
"""
from django.contrib.admin.utils import NestedObjects
from django.core.management.base import BaseCommand, CommandError
from django.db import DEFAULT_DB_ALIAS, transaction
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from apps.users.models import User


class Command(BaseCommand):
    help = 'Remove one account and everything that belongs to it (dry run unless --apply).'

    def add_arguments(self, parser):
        parser.add_argument('email', help='Email address of the account to remove.')
        parser.add_argument('--apply', action='store_true', help='Actually remove the account.')

    def handle(self, *args, **options):
        email = options['email'].strip()
        users = list(User.objects.filter(email__iexact=email))
        if not users:
            raise CommandError(f'No account with the email {email}.')
        if len(users) > 1:  # emails are unique, but only with the exact same capitals
            found = ', '.join(u.email for u in users)
            raise CommandError(f'Several accounts match {email} ({found}): give the exact email.')
        user = users[0]

        if self._is_last_admin(user):
            raise CommandError(
                f'Refusing: {user.email} is the only active admin. Create another admin first.'
            )

        # The same lookup Django's admin uses for its "are you sure?" page
        collector = NestedObjects(using=DEFAULT_DB_ALIAS)
        collector.collect([user])
        if collector.protected:
            raise CommandError('Refusing: other records still need this account and block removing it.')

        last_login = timezone.localtime(user.last_login).strftime('%Y-%m-%d') if user.last_login else 'never'
        self.stdout.write(
            f'Account: {user.email} (role {user.role}, superuser {"yes" if user.is_superuser else "no"}, '
            f'active {"yes" if user.is_active else "no"}, joined '
            f'{timezone.localtime(user.date_joined):%Y-%m-%d}, last login {last_login})'
        )
        self.stdout.write('Removed with it:')
        for model, objs in sorted(collector.model_objs.items(), key=lambda item: item[0]._meta.label):
            self.stdout.write(f'  {model._meta.verbose_name_plural}: {len(objs)}')

        unlinked = []
        for (field, _value), batches in collector.field_updates.items():
            count = sum(len(batch) for batch in batches)
            if count:
                unlinked.append(f'  {field.model._meta.verbose_name_plural}: {count} (their {field.name} is cleared)')
        if unlinked:
            self.stdout.write('Kept, but no longer linked to this account:')
            for line in sorted(unlinked):
                self.stdout.write(line)

        if not options['apply']:
            self.stdout.write(self.style.WARNING('Dry run: nothing removed. Add --apply to remove it.'))
            return

        with transaction.atomic():
            # Block its login tokens first: refreshing a token of a removed account would
            # otherwise crash the refresh endpoint (500) instead of answering 401.
            tokens = OutstandingToken.objects.filter(user=user).exclude(blacklistedtoken__isnull=False)
            blocked = BlacklistedToken.objects.bulk_create([BlacklistedToken(token=t) for t in tokens])
            user.delete()
        self.stdout.write(f'Blocked {len(blocked)} login token(s).')
        self.stdout.write(self.style.SUCCESS(f'Removed {user.email}.'))

    @staticmethod
    def _is_last_admin(user):
        if user.role != User.Role.ADMIN or not user.is_active or user.deleted:
            return False
        others = User.objects.filter(role=User.Role.ADMIN, is_active=True, deleted=False).exclude(pk=user.pk)
        return not others.exists()
