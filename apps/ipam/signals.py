"""Keep IPAddress.prefix pointing at the most specific prefix as prefixes come and go."""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import IPAddress, Prefix, prefix_for


def _rehome(network_int, broadcast_int):
    # .update() on purpose: the prefix link is derived, not a user change worth an audit entry.
    for ip in IPAddress.objects.filter(address_int__gte=network_int, address_int__lte=broadcast_int):
        best = prefix_for(ip.address)
        if ip.prefix_id != (best.pk if best else None):
            IPAddress.objects.filter(pk=ip.pk).update(prefix=best)


@receiver(post_save, sender=Prefix, dispatch_uid="ipam-prefix-saved")
def prefix_saved(sender, instance, raw=False, **kwargs):
    if not raw:
        _rehome(instance.network_int, instance.broadcast_int)


@receiver(post_delete, sender=Prefix, dispatch_uid="ipam-prefix-deleted")
def prefix_deleted(sender, instance, **kwargs):
    _rehome(instance.network_int, instance.broadcast_int)
