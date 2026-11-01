from django import forms

from apps.core.forms import StyledFormMixin
from apps.dcim.models import Port
from apps.inventory.models import Device

from .models import ISP, Circuit, IPAddress, Prefix, parse_network


class ISPForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = ISP
        fields = ["name", "account_number", "support_phone", "support_email", "account_manager", "portal_url", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}


class PortLabel(forms.ModelChoiceField):
    def label_from_instance(self, port):
        return f"{port.device} · {port.name}"


class CircuitForm(StyledFormMixin, forms.ModelForm):
    termination_port = PortLabel(queryset=Port.objects.select_related("device"), required=False,
                                 label="Lands on port", empty_label="— Not recorded —")

    class Meta:
        model = Circuit
        fields = ["isp", "circuit_id", "circuit_type", "status", "bandwidth", "install_date", "contract_start",
                  "contract_end", "monthly_cost", "currency", "termination_port", "notes"]
        widgets = {
            "install_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "contract_start": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "contract_end": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_currency(self):
        return self.cleaned_data["currency"].strip().upper()


class PrefixForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Prefix
        fields = ["cidr", "kind", "status", "gateway", "isp", "circuit", "vlan", "location", "description"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cidr"].widget.attrs.update({"class": "input mono", "placeholder": "129.205.100.128/28"})
        self.fields["gateway"].widget.attrs.update({"class": "input mono"})
        self.fields["kind"].required = False
        if not self.instance.pk:
            # New prefix: start on "Automatic" so public/private is detected from the address,
            # instead of silently using the model default ("private").
            self.initial.pop("kind", None)
            self.fields["kind"].initial = None

    def clean(self):
        data = super().clean()
        cidr = data.get("cidr")
        if cidr and not data.get("kind"):
            try:
                net, _ = parse_network(cidr)
                data["kind"] = Prefix.Kind.PRIVATE if net.is_private else Prefix.Kind.PUBLIC
                self.instance.kind = data["kind"]
            except forms.ValidationError:
                pass
        return data


class DeviceLabel(forms.ModelChoiceField):
    def label_from_instance(self, device):
        return device.name


class IPAddressForm(StyledFormMixin, forms.ModelForm):
    device = DeviceLabel(queryset=Device.objects.exclude(status=Device.Status.RETIRED).only("name"),
                         required=False, empty_label="— None —")
    port = forms.ModelChoiceField(queryset=Port.objects.none(), required=False, empty_label="— None —")

    class Meta:
        model = IPAddress
        fields = ["address", "status", "device", "port", "service", "dns_name", "nat_to", "nat_ports",
                  "description"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["address"].widget.attrs.update({"class": "input mono", "placeholder": "129.205.100.131"})
        self.fields["dns_name"].widget.attrs["class"] = "input mono"
        device_id = self.data.get("device") or self.initial.get("device") or self.instance.device_id
        if str(device_id or "").isdigit():
            self.fields["port"].queryset = Port.objects.filter(device_id=device_id)
        self.fields["port"].label_from_instance = lambda p: p.name
        self.fields["nat_to"].widget.attrs.update({"class": "input mono", "placeholder": "10.10.10.11"})
