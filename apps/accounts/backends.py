from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class UsernameOrEmailBackend(ModelBackend):
    """Lets people sign in with either their username or their email address."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None or password is None:
            return None
        User = get_user_model()
        lookup = {"email__iexact": username} if "@" in username else {"username__iexact": username}
        try:
            user = User.objects.get(**lookup)
        except User.DoesNotExist:
            # Run the hasher anyway so response time doesn't reveal whether the user exists.
            User().set_password(password)
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
