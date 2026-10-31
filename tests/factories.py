import factory
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts import roles, services
from apps.accounts.models import User

PASSWORD = "correct-horse-battery-staple"


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        skip_postgeneration_save = True

    username = factory.Sequence(lambda n: f"user{n}")
    full_name = factory.Faker("name")
    email = factory.LazyAttribute(lambda o: f"{o.username}@example.test")
    password = factory.PostGenerationMethodCall("set_password", PASSWORD)

    @factory.post_generation
    def role(self, create, extracted, **kwargs):
        self.save()
        if extracted:
            self.set_role(extracted)


def make_user(role=roles.VIEWER, with_2fa=True, with_reveal_code=True, **kwargs):
    """A ready-to-use user: role assigned, 2FA confirmed and (for vault roles) a reveal code set."""
    user = UserFactory(role=role, **kwargs)
    if with_2fa:
        TOTPDevice.objects.create(user=user, name="Authenticator app", confirmed=True)
    if with_reveal_code and roles.can(user, "vault"):
        services.set_reveal_code(user, "reveal-123")
    return user
