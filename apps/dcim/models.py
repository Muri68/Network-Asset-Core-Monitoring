"""VLANs, ports, external endpoints and connections (PLAN.md §6.3)."""

import re

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from apps.core.models import TrackedModel
from apps.inventory.models import Device, Location

from .patterns import natural_key


class VLAN(TrackedModel):
    vid = models.PositiveSmallIntegerField("VLAN ID", validators=[MinValueValidator(1), MaxValueValidator(4094)])
    name = models.CharField(max_length=100)
    location = models.ForeignKey(Location, on_delete=models.PROTECT, null=True, blank=True, related_name="vlans",
                                 help_text="Leave empty for a VLAN used everywhere.")
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "VLAN"
        ordering = ["vid", "location__name"]
        constraints = [
            models.UniqueConstraint(fields=["location", "vid"], condition=Q(location__isnull=False),
                                    name="vlan_unique_vid_per_location",
                                    violation_error_message="This location already has a VLAN with that ID."),
            models.UniqueConstraint(fields=["vid"], condition=Q(location__isnull=True), name="vlan_unique_global_vid",
                                    violation_error_message="There's already a global VLAN with that ID."),
        ]

    def __str__(self):
        return f"{self.vid} · {self.name}"


class Port(TrackedModel):
    class Type(models.TextChoices):
        COPPER = "copper", "Copper (RJ45)"
        SFP = "sfp", "SFP / SFP+"
        FIBRE = "fibre", "Fibre"
        CONSOLE = "console", "Console"
        FRONT = "front", "Patch panel — front"
        REAR = "rear", "Patch panel — rear"
        OTHER = "other", "Other"

    class Mode(models.TextChoices):
        NONE = "", "—"
        ACCESS = "access", "Access"
        TRUNK = "trunk", "Trunk"
        ROUTED = "routed", "Routed"

    SPEEDS = [("", "—"), ("100M", "100 Mb"), ("1G", "1 Gb"), ("2.5G", "2.5 Gb"), ("5G", "5 Gb"), ("10G", "10 Gb"),
              ("25G", "25 Gb"), ("40G", "40 Gb"), ("100G", "100 Gb")]

    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="ports")
    name = models.CharField(max_length=64)
    label = models.CharField(max_length=64, blank=True, help_text="Printed label, if different from the name.")
    type = models.CharField(max_length=10, choices=Type.choices, default=Type.COPPER)
    speed = models.CharField(max_length=5, choices=SPEEDS, blank=True)
    mode = models.CharField(max_length=6, choices=Mode.choices, blank=True)
    untagged_vlan = models.ForeignKey(VLAN, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name="untagged_ports", verbose_name="untagged / access VLAN")
    tagged_vlans = models.ManyToManyField(VLAN, blank=True, related_name="tagged_ports")
    remark = models.CharField(max_length=255, blank=True)
    enabled = models.BooleanField(default=True)
    # Patch panels: a front port passes straight through to its rear port.
    paired_port = models.OneToOneField("self", on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name="+", verbose_name="paired port")
    sort_key = models.CharField(max_length=200, editable=False, db_index=True)

    class Meta:
        ordering = ["device__name", "sort_key"]
        constraints = [
            models.UniqueConstraint("device", Lower("name"), name="port_unique_name_per_device",
                                    violation_error_message="This device already has a port with that name."),
        ]

    def __str__(self):
        return f"{self.device} · {self.name}"

    def save(self, *args, **kwargs):
        self.sort_key = natural_key(self.name)
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = {*kwargs["update_fields"], "sort_key"}
        super().save(*args, **kwargs)

    @property
    def short_name(self):
        """Front-panel label: 'Gi1/0/10' → '10', 'wan1' → 'wan1', 'port11' → 'p11', 'ether12' → 'e12'."""
        tail = self.name.rsplit("/", 1)[-1].replace(" (rear)", "")
        if len(tail) <= 4:
            return tail
        if m := re.fullmatch(r"([A-Za-z][A-Za-z\-]*?)(\d+)", tail):
            return f"{m[1][0]}{m[2]}"
        return tail[:4]

    def connection(self):
        return Connection.objects.filter(Q(a_port=self) | Q(b_port=self)).first()


class ExternalEndpoint(TrackedModel):
    """Something a cable goes to that isn't a device in NetCore: an ISP handoff, a wall jack…"""

    name = models.CharField(max_length=120)
    location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True, blank=True)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "external endpoint"
        ordering = ["name"]
        constraints = [models.UniqueConstraint(Lower("name"), name="external_endpoint_ci_unique",
                                               violation_error_message="That endpoint already exists.")]

    def __str__(self):
        return self.name


class Connection(TrackedModel):
    """One cable. Entered once, shown on both devices."""

    class CableType(models.TextChoices):
        CAT5E = "cat5e", "Cat5e"
        CAT6 = "cat6", "Cat6"
        CAT6A = "cat6a", "Cat6A"
        FIBRE_SM = "fibre_sm", "Fibre single-mode"
        FIBRE_MM = "fibre_mm", "Fibre multi-mode"
        DAC = "dac", "DAC"
        OTHER = "other", "Other"

    a_port = models.OneToOneField(Port, on_delete=models.CASCADE, related_name="connection_a", verbose_name="side A")
    b_port = models.OneToOneField(Port, on_delete=models.CASCADE, related_name="connection_b", null=True,
                                  blank=True, verbose_name="side B")
    b_external = models.ForeignKey(ExternalEndpoint, on_delete=models.PROTECT, related_name="connections", null=True,
                                   blank=True, verbose_name="side B (external)")
    function = models.CharField(max_length=200, help_text="What the link is for, e.g. “Internet + VLAN trunk”.")
    cable_label = models.CharField(max_length=50, blank=True)
    cable_type = models.CharField(max_length=10, choices=CableType.choices, blank=True)
    colour = models.CharField(max_length=30, blank=True)
    length_m = models.DecimalField("length (m)", max_digits=6, decimal_places=1, null=True, blank=True)
    remark = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["a_port__device__name", "a_port__sort_key"]
        constraints = [
            models.CheckConstraint(
                condition=(Q(b_port__isnull=False, b_external__isnull=True)
                           | Q(b_port__isnull=True, b_external__isnull=False)),
                name="connection_exactly_one_b_side",
                violation_error_message="Side B must be either a device port or an external endpoint.",
            ),
            models.CheckConstraint(condition=~Q(a_port=models.F("b_port")), name="connection_not_to_itself",
                                   violation_error_message="A port can't be connected to itself."),
        ]

    def __str__(self):
        return f"{self.a_port} ↔ {self.b_port or self.b_external}"

    @property
    def b_label(self):
        return str(self.b_port) if self.b_port_id else str(self.b_external)

    def clean(self):
        errors = {}
        taken = Connection.objects.exclude(pk=self.pk)
        for field in ("a_port", "b_port"):
            port_id = getattr(self, f"{field}_id")
            if port_id and taken.filter(Q(a_port_id=port_id) | Q(b_port_id=port_id)).exists():
                port = Port.objects.select_related("device").get(pk=port_id)
                errors[field] = f"{port} is already connected. Remove that connection first."
        if errors:
            raise ValidationError(errors)

    def other_end(self, port):
        """(port or None, external or None) on the far side from `port`."""
        if port.pk == self.a_port_id:
            return self.b_port, self.b_external
        return self.a_port, None


def connection_for(port):
    return Connection.objects.select_related(
        "a_port__device", "b_port__device", "b_external"
    ).filter(Q(a_port=port) | Q(b_port=port)).first()


def trace(connection):
    """
    Follow a cable run end to end through patch-panel pass-throughs.

    Returns (hops, cables). Each hop is {"port", "external", "passthrough"}; passthrough hops are the
    rear/front twin of the port before them, e.g.
    FW:port2 → PP:2 → PP:2 (rear) [passthrough] → SW:Gi1/0/10
    """
    # 1. Walk backwards from side A to the true start of the run.
    seen = {connection.pk}
    first, start_port, start_external = connection, connection.a_port, None
    while start_port is not None and start_port.paired_port_id:
        previous = connection_for(start_port.paired_port)
        if previous is None or previous.pk in seen:
            break
        seen.add(previous.pk)
        first = previous
        start_port, start_external = previous.other_end(start_port.paired_port)

    # 2. Walk forwards to the far end.
    hops = [{"port": start_port, "external": start_external, "passthrough": False}]
    cables, seen = [], set()
    current, from_port = first, start_port
    while current is not None and current.pk not in seen:
        seen.add(current.pk)
        cables.append(current)
        if from_port is None:  # the run starts at an external endpoint (always side B)
            far_port, far_external = current.a_port, None
        else:
            far_port, far_external = current.other_end(from_port)
        if far_external is not None:
            hops.append({"port": None, "external": far_external, "passthrough": False})
            break
        hops.append({"port": far_port, "external": None, "passthrough": False})
        if not far_port.paired_port_id:
            break
        following = connection_for(far_port.paired_port)
        if following is None or following.pk in seen:
            break
        hops.append({"port": far_port.paired_port, "external": None, "passthrough": True})
        current, from_port = following, far_port.paired_port
    return hops, cables
