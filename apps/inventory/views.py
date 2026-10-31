from collections import Counter

from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, ProtectedError, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.decorators import capability_required
from apps.audit.models import AuditLog

from .forms import DeviceForm, LocationForm, RackForm, parse_attributes
from .models import DEVICE_ICONS, Device, DeviceAttribute, DeviceModel, DeviceType, Location, Manufacturer, Rack

UNIT_PX = 22
PAGE_SIZE = 50


# --------------------------------------------------------------------------
# Rack layout (shared by racks list, rack page and device form)
# --------------------------------------------------------------------------


def rack_layout(rack, face="front", highlight=None):
    """Data for templates/inventory/partials/rack_elevation.html."""
    items = []
    for d in rack.occupants(face=face).select_related("device_model"):
        d.top_px = (rack.height_u - d.top_u) * UNIT_PX + 1
        d.height_px = max(d.height_u, 1) * UNIT_PX - 3
        d.highlight = highlight is not None and d.pk == highlight.pk
        items.append(d)
    used_units = {u for d in items for u in range(d.position_u, d.top_u + 1)}
    return {
        "obj": rack, "face": face, "items": sorted(items, key=lambda d: -d.position_u),
        "units": list(range(rack.height_u, 0, -1)), "free_units": set(range(1, rack.height_u + 1)) - used_units,
        "px": rack.height_u * UNIT_PX, "used": len(used_units),
        "pct": round(len(used_units) * 100 / rack.height_u) if rack.height_u else 0,
    }


# --------------------------------------------------------------------------
# Devices
# --------------------------------------------------------------------------


def devices(request):
    dtype = request.GET.get("type", "")
    status = request.GET.get("status", "")
    q = request.GET.get("q", "").strip()
    location = request.GET.get("location", "")

    qs = Device.objects.select_related("device_model__manufacturer", "rack", "location")
    if status == "all":
        pass
    elif status in Device.Status.values:
        qs = qs.filter(status=status)
    else:
        qs = qs.exclude(status=Device.Status.RETIRED)
    type_counts = Counter(qs.values_list("device_type", flat=True))
    if dtype in DeviceType.values:
        qs = qs.filter(device_type=dtype)
    if location.isdigit():
        qs = qs.filter(location_id=location)
    if q:
        qs = qs.filter(
            Q(name__icontains=q) | Q(mgmt_ip__icontains=q) | Q(serial__icontains=q) | Q(asset_tag__icontains=q)
            | Q(device_model__name__icontains=q) | Q(device_model__manufacturer__name__icontains=q)
            | Q(mac__icontains=q)
        )
    page = Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return render(request, "inventory/devices.html", {
        "page": page, "devices": page.object_list, "q": q, "dtype": dtype, "status": status, "location": location,
        "types": [(k, label, DEVICE_ICONS[k], type_counts.get(k, 0)) for k, label in DeviceType.choices],
        "shown_total": sum(type_counts.values()), "total": Device.objects.count(),
        "retired_count": Device.objects.filter(status=Device.Status.RETIRED).count(),
        "locations": Location.objects.all(), "query": query.urlencode(), "statuses": Device.Status.choices,
        "has_locations": Location.objects.exists(), "active": "devices",
    })


def device_detail(request, pk):
    device = get_object_or_404(
        Device.objects.select_related("device_model__manufacturer", "rack", "location", "parent"), pk=pk
    )
    ct_device = ContentType.objects.get_for_model(Device)
    ct_attr = ContentType.objects.get_for_model(DeviceAttribute)
    history = AuditLog.objects.filter(
        Q(object_type=ct_device, object_id=str(device.pk))
        | Q(object_type=ct_attr, object_repr__startswith=f"{device.name} · ")
    ).select_related("object_type")[:50]
    return render(request, "inventory/device_detail.html", {
        "d": device,
        "attributes": device.attributes.all(),
        "children": device.children.exclude(status=Device.Status.RETIRED).select_related("device_model"),
        "rack": rack_layout(device.rack, device.face, highlight=device) if device.rack_id else None,
        "history": history,
        "tab": request.GET.get("tab", "overview"),
        "active": "devices",
    })


def _save_device(request, form, attributes):
    with transaction.atomic():
        device = form.save()
        existing = {a.key: a for a in device.attributes.all()}
        for key, attr in existing.items():
            if key not in attributes:
                attr.delete()
        for key, value in attributes.items():
            attr = existing.get(key)
            if attr is None:
                DeviceAttribute.objects.create(device=device, key=key, value=value)
            elif attr.value != value:
                attr.value = value
                attr.save()
    return device


def _device_form_page(request, form, device=None, attributes=None, attr_errors=()):
    if attributes is None:
        attributes = {a.key: a.value for a in device.attributes.all()} if device else {}
    racks = Rack.objects.select_related("location")
    return render(request, "inventory/device_form.html", {
        "form": form, "device": device, "attributes": [list(kv) for kv in attributes.items()],
        "attr_errors": attr_errors,
        "racks": racks, "has_locations": Location.objects.exists(), "type_icons": DEVICE_ICONS,
        "manufacturers": Manufacturer.objects.values_list("name", flat=True),
        "models": DeviceModel.objects.values_list("name", flat=True).distinct(),
        "active": "devices",
    }, status=400 if form.is_bound else 200)


@capability_required("edit")
def device_create(request):
    if request.method == "POST":
        form = DeviceForm(request.POST)
        attributes, attr_errors = parse_attributes(request.POST)
        if form.is_valid() and not attr_errors:
            device = _save_device(request, form, attributes)
            messages.success(request, f"{device.name} added.")
            return redirect(device)
        return _device_form_page(request, form, attributes=attributes, attr_errors=attr_errors)
    initial = {}
    for key in ("rack", "position_u", "face"):
        if request.GET.get(key):
            initial[key] = request.GET[key]
    if "rack" in initial:
        rack = Rack.objects.filter(pk=initial["rack"]).first()
        if rack:
            initial["location"] = rack.location_id
    return _device_form_page(request, DeviceForm(initial=initial))


@capability_required("edit")
def device_edit(request, pk):
    device = get_object_or_404(Device, pk=pk)
    if request.method == "POST":
        form = DeviceForm(request.POST, instance=device)
        attributes, attr_errors = parse_attributes(request.POST)
        if form.is_valid() and not attr_errors:
            _save_device(request, form, attributes)
            messages.success(request, f"{device.name} saved.")
            return redirect(device)
        return _device_form_page(request, form, device, attributes, attr_errors)
    return _device_form_page(request, DeviceForm(instance=device), device)


@capability_required("edit")
@require_POST
def device_retire(request, pk):
    device = get_object_or_404(Device, pk=pk)
    device.retire()
    messages.success(request, f"{device.name} retired. Its rack space is free; the record and history are kept.")
    return redirect(device)


@capability_required("delete_hard")
@require_POST
def device_delete(request, pk):
    device = get_object_or_404(Device, pk=pk)
    if request.POST.get("confirm_name", "").strip().lower() != device.name.lower():
        messages.error(request, "Type the device name exactly to confirm deletion.")
        return redirect(device)
    name = device.name
    device.delete()
    messages.success(request, f"{name} permanently deleted.")
    return redirect("inventory:devices")


# --------------------------------------------------------------------------
# Racks & locations
# --------------------------------------------------------------------------


def racks(request):
    locations = Location.objects.annotate(device_count=Count("devices")).prefetch_related("racks")
    sections = [(loc, [rack_layout(r) for r in loc.racks.all()]) for loc in locations]
    return render(request, "inventory/racks.html", {
        "sections": sections, "location_form": LocationForm(), "rack_form": RackForm(),
        "unracked": Device.objects.filter(rack__isnull=True).exclude(status=Device.Status.RETIRED).count(),
        "active": "racks",
    })


def rack_detail(request, pk):
    rack = get_object_or_404(Rack.objects.select_related("location"), pk=pk)
    devices_in = rack.devices.select_related("device_model__manufacturer").order_by("-position_u")
    return render(request, "inventory/rack_detail.html", {
        "rack": rack, "front": rack_layout(rack, "front"), "rear": rack_layout(rack, "rear"),
        "devices": devices_in, "siblings": Rack.objects.filter(location=rack.location),
        "active": "racks",
    })


def _simple_form(request, form, title, cancel_url, obj=None):
    return render(request, "inventory/simple_form.html", {
        "form": form, "title": title, "cancel_url": cancel_url, "obj": obj, "active": "racks",
    }, status=400 if form.is_bound else 200)


@capability_required("edit")
def location_create(request):
    form = LocationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        location = form.save()
        messages.success(request, f"Location {location.name} added.")
        return redirect("inventory:racks")
    return _simple_form(request, form, "Add location", "inventory:racks")


@capability_required("edit")
def location_edit(request, pk):
    location = get_object_or_404(Location, pk=pk)
    form = LocationForm(request.POST or None, instance=location)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Location {location.name} saved.")
        return redirect("inventory:racks")
    return _simple_form(request, form, f"Edit location · {location.name}", "inventory:racks", location)


@capability_required("edit")
@require_POST
def location_delete(request, pk):
    location = get_object_or_404(Location, pk=pk)
    try:
        location.delete()
        messages.success(request, f"Location {location.name} deleted.")
    except ProtectedError:
        messages.error(request, f"{location.name} still has racks or devices. Move or remove them first.")
    return redirect("inventory:racks")


@capability_required("edit")
def rack_create(request):
    initial = {"location": request.GET.get("location")} if request.GET.get("location") else None
    form = RackForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        rack = form.save()
        messages.success(request, f"{rack.name} added.")
        return redirect(rack)
    return _simple_form(request, form, "Add rack", "inventory:racks")


@capability_required("edit")
def rack_edit(request, pk):
    rack = get_object_or_404(Rack, pk=pk)
    form = RackForm(request.POST or None, instance=rack)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"{rack.name} saved.")
        return redirect(rack)
    return _simple_form(request, form, f"Edit rack · {rack.name}", "inventory:racks", rack)


@capability_required("edit")
@require_POST
def rack_delete(request, pk):
    rack = get_object_or_404(Rack, pk=pk)
    try:
        rack.delete()
        messages.success(request, f"{rack.name} deleted.")
        return redirect("inventory:racks")
    except ProtectedError:
        messages.error(request, f"{rack.name} still holds devices. Move or retire them first.")
        return redirect(rack)


@capability_required("edit")
def rack_check(request):
    """HTMX: does a device of this height fit at this U? Used live by the device form."""
    rack = Rack.objects.filter(pk=request.GET.get("rack") or 0).first()
    try:
        position = int(request.GET.get("position_u") or 0)
        height = max(int(request.GET.get("height_u") or 1), 1)
    except ValueError:
        position, height = 0, 1
    face = request.GET.get("face", "front")
    exclude = Device.objects.filter(pk=request.GET.get("device") or 0).first()
    result = {"rack": rack, "position": position, "height": height, "top": position + height - 1}
    if rack and position:
        if position + height - 1 > rack.height_u:
            result["problem"] = f"Doesn't fit: {rack.name} is {rack.height_u}U tall."
        else:
            clash = rack.conflicts(position, height, face, exclude=exclude)
            if clash:
                result["problem"] = "Overlaps " + ", ".join(f"{d.name} (U{d.position_u}–U{d.top_u})" for d in clash)
    return render(request, "inventory/partials/rack_check.html", result)
