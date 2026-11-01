from django import forms

from apps.core.forms import StyledFormMixin
from apps.inventory.models import Device

from .models import Credential, CredentialFolder


def _secret_widget():
    return forms.PasswordInput(attrs={"class": "input mono pr-20", "autocomplete": "new-password"},
                               render_value=False)


class CredentialForm(StyledFormMixin, forms.ModelForm):
    """Add / edit. The password is only set when adding; changing it later has its own form (keeps history)."""

    password = forms.CharField(widget=_secret_widget(), strip=False, max_length=500)
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False, max_length=2000,
                            help_text="Encrypted too, and only shown together with the password.")
    clear_notes = forms.BooleanField(required=False, label="Remove the current notes")
    device = forms.ModelChoiceField(queryset=Device.objects.exclude(status=Device.Status.RETIRED).only("name"),
                                    required=False, empty_label="— None —")

    class Meta:
        model = Credential
        fields = ["name", "folder", "protocol", "username", "password", "host", "device", "notes", "clear_notes"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("username", "host"):
            self.fields[name].widget.attrs.update({"class": "input mono", "autocomplete": "off"})
        if self.instance.pk:
            # Neither the password nor the notes are ever put into the page without a reveal code.
            del self.fields["password"]
            self.fields["notes"].help_text = "Leave empty to keep the current notes (they stay hidden here)."
        else:
            del self.fields["clear_notes"]

    def save(self, by=None):
        credential = super().save(commit=False)
        if "password" in self.fields:
            credential.set_secret(self.cleaned_data["password"], by=by)
        if self.cleaned_data["notes"] or self.cleaned_data.get("clear_notes") or not credential.pk:
            credential.set_notes(self.cleaned_data["notes"])
        credential.save()
        return credential


class ChangePasswordForm(StyledFormMixin, forms.Form):
    password = forms.CharField(label="New password", widget=_secret_widget(), strip=False, max_length=500)


class FolderForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = CredentialFolder
        fields = ["name", "description"]


class RevealLogFilter(forms.Form):
    """Filters on the access log; `user` only for Admins (vault_logs_all)."""

    action = forms.ChoiceField(required=False)
    user = forms.ModelChoiceField(queryset=None, required=False, empty_label="Everyone")
    days = forms.TypedChoiceField(coerce=int, required=False, empty_value=30,
                                  choices=[(1, "Last 24 hours"), (7, "Last 7 days"), (30, "Last 30 days"),
                                           (365, "Last year")])

    def __init__(self, *args, users=None, **kwargs):
        from .models import RevealLog

        super().__init__(*args, **kwargs)
        self.fields["action"].choices = [("", "All events"), *RevealLog.Action.choices]
        if users is None:
            del self.fields["user"]
        else:
            self.fields["user"].queryset = users
        for field in self.fields.values():
            field.widget.attrs["class"] = "input"
