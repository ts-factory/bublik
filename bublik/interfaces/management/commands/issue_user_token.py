# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
Issue a personal access token from the command line, e.g. for air-gapped setups.
"""

from django.core.management.base import BaseCommand, CommandError
from rest_framework.exceptions import ValidationError

from bublik.core.user_token import EXPIRY_CHOICES, UserTokenService
from bublik.data.models import User


EXPIRY_OPTIONS = [str(days) if days else 'never' for days in EXPIRY_CHOICES]


class Command(BaseCommand):
    help = 'Issue a personal access token for a user and print it once'

    def add_arguments(self, parser):
        parser.add_argument('email', help='Email of the user the token acts as')
        parser.add_argument(
            '--name',
            required=True,
            help='Name for the token, e.g. the client it is configured in',
        )
        parser.add_argument(
            '--expires',
            default='30',
            choices=EXPIRY_OPTIONS,
            help='Lifetime in days, or "never" (default: 30)',
        )

    def handle(self, *args, **options):
        try:
            user = User.objects.get(email=options['email'])
        except User.DoesNotExist:
            msg = f'No user with email {options["email"]}'
            raise CommandError(msg) from None

        if not user.is_active:
            msg = f'{user.email} is deactivated; its tokens would not work'
            raise CommandError(msg)

        expires = options['expires']
        expires_in_days = None if expires == 'never' else int(expires)

        try:
            issued = UserTokenService.issue(
                user=user,
                name=options['name'],
                expires_in=expires_in_days,
            )
        except ValidationError as e:
            raise CommandError('; '.join(str(detail) for detail in e.detail)) from e

        token = issued.token
        expiry = token.expires_at.isoformat() if token.expires_at else 'never'
        self.stdout.write(f'Token "{token.name}" for {user.email}, expires: {expiry}')
        self.stdout.write(self.style.SUCCESS(issued.value))
        self.stdout.write('This value is not stored and will not be shown again.')
