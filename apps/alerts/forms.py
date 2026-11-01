import re

from django import forms

from apps.core.forms import StyledFormMixin

from . import telegram
from .models import AlertSettings

TOKEN_FORMAT = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")


class AlertSettingsForm(StyledFormMixin, forms.ModelForm):
    bot_token = forms.CharField(
        required=False, strip=True, label="Bot token",
        widget=forms.PasswordInput(attrs={"class": "input mono", "autocomplete": "off",
                                          "placeholder": "1234567890:AAH…"}),
        help_text="From @BotFather. Leave empty to keep the current token.")

    class Meta:
        model = AlertSettings
        fields = ["bot_token", "enabled", "reminder_minutes", "ssl_warnings"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.configured:
            self.fields["bot_token"].help_text = "From @BotFather (see the steps above)."

    def clean_bot_token(self):
        """A new token is checked with Telegram straight away; `bot_username` is filled in from the answer."""
        token = self.cleaned_data["bot_token"]
        self.bot_username = None
        if not token:
            if not self.instance.configured:
                raise forms.ValidationError("Enter the bot token you got from @BotFather.")
            return ""
        if not TOKEN_FORMAT.match(token):
            raise forms.ValidationError("That doesn't look like a bot token (it should be like 1234567890:AAH…).")
        try:
            me = telegram.get_me(token)
        except telegram.TelegramError as e:
            raise forms.ValidationError(f"Couldn't check the token with Telegram: {e}") from None
        self.bot_username = me.get("username", "")
        return token
