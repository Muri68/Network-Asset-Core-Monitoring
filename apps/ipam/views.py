import ipaddress

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import ProtectedError, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.accounts.decorators import capability_required
from apps.inventory.models import Device

from .forms import CircuitForm, IPAddressForm, ISPForm, PrefixForm
from .models import ISP, Circuit, IPAddress, Prefix, prefix_for

ROWS_PER_PAGE = 256


# --------------------------------------------------------------------------
# Address bookkeeping
# --------------------------------------------------------------------------


def _mgmt_ips():
    """{address int: device} for every device with a management IP."""
    return {int(ipaddress.ip_address(d.mgmt_ip)): d
            for d in Device.objects.exclude(mgmt_ip__isnull=True).exclude(status=Device.Status.RETIRED).only(
                "name", "mgmt_ip", "device_type")}


def used_addresses(prefix, mgmt=None):
    """Usable addresses in `prefix` that are taken: recorded IPs, device management IPs and the gateway."""
    mgmt = _mgmt_ips() if mgmt is None else mgmt
    lo, hi = prefix.network_int, prefix.broadcast_int
    if prefix.prefixlen < 31:
        lo, hi = lo + 1, hi - 1
    used = set(IPAddress.objects.filter(address_int__gte=lo, address_int__lte=hi).values_list("address_int", flat=True))
    used |= {n for n in mgmt if lo <= n <= hi}
    if prefix.gateway:
        used.add(int(ipaddress.ip_address(prefix.gateway)))
    return used


def utilisation(prefix, mgmt=None):
    used = len(used_addresses(prefix, mgmt))
    return used, round(used * 100 / prefix.usable) if prefix.usable else 0


def next_free(prefix, used=None):
    used = used_addresses(prefix) if used is None else used
    start = prefix.network_int + (0 if prefix.prefixlen >= 31 else 1)
    end = prefix.broadcast_int - (0 if prefix.prefixlen >= 31 else 1)
    for n in range(start, end + 1):
        if n not in used:
            return str(ipaddress.ip_address(n))
    return None


def address_rows(prefix, numbers):
    """Table rows for the given address numbers in `prefix`, merging records, management IPs and roles."""
    if not numbers:
        return []
    lo, hi = min(numbers), max(numbers)
    records = {ip.address_int: ip for ip in IPAddress.objects.filter(address_int__gte=lo, address_int__lte=hi)
               .select_related("device", "port")}
    mgmt = _mgmt_ips()
    gateway = int(ipaddress.ip_address(prefix.gateway)) if prefix.gateway else None
    rows = []
    for n in numbers:
        row = {"address": str(ipaddress.ip_address(n)), "record": records.get(n), "mgmt_device": None, "role": "host"}
        if prefix.prefixlen < 31 and n == prefix.network_int:
            row.update(role="network", status="system")
        elif prefix.prefixlen < 31 and n == prefix.broadcast_int:
            row.update(role="broadcast", status="system")
        else:
            if n == gateway:
                row["role"] = "gateway"
            if row["record"]:
                row["status"] = row["record"].status
            elif n in mgmt:
                row.update(status="used", mgmt_device=mgmt[n])
            elif n == gateway:
                row["status"] = "reserved"
            else:
                row["status"] = "free"
        rows.append(row)
    return rows


# --------------------------------------------------------------------------
# Prefixes
# --------------------------------------------------------------------------


def prefixes(request):
    kind = request.GET.get("kind", "")
    q = request.GET.get("q", "").strip()
    qs = Prefix.objects.select_related("isp", "vlan", "location")
    if kind in Prefix.Kind.values:
        qs = qs.filter(kind=kind)
    if q:
        match = Q(cidr__icontains=q) | Q(description__icontains=q) | Q(isp__name__icontains=q)
        try:
            n = int(ipaddress.ip_address(q))
            match |= Q(network_int__lte=n, broadcast_int__gte=n)  # find the prefix containing an IP
        except ValueError:
            pass
        qs = qs.filter(match)
    mgmt = _mgmt_ips()
    rows = list(qs)
    for p in rows:
        p.used, p.pct = utilisation(p, mgmt)
        p.depth = 0
    # Indent nested prefixes under their parents.
    for p in rows:
        p.depth = sum(1 for o in rows if o.pk != p.pk and o.network_int <= p.network_int
                      and o.broadcast_int >= p.broadcast_int and o.prefixlen < p.prefixlen)
    return render(request, "ipam/prefixes.html", {
        "prefixes": rows, "kind": kind, "q": q, "total": Prefix.objects.count(), "active": "prefixes",
    })


def prefix_detail(request, pk):
    prefix = get_object_or_404(Prefix.objects.select_related("isp", "circuit", "vlan", "location"), pk=pk)
    used = used_addresses(prefix)
    show = request.GET.get("show") or ("all" if prefix.total <= ROWS_PER_PAGE else "used")
    if show == "used":
        lo, hi = prefix.network_int, prefix.broadcast_int
        recorded = set(IPAddress.objects.filter(address_int__gte=lo, address_int__lte=hi)
                       .values_list("address_int", flat=True))
        numbers = sorted(used | recorded)
        page = None
    else:
        page = Paginator(range(prefix.network_int, prefix.broadcast_int + 1), ROWS_PER_PAGE).get_page(
            request.GET.get("page"))
        numbers = list(page.object_list)
    rows = address_rows(prefix, numbers)
    map_numbers = list(range(prefix.network_int, min(prefix.broadcast_int, prefix.network_int + ROWS_PER_PAGE - 1) + 1))
    counts = {"used": 0, "reserved": 0, "dhcp": 0}
    for r in IPAddress.objects.filter(address_int__gte=prefix.network_int, address_int__lte=prefix.broadcast_int):
        counts[r.status] = counts.get(r.status, 0) + 1
    used_count = len(used)
    return render(request, "ipam/prefix_detail.html", {
        "p": prefix, "rows": rows, "page": page, "show": show,
        "map": address_rows(prefix, map_numbers), "map_truncated": prefix.total > ROWS_PER_PAGE,
        "used_count": used_count, "pct": round(used_count * 100 / prefix.usable) if prefix.usable else 0,
        "free_count": prefix.usable - used_count, "counts": counts, "next_free": next_free(prefix, used),
        "parent": prefix.parent(), "children": prefix.children(), "active": "prefixes",
    })


def _prefix_form_page(request, form, prefix=None):
    return render(request, "ipam/prefix_form.html", {"form": form, "prefix": prefix, "active": "prefixes"},
                  status=400 if form.is_bound else 200)


@capability_required("edit")
def prefix_create(request):
    form = PrefixForm(request.POST or None, initial={"cidr": request.GET.get("cidr", "")})
    if request.method == "POST" and form.is_valid():
        prefix = form.save()
        messages.success(request, f"Prefix {prefix} added.")
        return redirect(prefix)
    return _prefix_form_page(request, form)


@capability_required("edit")
def prefix_edit(request, pk):
    prefix = get_object_or_404(Prefix, pk=pk)
    form = PrefixForm(request.POST or None, instance=prefix)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Prefix {prefix} saved.")
        return redirect(prefix)
    return _prefix_form_page(request, form, prefix)


@capability_required("edit")
@require_POST
def prefix_delete(request, pk):
    prefix = get_object_or_404(Prefix, pk=pk)
    prefix.delete()
    messages.success(request, f"Prefix {prefix} deleted. Its IP assignments are kept.")
    return redirect("ipam:prefixes")


# --------------------------------------------------------------------------
# IP addresses
# --------------------------------------------------------------------------


def _back(request, ip=None):
    nxt = request.POST.get("next") or request.GET.get("next")
    if nxt and nxt.startswith("/"):
        return nxt
    prefix = ip.prefix if ip else None
    return prefix.get_absolute_url() if prefix else reverse("ipam:prefixes")


def _ip_form_page(request, form, ip=None):
    address = form["address"].value() or ""
    try:
        prefix = prefix_for(address) if address else None
    except ValueError:
        prefix = None
    return render(request, "ipam/ip_form.html", {
        "form": form, "ip": ip, "prefix": prefix, "next": request.GET.get("next", ""), "active": "prefixes",
    }, status=400 if form.is_bound else 200)


@capability_required("edit")
def ip_create(request):
    if request.method == "POST":
        form = IPAddressForm(request.POST)
        if form.is_valid():
            ip = form.save()
            messages.success(request, f"{ip} assigned.")
            return redirect(_back(request, ip))
        return _ip_form_page(request, form)
    initial = {k: request.GET[k] for k in ("address", "device") if request.GET.get(k)}
    return _ip_form_page(request, IPAddressForm(initial=initial))


@capability_required("edit")
def ip_edit(request, pk):
    ip = get_object_or_404(IPAddress, pk=pk)
    form = IPAddressForm(request.POST or None, instance=ip)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"{ip} saved.")
        return redirect(_back(request, ip))
    return _ip_form_page(request, form, ip)


@capability_required("edit")
@require_POST
def ip_delete(request, pk):
    ip = get_object_or_404(IPAddress, pk=pk)
    target = _back(request, ip)
    ip.delete()
    messages.success(request, f"{ip} is free again.")
    return redirect(target)


def device_ips(device):
    """IP addresses for the device page: management IP plus IPAM records (with NAT)."""
    records = list(device.ip_addresses.select_related("port", "prefix"))
    mgmt_in_ipam = any(r.address == device.mgmt_ip for r in records)
    return {"ip_records": records, "mgmt_in_ipam": mgmt_in_ipam,
            "mgmt_count": 1 if device.mgmt_ip and not mgmt_in_ipam else 0,
            "mgmt_prefix": prefix_for(device.mgmt_ip) if device.mgmt_ip else None}


# --------------------------------------------------------------------------
# ISPs & circuits
# --------------------------------------------------------------------------


def isps(request):
    providers = ISP.objects.prefetch_related("circuits__termination_port__device", "prefixes")
    mgmt = _mgmt_ips()
    for isp in providers:
        for p in isp.prefixes.all():
            p.used, p.pct = utilisation(p, mgmt)
    return render(request, "ipam/isps.html", {"isps": providers, "active": "isps"})


def _simple(request, form, title):
    return render(request, "inventory/simple_form.html", {
        "form": form, "title": title, "cancel_url": "ipam:isps", "crumb": "IP addressing / ISPs & circuits",
        "active": "isps",
    }, status=400 if form.is_bound else 200)


@capability_required("edit")
def isp_create(request):
    form = ISPForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        isp = form.save()
        messages.success(request, f"{isp} added.")
        return redirect("ipam:isps")
    return _simple(request, form, "Add ISP")


@capability_required("edit")
def isp_edit(request, pk):
    isp = get_object_or_404(ISP, pk=pk)
    form = ISPForm(request.POST or None, instance=isp)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"{isp} saved.")
        return redirect("ipam:isps")
    return _simple(request, form, f"Edit {isp}")


@capability_required("edit")
@require_POST
def isp_delete(request, pk):
    isp = get_object_or_404(ISP, pk=pk)
    try:
        isp.delete()
        messages.success(request, f"{isp} deleted.")
    except ProtectedError:
        messages.error(request, f"{isp} still has circuits. Delete those first.")
    return redirect("ipam:isps")


@capability_required("edit")
def circuit_create(request):
    form = CircuitForm(request.POST or None, initial={"isp": request.GET.get("isp")})
    if request.method == "POST" and form.is_valid():
        circuit = form.save()
        messages.success(request, f"Circuit {circuit} added.")
        return redirect("ipam:isps")
    return _simple(request, form, "Add circuit")


@capability_required("edit")
def circuit_edit(request, pk):
    circuit = get_object_or_404(Circuit, pk=pk)
    form = CircuitForm(request.POST or None, instance=circuit)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Circuit {circuit} saved.")
        return redirect("ipam:isps")
    return _simple(request, form, f"Edit circuit {circuit}")


@capability_required("edit")
@require_POST
def circuit_delete(request, pk):
    circuit = get_object_or_404(Circuit, pk=pk)
    circuit.delete()
    messages.success(request, f"Circuit {circuit} deleted.")
    return redirect("ipam:isps")


def calculator(request):
    return render(request, "ipam/calculator.html", {"active": "calculator"})
