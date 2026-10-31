import getpass

from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.accounts import roles, services
from apps.accounts.models import User


class Command(BaseCommand):
    help = "Create the first Admin account (first run only). 2FA is set up at their first sign-in."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--full-name", default="")
        parser.add_argument("--email", default="")
        parser.add_argument(
            "--password", help="Avoid on shared machines (ends up in shell history); prompted for if omitted."
        )
        parser.add_argument("--force", action="store_true", help="Create even if an Admin already exists.")

    def handle(self, *args, **opts):
        if services.active_admin_count() and not opts["force"]:
            raise CommandError("An active Admin already exists. Add more users from the Users page (or use --force).")
        username = opts["username"].strip().lower()
        if User.objects.filter(username__iexact=username).exists():
            raise CommandError(f"User '{username}' already exists.")

        password = opts["password"]
        if password is None:
            password = getpass.getpass("Password: ")
            if password != getpass.getpass("Password (again): "):
                raise CommandError("Passwords don't match.")
        try:
            password_validation.validate_password(password, User(username=username, full_name=opts["full_name"]))
        except ValidationError as e:
            raise CommandError(" ".join(e.messages)) from e

        user, _ = services.create_user(
            username=username, full_name=opts["full_name"], email=opts["email"], role=roles.ADMIN, password=password
        )
        self.stdout.write(self.style.SUCCESS(
            f"Admin '{user.username}' created. Sign in to set up two-factor authentication and your reveal code."
        ))
