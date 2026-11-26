"""Bootstrap/reset a user's authenticator app from the server console.

    py manage.py mfa_enroll <username>          # creates a CONFIRMED device and prints the secret once
"""
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.mfa import encrypt, new_secret, provisioning_uri
from apps.accounts.models import TOTPDevice, User


class Command(BaseCommand):
    help = "Create (or replace) a confirmed TOTP device for a user and print the secret."

    def add_arguments(self, parser):
        parser.add_argument("username")

    def handle(self, *args, **opts):
        try:
            user = User.objects.get(username=opts["username"])
        except User.DoesNotExist:
            raise CommandError("User not found.")
        secret = new_secret()
        TOTPDevice.objects.update_or_create(
            user=user, defaults={"secret_enc": encrypt(secret), "confirmed": True, "last_step": 0})
        self.stdout.write(self.style.SUCCESS("MFA enabled. Add this to the authenticator app NOW (shown once):"))
        self.stdout.write(f"  secret : {secret}")
        self.stdout.write(f"  uri    : {provisioning_uri(user, secret)}")
