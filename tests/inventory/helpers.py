from apps.inventory.models import Device, DeviceModel, Location, Manufacturer, Rack


def make_location(name="HQ — Server Room"):
    return Location.objects.get_or_create(name=name)[0]


def make_rack(name="Rack 1", location=None, height_u=42):
    location = location or make_location()
    return Rack.objects.get_or_create(location=location, name=name, defaults={"height_u": height_u})[0]


def make_model(manufacturer="Cisco", name="Catalyst 9300", device_type="switch", height_u=1):
    m = Manufacturer.objects.filter(name__iexact=manufacturer).first() or Manufacturer.objects.create(name=manufacturer)
    return DeviceModel.objects.get_or_create(manufacturer=m, name=name,
                                             defaults={"device_type": device_type, "height_u": height_u})[0]


_counter = {"n": 0}


def make_device(name=None, rack="default", position_u=10, height_u=1, face="front", save=True, **kwargs):
    """A valid racked device by default; pass rack=None for an unracked one."""
    _counter["n"] += 1
    if rack == "default":
        rack = make_rack()
    location = kwargs.pop("location", None) or (rack.location if rack else make_location())
    device = Device(
        name=name or f"DEV-{_counter['n']:03d}", device_model=kwargs.pop("device_model", None) or make_model(),
        device_type=kwargs.pop("device_type", "switch"), location=location, rack=rack,
        position_u=position_u if rack else None, height_u=height_u, face=face, **kwargs,
    )
    if save:
        device.full_clean()
        device.save()
    return device
