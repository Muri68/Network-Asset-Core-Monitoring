from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.accounts.decorators import capability_required
from apps.inventory.models import Device, Rack

from . import services
from .forms import BulkPortsForm, ConnectionForm, PortForm, VLANForm
from .models import VLAN, Connection, ExternalEndpoint, Port, trace
from .patterns import PatternError, expand_lines

PAGE_SIZE = 50


# --------------------------------------------------------------------------
# VLANs
# --------------------------------------------------------------------------


def vlans(request):
    rows = VLAN.objects.select_related("location").annotate(
        access_count=Count("untagged_ports", distinct=True), trunk_count=Count("tagged_ports", distinct=True)
    )
    return render(request, "dcim/vlans.html", {"vlans": rows, "form": VLANForm(), "active": "vlans"})


def _vlan_form_page(request, form, title):
    return render(request, "inventory/simple_form.html", {
        "form": form, "title": title, "cancel_url": "dcim:vlans", "crumb": "Infrastructure / VLANs",
        "active": "vlans",
    }, status=400 if form.is_bound else 200)


@capability_required("edit")
def vlan_create(request):
    form = VLANForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        vlan = form.save()
        messages.success(request, f"VLAN {vlan} added.")
        return redirect("dcim:vlans")
    return _vlan_form_page(request, form, "Add VLAN")


@capability_required("edit")
def vlan_edit(request, pk):
    vlan = get_object_or_404(VLAN, pk=pk)
    form = VLANForm(request.POST or None, instance=vlan)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"VLAN {vlan} saved.")
        return redirect("dcim:vlans")
    return _vlan_form_page(request, form, f"Edit VLAN {vlan}")


@capability_required("edit")
@require_POST
def vlan_delete(request, pk):
    vlan = get_object_or_404(VLAN, pk=pk)
    in_use = vlan.untagged_ports.count() + vlan.tagged_ports.count()
    vlan.delete()
    note = f" It was removed from {in_use} port{'s' if in_use != 1 else ''}." if in_use else ""
    messages.success(request, f"VLAN {vlan} deleted.{note}")
    return redirect("dcim:vlans")


# --------------------------------------------------------------------------
# Connections
# --------------------------------------------------------------------------


def _connections_qs():
    return Connection.objects.select_related(
        "a_port__device", "b_port__device", "b_external", "a_port__untagged_vlan"
    ).prefetch_related("a_port__tagged_vlans")


def connections(request):
    q = request.GET.get("q", "").strip()
    device = request.GET.get("device", "")
    rack = request.GET.get("rack", "")
    qs = _connections_qs()
    if q:
        vlan_q = Q()
        if q.isdigit():
            vlan_q = Q(a_port__untagged_vlan__vid=int(q)) | Q(a_port__tagged_vlans__vid=int(q))
        qs = qs.filter(
            Q(a_port__device__name__icontains=q) | Q(b_port__device__name__icontains=q)
            | Q(b_external__name__icontains=q) | Q(function__icontains=q) | Q(cable_label__icontains=q)
            | Q(remark__icontains=q) | Q(a_port__name__icontains=q) | Q(b_port__name__icontains=q) | vlan_q
        ).distinct()
    if device.isdigit():
        qs = qs.filter(Q(a_port__device_id=device) | Q(b_port__device_id=device))
    if rack.isdigit():
        qs = qs.filter(Q(a_port__device__rack_id=rack) | Q(b_port__device__rack_id=rack))
    page = Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))
    for c in page.object_list:
        c.vlans = services.vlan_summary(c.a_port)
    query = request.GET.copy()
    query.pop("page", None)
    return render(request, "dcim/connections.html", {
        "page": page, "connections": page.object_list, "q": q, "device": device, "rack": rack,
        "query": query.urlencode(), "total": Connection.objects.count(),
        "devices": Device.objects.exclude(status=Device.Status.RETIRED).only("name"),
        "racks": Rack.objects.select_related("location"), "active": "connections",
    })


def _connection_form_page(request, form, connection=None):
    return render(request, "dcim/connection_form.html", {
        "form": form, "connection": connection,
        "devices": Device.objects.exclude(status=Device.Status.RETIRED).only("name"),
        "externals": ExternalEndpoint.objects.values_list("name", flat=True),
        "active": "connections",
    }, status=400 if form.is_bound else 200)


def _back_to(request, connection):
    device_id = request.POST.get("return_device") or request.GET.get("return_device")
    if device_id and device_id.isdigit():
        return f"{reverse('inventory:device_detail', args=[device_id])}?tab=ports"
    return reverse("dcim:connections")


@capability_required("edit")
def connection_create(request):
    if request.method == "POST":
        form = ConnectionForm(request.POST)
        if form.is_valid():
            connection = form.save()
            messages.success(request, f"Connected {connection.a_port} ↔ {connection.b_label}.")
            return redirect(_back_to(request, connection))
        return _connection_form_page(request, form)
    initial = {}
    port = Port.objects.filter(pk=request.GET.get("a_port") or 0).first()
    if port:
        initial = {"a_device": port.device_id, "a_port": port.pk}
    return _connection_form_page(request, ConnectionForm(initial=initial))


@capability_required("edit")
def connection_edit(request, pk):
    connection = get_object_or_404(_connections_qs(), pk=pk)
    form = ConnectionForm(request.POST or None, instance=connection)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Connection saved.")
        return redirect(_back_to(request, connection))
    return _connection_form_page(request, form, connection)


@capability_required("edit")
@require_POST
def connection_delete(request, pk):
    connection = get_object_or_404(Connection, pk=pk)
    label = str(connection)
    connection.delete()
    messages.success(request, f"Disconnected {label}.")
    return redirect(_back_to(request, connection))


def connection_trace(request, pk):
    connection = get_object_or_404(_connections_qs(), pk=pk)
    hops, cables = trace(connection)
    return render(request, "dcim/partials/trace.html", {"hops": hops, "cables": cables})


def port_options(request):
    """HTMX: <option>s for the ports of a device. Connected ports are listed but disabled."""
    device_id = request.GET.get("device") or request.GET.get("a_device") or request.GET.get("b_device")
    device = Device.objects.filter(pk=device_id).first() if (device_id or "").isdigit() else None
    keep = request.GET.get("keep", "")
    ports = []
    if device:
        taken = set(Connection.objects.filter(a_port__device=device).values_list("a_port_id", flat=True))
        taken |= set(Connection.objects.filter(b_port__device=device).values_list("b_port_id", flat=True))
        for p in device.ports.all():
            ports.append((p, p.pk in taken and str(p.pk) != keep))
    return render(request, "dcim/partials/port_options.html", {"ports": ports, "device": device, "keep": keep})


# --------------------------------------------------------------------------
# Ports
# --------------------------------------------------------------------------


@capability_required("edit")
@require_POST
def ports_bulk_add(request, device_pk):
    device = get_object_or_404(Device, pk=device_pk)
    form = BulkPortsForm(request.POST)
    if form.is_valid():
        created, skipped = services.create_ports(
            device, form.names, type=form.cleaned_data["type"], speed=form.cleaned_data["speed"],
            mode=form.cleaned_data["mode"], pair_rear=form.cleaned_data["pair_rear"],
        )
        msg = f"Added {len(created)} port{'s' if len(created) != 1 else ''} to {device.name}."
        if skipped:
            msg += f" Skipped {len(skipped)} that already existed."
        messages.success(request, msg)
    else:
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
    return redirect(f"{device.get_absolute_url()}?tab=ports")


@capability_required("edit")
@require_POST
def ports_apply_template(request, device_pk):
    device = get_object_or_404(Device.objects.select_related("device_model"), pk=device_pk)
    try:
        created, skipped = services.apply_model_template(device)
    except PatternError as e:
        messages.error(request, f"The model's port template has a problem: {e}")
    else:
        messages.success(request, f"Added {len(created)} ports from the {device.device_model} template.")
    return redirect(f"{device.get_absolute_url()}?tab=ports")


@capability_required("edit")
def port_edit(request, pk):
    port = get_object_or_404(Port.objects.select_related("device"), pk=pk)
    form = PortForm(request.POST or None, instance=port)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"{port} saved.")
        return redirect(f"{port.device.get_absolute_url()}?tab=ports")
    return render(request, "dcim/port_form.html", {
        "form": form, "port": port, "connection": port.connection(), "active": "devices",
    }, status=400 if form.is_bound else 200)


@capability_required("edit")
@require_POST
def port_delete(request, pk):
    port = get_object_or_404(Port.objects.select_related("device"), pk=pk)
    device = port.device
    connection = port.connection()
    port.delete()
    note = " Its cable was disconnected." if connection else ""
    messages.success(request, f"Port {port.name} deleted from {device.name}.{note}")
    return redirect(f"{device.get_absolute_url()}?tab=ports")


def pattern_preview(request):
    """HTMX: how many ports a template/pattern makes."""
    text = request.GET.get("patterns") or request.GET.get("port_template") or ""
    try:
        names = expand_lines(text)
        error = None
    except PatternError as e:
        names, error = [], str(e)
    return render(request, "dcim/partials/pattern_preview.html", {"names": names, "error": error})


def device_ports_context(device):
    """Everything the device page's Ports tab needs, with one query per kind of data."""
    ports = list(device.ports.select_related("untagged_vlan", "paired_port").prefetch_related("tagged_vlans"))
    conns = Connection.objects.filter(Q(a_port__device=device) | Q(b_port__device=device)).select_related(
        "a_port__device", "b_port__device", "b_external"
    )
    by_port = {}
    for c in conns:
        by_port[c.a_port_id] = (c, c.b_port, c.b_external)
        if c.b_port_id:
            by_port[c.b_port_id] = (c, c.a_port, None)
    for p in ports:
        p.conn, p.peer, p.external = by_port.get(p.pk, (None, None, None))
        p.vlans = services.vlan_summary(p)
        if not p.enabled:
            p.state = "disabled"
        elif p.conn:
            p.state = "up"
        else:
            p.state = "empty"
    front = [p for p in ports if p.type != Port.Type.REAR]
    rear = [p for p in ports if p.type == Port.Type.REAR]
    rows = [front[0::2], front[1::2]] if len(front) > 16 else [front]
    return {
        "ports": ports, "panel_rows": rows, "rear_ports": rear, "connected": sum(1 for p in ports if p.conn),
        "bulk_form": BulkPortsForm(initial={"pair_rear": device.device_type == "patch_panel"}),
        "has_template": bool(device.device_model.port_template.strip()),
    }

