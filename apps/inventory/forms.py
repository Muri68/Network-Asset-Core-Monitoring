from django import forms

from apps.core.forms import StyledFormMixin

from .models import Device, DeviceModel, DeviceType, Location, Manufacturer, Rack


class LocationForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Location
        fields = ["name", "address", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}


class RackForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Rack
        fields = ["location", "name", "height_u", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}

    def clean_height_u(self):
        height = self.cleaned_data["height_u"]
        if self.instance.pk:
            tallest = max((d.top_u for d in self.instance.occupants()), default=0)
            if height < tallest:
                raise forms.ValidationError(f"A device sits up to U{tallest}; the rack can't be shorter than that.")
        return height


class RackChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, rack):
        return f"{rack.location} · {rack.name} ({rack.height_u}U)"


class DeviceForm(StyledFormMixin, forms.ModelForm):
    """Manufacturer and model are typed in; new ones are created on save."""

    manufacturer_name = forms.CharField(label="Manufacturer", max_length=100)
    model_name = forms.CharField(label="Model", max_length=100)
    rack = RackChoiceField(
        queryset=Rack.objects.select_related("location"), required=False, empty_label="— Not racked —"
    )

    class Meta:
        model = Device
        fields = [
            "name", "device_type", "status", "serial", "asset_tag", "firmware", "mgmt_ip", "mac",
            "location", "rack", "position_u", "height_u", "face", "parent",
            "purchase_date", "warranty_expiry", "owner", "notes",
        ]
        widgets = {
            "purchase_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "warranty_expiry": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        model = getattr(self.instance, "device_model", None) if self.instance.device_model_id else None
        if model is not None:
            self.fields["manufacturer_name"].initial = model.manufacturer.name
            self.fields["model_name"].initial = model.name
        parents = Device.objects.exclude(status=Device.Status.RETIRED).only("name")
        if self.instance.pk:
            parents = parents.exclude(pk=self.instance.pk)
        self.fields["parent"].queryset = parents
        self.fields["parent"].empty_label = "— None —"
        self.fields["location"].empty_label = None if Location.objects.count() == 1 else "— Choose —"
        for name in ("serial", "firmware", "mac", "owner", "mgmt_ip", "asset_tag"):
            self.fields[name].widget.attrs["class"] = "input mono" if name in ("serial", "mac", "mgmt_ip",
                                                                               "asset_tag") else "input"
        self.fields["mgmt_ip"].widget.attrs["placeholder"] = "10.10.0.5"
        self.fields["mac"].widget.attrs["placeholder"] = "aa:bb:cc:dd:ee:ff"

    def clean_name(self):
        return self.cleaned_data["name"].strip()

    def clean(self):
        data = super().clean()
        if data.get("rack"):
            # A racked device is wherever its rack is.
            data["location"] = data["rack"].location
            self.errors.pop("location", None)
        else:
            data["position_u"] = None
        return data

    def save(self, commit=True):
        manufacturer = Manufacturer.objects.filter(name__iexact=self.cleaned_data["manufacturer_name"].strip()).first()
        if manufacturer is None:
            manufacturer = Manufacturer.objects.create(name=self.cleaned_data["manufacturer_name"].strip())
        model_name = self.cleaned_data["model_name"].strip()
        device_model = DeviceModel.objects.filter(manufacturer=manufacturer, name__iexact=model_name).first()
        if device_model is None:
            device_model = DeviceModel.objects.create(
                manufacturer=manufacturer, name=model_name,
                device_type=self.cleaned_data["device_type"], height_u=self.cleaned_data["height_u"],
            )
        self.instance.device_model = device_model
        return super().save(commit=commit)


def parse_attributes(post):
    """Extra attributes from repeated attr_key / attr_value inputs → (dict, errors)."""
    pairs, errors = {}, []
    for key, value in zip(post.getlist("attr_key"), post.getlist("attr_value"), strict=False):
        key, value = key.strip(), value.strip()
        if not key and not value:
            continue
        if not key:
            errors.append(f"The value “{value}” needs a name.")
        elif key.lower() in {k.lower() for k in pairs}:
            errors.append(f"“{key}” is listed twice.")
        else:
            pairs[key[:60]] = value[:255]
    return pairs, errors


DEVICE_TYPE_CHOICES = DeviceType.choices


class DeviceModelForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = DeviceModel
        fields = ["manufacturer", "name", "device_type", "height_u", "port_template"]
        widgets = {"port_template": forms.Textarea(attrs={
            "rows": 6, "class": "input mono", "placeholder": "GigabitEthernet1/0/[1-48]\nTenGigabitEthernet1/1/[1-4]",
        })}
        help_texts = {"port_template": "One pattern per line. New devices of this model get these ports automatically; "
                                       "existing devices can add them from their Ports tab."}

    def clean_port_template(self):
        from apps.dcim.patterns import PatternError, expand_lines

        text = self.cleaned_data["port_template"]
        try:
            expand_lines(text)
        except PatternError as e:
            raise forms.ValidationError(str(e)) from e
        return text
