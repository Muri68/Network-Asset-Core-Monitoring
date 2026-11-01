"""ISPs, circuits, prefixes and IP addresses (PLAN.md §6.4). IPv4 only in v1 (assumption A4)."""

import ipaddress

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower
from django.urls import reverse
from django.utils import timezone

from apps.core.models import TrackedModel
from apps.dcim.models import VLAN, Port
from apps.inventory.models import Device, Location


class ISP(TrackedModel):
    name = models.CharField(max_length=100)
    account_number = models.CharField(max_length=60, blank=True)
    support_phone = models.CharField(max_length=40, blank=True)
    support_email = models.EmailField(blank=True)
    account_manager = models.CharField(max_length=100, blank=True)
    portal_url = models.URLField("customer portal", blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "ISP"
        ordering = ["name"]
        constraints = [models.UniqueConstraint(Lower("name"), name="isp_name_ci_unique",
                                               violation_error_message="That ISP already exists.")]

    def __str__(self):
        return self.name


class Circuit(TrackedModel):
    class Type(models.TextChoices):
        FIBRE = "fibre", "Fibre"
        RADIO = "radio", "Radio / microwave"
        COPPER = "copper", "Copper / DSL"
        LTE = "lte", "LTE / 5G"
        SATELLITE = "satellite", "Satellite"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        PLANNED = "planned", "Planned"
        CANCELLED = "cancelled", "Cancelled"

    isp = models.ForeignKey(ISP, on_delete=models.PROTECT, related_name="circuits", verbose_name="ISP")
    circuit_id = models.CharField("circuit ID", max_length=60, unique=True,
                                  error_messages={"unique": "Another circuit already has this ID."})
    circuit_type = models.CharField("type", max_length=10, choices=Type.choices, default=Type.FIBRE)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    bandwidth = models.CharField(max_length=60, blank=True, help_text="e.g. 200 / 200 Mbps")
    install_date = models.DateField(null=True, blank=True)
    contract_start = models.DateField(null=True, blank=True)
    contract_end = models.DateField(null=True, blank=True)
    monthly_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True, help_text="e.g. NGN, USD")
    termination_port = models.ForeignKey(Port, on_delete=models.SET_NULL, null=True, blank=True,
                                         related_name="circuits", verbose_name="lands on port")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["isp__name", "circuit_id"]

    def __str__(self):
        return self.circuit_id

    @property
    def contract_days(self):
        return (self.contract_end - timezone.localdate()).days if self.contract_end else None

    def clean(self):
        if self.contract_start and self.contract_end and self.contract_end < self.contract_start:
            raise ValidationError({"contract_end": "The contract can't end before it starts."})


def parse_network(text):
    """'129.205.100.140/28' → (IPv4Network('129.205.100.128/28'), had_host_bits)."""
    text = (text or "").strip()
    if "/" not in text:
        raise ValidationError("Include the prefix length, e.g. 10.10.0.0/24.")
    try:
        loose = ipaddress.ip_network(text, strict=False)
    except ValueError as e:
        raise ValidationError(f"“{text}” isn't a valid network.") from e
    if loose.version != 4:
        raise ValidationError("Only IPv4 is supported for now.")
    return loose, ipaddress.ip_interface(text).ip != loose.network_address


class Prefix(TrackedModel):
    class Kind(models.TextChoices):
        PUBLIC = "public", "Public"
        PRIVATE = "private", "Private"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        RESERVED = "reserved", "Reserved"
        DEPRECATED = "deprecated", "Deprecated"

    cidr = models.CharField("prefix", max_length=18, unique=True,
                            error_messages={"unique": "That prefix already exists."})
    network_int = models.BigIntegerField(editable=False, db_index=True)
    broadcast_int = models.BigIntegerField(editable=False, db_index=True)
    prefixlen = models.PositiveSmallIntegerField(editable=False)
    kind = models.CharField(max_length=7, choices=Kind.choices, default=Kind.PRIVATE)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    isp = models.ForeignKey(ISP, on_delete=models.SET_NULL, null=True, blank=True, related_name="prefixes",
                            verbose_name="ISP")
    circuit = models.ForeignKey(Circuit, on_delete=models.SET_NULL, null=True, blank=True, related_name="prefixes")
    vlan = models.ForeignKey(VLAN, on_delete=models.SET_NULL, null=True, blank=True, related_name="prefixes",
                             verbose_name="VLAN")
    location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True, blank=True, related_name="prefixes")
    gateway = models.GenericIPAddressField(protocol="IPv4", null=True, blank=True,
                                           help_text="Leave empty to use the first usable address.")
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name_plural = "prefixes"
        ordering = ["network_int", "prefixlen"]

    def __str__(self):
        return self.cidr

    def get_absolute_url(self):
        return reverse("ipam:prefix_detail", args=[self.pk])

    # --- calculated values (not stored, PLAN.md §6.4) -------------------------

    @property
    def net(self):
        return ipaddress.ip_network(self.cidr)

    @property
    def netmask(self):
        return str(self.net.netmask)

    @property
    def wildcard(self):
        return str(self.net.hostmask)

    @property
    def network_address(self):
        return str(self.net.network_address)

    @property
    def broadcast(self):
        """None for /31 and /32, which have no broadcast address (RFC 3021)."""
        return None if self.prefixlen >= 31 else str(self.net.broadcast_address)

    @property
    def first_usable(self):
        return str(self.net.network_address + (0 if self.prefixlen >= 31 else 1))

    @property
    def last_usable(self):
        return str(self.net.broadcast_address - (0 if self.prefixlen >= 31 else 1))

    @property
    def total(self):
        return self.net.num_addresses

    @property
    def usable(self):
        return self.total if self.prefixlen >= 31 else self.total - 2

    def is_usable(self, address):
        n = int(ipaddress.ip_address(address))
        if not self.network_int <= n <= self.broadcast_int:
            return False
        return self.prefixlen >= 31 or self.network_int < n < self.broadcast_int

    # --- validation ------------------------------------------------------------

    def clean(self):
        errors = {}
        try:
            net, host_bits = parse_network(self.cidr)
        except ValidationError as e:
            raise ValidationError({"cidr": e.messages}) from e
        if host_bits:
            errors["cidr"] = (f"{self.cidr} has host bits set — the network is {net}. "
                              f"Use {net} (it contains {self.cidr.split('/')[0]}).")
        else:
            self.cidr = str(net)
            self._set_numbers(net)
            if self.gateway and not self.is_usable(self.gateway):
                errors["gateway"] = (f"The gateway must be a usable address in {net} "
                                     f"({self.first_usable}–{self.last_usable}).")
        if errors:
            raise ValidationError(errors)

    def _set_numbers(self, net):
        self.network_int = int(net.network_address)
        self.broadcast_int = int(net.broadcast_address)
        self.prefixlen = net.prefixlen

    def save(self, *args, **kwargs):
        net = ipaddress.ip_network(self.cidr)
        self.cidr = str(net)
        self._set_numbers(net)
        if not self.gateway and self.usable >= 1 and self.prefixlen < 31:
            self.gateway = self.first_usable
        super().save(*args, **kwargs)

    # --- relationships ---------------------------------------------------------

    def parent(self):
        return (Prefix.objects.filter(network_int__lte=self.network_int, broadcast_int__gte=self.broadcast_int,
                                      prefixlen__lt=self.prefixlen).order_by("-prefixlen").first())

    def children(self):
        return Prefix.objects.filter(network_int__gte=self.network_int, broadcast_int__lte=self.broadcast_int,
                                     prefixlen__gt=self.prefixlen)


def prefix_for(address):
    """The most specific prefix containing `address`, or None."""
    n = int(ipaddress.ip_address(address))
    return Prefix.objects.filter(network_int__lte=n, broadcast_int__gte=n).order_by("-prefixlen").first()


class IPAddress(TrackedModel):
    class Status(models.TextChoices):
        USED = "used", "Used"
        RESERVED = "reserved", "Reserved"
        DHCP = "dhcp", "DHCP pool"

    address = models.GenericIPAddressField(protocol="IPv4", unique=True,
                                           error_messages={"unique": "This IP address is already recorded."})
    address_int = models.BigIntegerField(editable=False, db_index=True)
    prefix = models.ForeignKey(Prefix, on_delete=models.SET_NULL, null=True, blank=True, editable=False,
                               related_name="ip_addresses")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.USED)
    device = models.ForeignKey(Device, on_delete=models.SET_NULL, null=True, blank=True, related_name="ip_addresses")
    port = models.ForeignKey(Port, on_delete=models.SET_NULL, null=True, blank=True, related_name="ip_addresses",
                             help_text="Optional: the interface the address is on.")
    service = models.CharField(max_length=120, blank=True, help_text="What uses it, e.g. “Customer portal”.")
    dns_name = models.CharField("DNS name", max_length=255, blank=True)
    nat_to = models.GenericIPAddressField("NAT to inside address", protocol="IPv4", null=True, blank=True,
                                          help_text="For public IPs forwarded to a private address.")
    nat_ports = models.CharField("NAT ports", max_length=120, blank=True, help_text="e.g. 443, 80 or 8443→443")
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "IP address"
        ordering = ["address_int"]

    def __str__(self):
        return self.address

    def clean(self):
        errors = {}
        if not self.address:
            return
        prefix = prefix_for(self.address)
        if prefix and not prefix.is_usable(self.address):
            role = "network" if int(ipaddress.ip_address(self.address)) == prefix.network_int else "broadcast"
            errors["address"] = f"{self.address} is the {role} address of {prefix}; it can't be assigned."
        owner = Device.objects.filter(mgmt_ip=self.address).exclude(pk=self.device_id).first()
        if owner is not None:
            errors["address"] = f"{self.address} is the management IP of {owner}."
        if self.port_id and self.device_id and self.port.device_id != self.device_id:
            errors["port"] = "Choose a port on the selected device."
        if self.port_id and not self.device_id:
            self.device = self.port.device
        if self.nat_to and self.nat_to == self.address:
            errors["nat_to"] = "An address can't NAT to itself."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.address_int = int(ipaddress.ip_address(self.address))
        self.prefix = prefix_for(self.address)
        super().save(*args, **kwargs)
