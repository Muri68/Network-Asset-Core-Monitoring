from django import forms
from django.db.models import Q

from apps.core.forms import StyledFormMixin
from apps.inventory.models import Device

from .models import VLAN, Connection, ExternalEndpoint, Port
from .patterns import PatternError, expand_lines


class VLANForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = VLAN
        fields = ["vid", "name", "location", "description"]


def vlans_for(device=None):
    """Global VLANs plus those of the device's location."""
    qs = VLAN.objects.select_related("location")
    if device is not None:
        qs = qs.filter(Q(location__isnull=True) | Q(location=device.location_id))
    return qs


class VLANChoice(forms.ModelChoiceField):
    def label_from_instance(self, vlan):
        return str(vlan)


class VLANMultiChoice(forms.ModelMultipleChoiceField):
    def label_from_instance(self, vlan):
        return str(vlan)


class BulkPortsForm(StyledFormMixin, forms.Form):
    patterns = forms.CharField(
        label="Port names", widget=forms.Textarea(attrs={"rows": 3, "class": "input mono"}),
        help_text="One pattern per line. [1-48] makes 48 ports; [1,3,5-8] picks numbers.",
    )
    type = forms.ChoiceField(choices=Port.Type.choices[:5] + [("other", "Other")], initial=Port.Type.COPPER)
    speed = forms.ChoiceField(choices=Port.SPEEDS, required=False)
    mode = forms.ChoiceField(choices=Port.Mode.choices, required=False)
    pair_rear = forms.BooleanField(required=False, label="Patch panel: create a paired rear port for each")

    def clean_patterns(self):
        try:
            names = expand_lines(self.cleaned_data["patterns"])
        except PatternError as e:
            raise forms.ValidationError(str(e)) from e
        if not names:
            raise forms.ValidationError("Enter at least one port name.")
        self.names = names
        return self.cleaned_data["patterns"]


class PortForm(StyledFormMixin, forms.ModelForm):
    untagged_vlan = VLANChoice(queryset=VLAN.objects.none(), required=False, label="Untagged / access VLAN")
    tagged_vlans = VLANMultiChoice(queryset=VLAN.objects.none(), required=False,
                                   widget=forms.CheckboxSelectMultiple, label="Tagged VLANs (trunk)")

    class Meta:
        model = Port
        fields = ["name", "label", "type", "speed", "mode", "untagged_vlan", "tagged_vlans", "remark", "enabled"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["untagged_vlan"].queryset = vlans_for(self.instance.device)
        self.fields["tagged_vlans"].queryset = vlans_for(self.instance.device)
        self.fields["tagged_vlans"].widget.attrs = {}
        self.fields["name"].widget.attrs["class"] = "input mono"

    def save(self, commit=True):
        from .services import set_port_vlans

        port = super().save(commit=False)
        port.save()
        set_port_vlans(port, self.cleaned_data["untagged_vlan"], self.cleaned_data["tagged_vlans"])
        return port


class PortChoice(forms.ModelChoiceField):
    def label_from_instance(self, port):
        return port.name


class ConnectionForm(StyledFormMixin, forms.ModelForm):
    """
    One cable: side A port ↔ side B port or external endpoint. Optionally sets the port mode and VLANs
    on both ends at the same time, since a trunk is a trunk on both switches.
    """

    a_device = forms.ModelChoiceField(queryset=Device.objects.all(), label="Device")
    a_port = PortChoice(queryset=Port.objects.all(), label="Port")
    b_kind = forms.ChoiceField(choices=[("device", "Device port"), ("external", "External")], initial="device",
                               widget=forms.HiddenInput)
    b_device = forms.ModelChoiceField(queryset=Device.objects.all(), label="Device", required=False)
    b_port = PortChoice(queryset=Port.objects.all(), label="Port", required=False)
    b_external_name = forms.CharField(label="External endpoint", max_length=120, required=False,
                                      help_text="e.g. “FibreLink ONT — circuit FL-22817” or “Wall jack B-12”")
    apply_mode = forms.ChoiceField(label="Set port mode on both ends", required=False,
                                   choices=[("", "Leave ports as they are")] + Port.Mode.choices[1:])
    untagged_vlan = VLANChoice(queryset=VLAN.objects.all(), required=False, label="Untagged / access VLAN")
    tagged_vlans = VLANMultiChoice(queryset=VLAN.objects.all(), required=False,
                                   widget=forms.CheckboxSelectMultiple, label="Tagged VLANs")

    class Meta:
        model = Connection
        fields = ["function", "cable_label", "cable_type", "colour", "length_m", "remark"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tagged_vlans"].widget.attrs = {}
        self.fields["untagged_vlan"].queryset = vlans_for()
        self.fields["tagged_vlans"].queryset = vlans_for()
        self.fields["cable_label"].widget.attrs["class"] = "input mono"
        c = self.instance
        if c.pk:
            self.fields["a_device"].initial = c.a_port.device_id
            self.fields["a_port"].initial = c.a_port_id
            if c.b_port_id:
                self.fields["b_device"].initial = c.b_port.device_id
                self.fields["b_port"].initial = c.b_port_id
            else:
                self.fields["b_kind"].initial = "external"
                self.fields["b_external_name"].initial = c.b_external.name
            self.fields["untagged_vlan"].initial = c.a_port.untagged_vlan_id
            self.fields["tagged_vlans"].initial = list(c.a_port.tagged_vlans.values_list("pk", flat=True))

    def clean(self):
        data = super().clean()
        a_device, a_port = data.get("a_device"), data.get("a_port")
        if a_device and a_port and a_port.device_id != a_device.pk:
            self.add_error("a_port", "Choose a port on the selected device.")
        if data.get("b_kind") == "external":
            if not (data.get("b_external_name") or "").strip():
                self.add_error("b_external_name", "Name the external endpoint.")
            data["b_port"] = None
        else:
            b_device, b_port = data.get("b_device"), data.get("b_port")
            if not b_device:
                self.add_error("b_device", "Choose the device at the other end.")
            elif not b_port:
                self.add_error("b_port", "Choose the port at the other end.")
            elif b_port.device_id != b_device.pk:
                self.add_error("b_port", "Choose a port on the selected device.")
            elif a_port and b_port.pk == a_port.pk:
                self.add_error("b_port", "A port can't be connected to itself.")
        # The model's "already connected" check (run by Django right after this) needs the ports set.
        if a_port:
            self.instance.a_port = a_port
        self.instance.b_port = data.get("b_port")
        return data

    def save(self, commit=True):
        from .services import set_port_vlans

        connection = super().save(commit=False)
        connection.a_port = self.cleaned_data["a_port"]
        if self.cleaned_data["b_kind"] == "external":
            name = self.cleaned_data["b_external_name"].strip()
            external = ExternalEndpoint.objects.filter(name__iexact=name).first()
            if external is None:
                external = ExternalEndpoint.objects.create(name=name, location=connection.a_port.device.location)
            connection.b_port, connection.b_external = None, external
        else:
            connection.b_port, connection.b_external = self.cleaned_data["b_port"], None
        connection.save()

        mode = self.cleaned_data.get("apply_mode")
        if mode:
            for port in filter(None, (connection.a_port, connection.b_port)):
                port.mode = mode
                set_port_vlans(port, self.cleaned_data["untagged_vlan"], self.cleaned_data["tagged_vlans"])
        return connection
