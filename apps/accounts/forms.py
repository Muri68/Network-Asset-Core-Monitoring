from django import forms
from django.contrib.auth import password_validation

from apps.core.forms import StyledFormMixin

from . import roles, services
from .models import User


class LoginForm(StyledFormMixin, forms.Form):
    username = forms.CharField(label="Username or email", max_length=254)
    password = forms.CharField(widget=forms.PasswordInput, strip=False)


class TokenForm(StyledFormMixin, forms.Form):
    token = forms.CharField(label="Code", max_length=16)

    def clean_token(self):
        return self.cleaned_data["token"].replace(" ", "").replace("-", "").strip()


class NewPasswordForm(StyledFormMixin, forms.Form):
    """Choose a password (temporary-password replacement at login)."""

    new_password1 = forms.CharField(label="New password", widget=forms.PasswordInput, strip=False)
    new_password2 = forms.CharField(label="Confirm new password", widget=forms.PasswordInput, strip=False)

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("new_password1"), data.get("new_password2")
        if p1 and p2:
            if p1 != p2:
                self.add_error("new_password2", "The two passwords don't match.")
            elif self.user.check_password(p1):
                self.add_error("new_password1", "Choose a password different from the current one.")
            else:
                try:
                    password_validation.validate_password(p1, self.user)
                except forms.ValidationError as e:
                    self.add_error("new_password1", e)
        return data

    def save(self):
        self.user.set_password(self.cleaned_data["new_password1"])
        self.user.must_change_password = False
        self.user.save(update_fields=["password", "must_change_password"])
        return self.user


class ChangePasswordForm(NewPasswordForm):
    """Profile page: same as NewPasswordForm but needs the current password."""

    current_password = forms.CharField(label="Current password", widget=forms.PasswordInput, strip=False)
    field_order = ["current_password", "new_password1", "new_password2"]

    def clean_current_password(self):
        value = self.cleaned_data["current_password"]
        if not self.user.check_password(value):
            raise forms.ValidationError("Your current password is incorrect.")
        return value


class RevealCodeForm(StyledFormMixin, forms.Form):
    """Set or change the vault reveal code (PLAN.md §7.3: 6–32 characters, never the login password)."""

    code1 = forms.CharField(label="Reveal code", min_length=6, max_length=32, widget=forms.PasswordInput, strip=False)
    code2 = forms.CharField(label="Confirm reveal code", widget=forms.PasswordInput, strip=False)

    def __init__(self, user, *args, require_password=True, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        if require_password:
            self.fields["current_password"] = forms.CharField(
                label="Current login password", widget=forms.PasswordInput(attrs={"class": "input"}), strip=False
            )
            self.order_fields(["current_password", "code1", "code2"])

    def clean_current_password(self):
        value = self.cleaned_data["current_password"]
        if not self.user.check_password(value):
            raise forms.ValidationError("Your current password is incorrect.")
        return value

    def clean(self):
        data = super().clean()
        c1, c2 = data.get("code1"), data.get("code2")
        if c1 and c2:
            if c1 != c2:
                self.add_error("code2", "The two codes don't match.")
            elif self.user.check_password(c1):
                self.add_error("code1", "Your reveal code must be different from your login password.")
        return data

    def save(self):
        services.set_reveal_code(self.user, self.cleaned_data["code1"])


class ConfirmPasswordForm(StyledFormMixin, forms.Form):
    password = forms.CharField(label="Current password", widget=forms.PasswordInput, strip=False)

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_password(self):
        value = self.cleaned_data["password"]
        if not self.user.check_password(value):
            raise forms.ValidationError("Your current password is incorrect.")
        return value


ROLE_CHOICES = [(r, r) for r in roles.ROLES]


class UserForm(StyledFormMixin, forms.ModelForm):
    """Admin: create or edit a user. The role is stored as a Group."""

    role = forms.ChoiceField(choices=ROLE_CHOICES, initial=roles.ENGINEER)

    class Meta:
        model = User
        fields = ["full_name", "username", "email", "is_active"]
        labels = {"full_name": "Full name", "is_active": "Active (can sign in)"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["full_name"].required = True
        self.fields["email"].required = False
        if self.instance.pk:
            self.fields["role"].initial = self.instance.role
        else:
            del self.fields["is_active"]

    def clean_username(self):
        username = self.cleaned_data["username"].strip().lower()
        qs = User.objects.filter(username__iexact=username).exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("That username is already taken.")
        return username

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if email and User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Another user already has this email address.")
        return email or None

    def clean(self):
        data = super().clean()
        if self.instance.pk and "role" in data:
            try:
                services.guard_last_admin(self.instance, data["role"], data.get("is_active", True))
            except services.LastAdminError as e:
                raise forms.ValidationError(str(e)) from e
        return data
