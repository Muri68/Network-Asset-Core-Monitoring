from django import forms

from apps.core.forms import StyledFormMixin
from apps.inventory.models import Device

from .models import MaintenanceWindow, Monitor


class NameChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return str(obj)


class MonitorForm(StyledFormMixin, forms.ModelForm):
    device = NameChoice(queryset=Device.objects.exclude(status=Device.Status.RETIRED).only("name"),
                        required=False, empty_label="— None —")
    parent = NameChoice(queryset=Monitor.objects.none(), required=False, empty_label="— Automatic —",
                        label="Depends on")

    class Meta:
        model = Monitor
        fields = ["name", "type", "target", "port", "interval_s", "timeout_s", "retries", "http_method",
                  "expected_status", "keyword", "follow_redirects", "ignore_tls", "dns_record_type", "dns_expected",
                  "ssl_warn_days", "device", "parent", "enabled"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        parents = Monitor.objects.only("name", "target")
        if self.instance.pk:
            parents = parents.exclude(pk=self.instance.pk)
        self.fields["parent"].queryset = parents
        self.fields["parent"].label_from_instance = lambda m: f"{m.name} · {m.target}"
        for name in ("target", "expected_status", "dns_expected"):
            self.fields[name].widget.attrs["class"] = "input mono"
        # Type-specific settings: left out of the POST when hidden, so fall back to the model default.
        for name in ("http_method", "expected_status", "dns_record_type"):
            self.fields[name].required = False

    def clean(self):
        data = super().clean()
        for name in ("http_method", "expected_status", "dns_record_type"):
            if not data.get(name):
                data[name] = Monitor._meta.get_field(name).default
        if data.get("type") == Monitor.Type.SSL and not data.get("port"):
            data["port"] = 443
            self.instance.port = 443
        return data


INTERVAL_CHOICES = [(20, "20 seconds"), (30, "30 seconds"), (60, "1 minute"), (120, "2 minutes"),
                    (300, "5 minutes"), (600, "10 minutes")]


class MaintenanceForm(StyledFormMixin, forms.ModelForm):
    devices = forms.ModelMultipleChoiceField(
        queryset=Device.objects.exclude(status=Device.Status.RETIRED).only("name"), required=False,
        widget=forms.SelectMultiple(attrs={"size": 8}))
    monitors = forms.ModelMultipleChoiceField(queryset=Monitor.objects.only("name"), required=False,
                                              widget=forms.SelectMultiple(attrs={"size": 8}))

    class Meta:
        model = MaintenanceWindow
        fields = ["name", "starts_at", "ends_at", "recurrence", "devices", "monitors", "include_children", "reason"]
        widgets = {
            "starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "ends_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        }

    def clean(self):
        data = super().clean()
        if not data.get("devices") and not data.get("monitors"):
            raise forms.ValidationError("Choose at least one device or monitor to mute.")
        return data
