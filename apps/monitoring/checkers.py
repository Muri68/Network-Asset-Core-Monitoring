"""
The actual network checks (PLAN.md §7.1). Pure asyncio, no database access, so they're easy to test
and can run hundreds at once.

Each checker returns a Result; failures carry an error_class used for the cause shown to people:
timeout, host_unreachable, connection_refused, dns_failure, tls_error, ssl_expired, http_status,
keyword_missing, dns_mismatch.
"""

import asyncio
import datetime
import socket
import ssl
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import dns.asyncresolver
import dns.exception
import dns.resolver
import httpx
import icmplib
from cryptography import x509
from cryptography.x509.oid import NameOID

from .models import Monitor, parse_status_ranges


@dataclass
class Result:
    ok: bool
    response_ms: int | None = None
    status_code: int | None = None
    error_class: str = ""
    message: str = ""
    cert_expires_at: datetime.datetime | None = None


def _ms(start):
    return max(1, round((time.perf_counter() - start) * 1000))


def _failure(error_class, message):
    return Result(ok=False, error_class=error_class, message=message[:255])


def _root_os_error(exc):
    """The deepest OSError behind a library exception, e.g. httpx → OSError → ConnectionRefusedError."""
    seen, found = set(), None
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, OSError):
            found = exc
        exc = exc.__cause__ or exc.__context__
    return found


def _classify_os_error(exc, host, port=None):
    where = f"{host}:{port}" if port else host
    if isinstance(exc, ConnectionRefusedError):
        return _failure("connection_refused", f"Connection refused ({where})")
    if isinstance(exc, socket.gaierror):
        return _failure("dns_failure", f"Can't resolve {host}")
    if isinstance(exc, TimeoutError | asyncio.TimeoutError):
        return _failure("timeout", f"Timed out connecting to {where}")
    return _failure("host_unreachable", f"{where} unreachable ({exc.__class__.__name__}: {exc})")


async def check_ping(m):
    try:
        host = await icmplib.async_ping(m.target, count=2, interval=0.3, timeout=m.timeout_s, privileged=False)
    except icmplib.NameLookupError:
        return _failure("dns_failure", f"Can't resolve {m.target}")
    except icmplib.ICMPLibError as e:
        return _failure("host_unreachable", f"Ping failed: {e}")
    if host.is_alive:
        return Result(ok=True, response_ms=max(1, round(host.avg_rtt)))
    return _failure("timeout", f"No ping reply from {m.target}")


async def check_tcp(m):
    start = time.perf_counter()
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(m.target, m.port), timeout=m.timeout_s)
    except (TimeoutError, OSError) as e:
        return _classify_os_error(e, m.target, m.port)
    elapsed = _ms(start)
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return Result(ok=True, response_ms=elapsed)


async def check_http(m):
    ranges = parse_status_ranges(m.expected_status)
    host = urlsplit(m.target).hostname or m.target
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(verify=not m.ignore_tls, follow_redirects=m.follow_redirects,
                                     timeout=m.timeout_s, headers={"User-Agent": "NetCore-monitor/1.0"}) as client:
            response = await client.request(m.http_method, m.target)
            body = response.text if m.type == Monitor.Type.KEYWORD else ""
    except httpx.TimeoutException:
        return _failure("timeout", f"No response from {host} within {m.timeout_s}s")
    except httpx.ConnectError as e:
        root = _root_os_error(e)
        if root is not None and not isinstance(root, ssl.SSLError):
            return _classify_os_error(root, host)
        text = str(e)
        if "CERTIFICATE_VERIFY_FAILED" in text or "SSL" in text:
            expired = "expired" in text.lower()
            return _failure("ssl_expired" if expired else "tls_error",
                            "Certificate expired" if expired else f"TLS error: {text[:150]}")
        if "Name or service not known" in text or "nodename nor servname" in text or "getaddrinfo" in text:
            return _failure("dns_failure", f"Can't resolve {host}")
        if "refused" in text.lower():
            return _failure("connection_refused", f"Connection refused ({host})")
        return _failure("host_unreachable", f"Can't connect to {host}: {text[:150]}")
    except httpx.HTTPError as e:
        return _failure("host_unreachable", f"HTTP error: {e.__class__.__name__}: {str(e)[:150]}")
    elapsed = _ms(start)
    code = response.status_code
    if not any(lo <= code <= hi for lo, hi in ranges):
        reason = response.reason_phrase or ""
        return Result(ok=False, response_ms=elapsed, status_code=code, error_class="http_status",
                      message=f"HTTP {code} {reason}".strip())
    if m.type == Monitor.Type.KEYWORD and m.keyword not in body:
        return Result(ok=False, response_ms=elapsed, status_code=code, error_class="keyword_missing",
                      message=f"Keyword “{m.keyword}” not found")
    return Result(ok=True, response_ms=elapsed, status_code=code)


async def check_dns(m):
    resolver = dns.asyncresolver.Resolver()
    resolver.lifetime = m.timeout_s
    start = time.perf_counter()
    try:
        answer = await resolver.resolve(m.target, m.dns_record_type)
    except dns.resolver.NXDOMAIN:
        return _failure("dns_failure", f"{m.target} doesn't exist (NXDOMAIN)")
    except dns.resolver.NoAnswer:
        return _failure("dns_failure", f"No {m.dns_record_type} record for {m.target}")
    except dns.exception.Timeout:
        return _failure("timeout", "DNS lookup timed out")
    except dns.exception.DNSException as e:
        return _failure("dns_failure", f"DNS failure: {e.__class__.__name__}")
    elapsed = _ms(start)
    values = [r.to_text().rstrip(".").strip('"') for r in answer]
    if m.dns_expected and not any(m.dns_expected.rstrip(".") in v for v in values):
        return Result(ok=False, response_ms=elapsed, error_class="dns_mismatch",
                      message=f"Got {', '.join(values)[:150]}, expected {m.dns_expected}")
    return Result(ok=True, response_ms=elapsed)


async def _unverified_cert(m, port):
    """The server's own certificate without checking the chain, so a broken chain still reports expiry."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    _reader, writer = await asyncio.wait_for(
        asyncio.open_connection(m.target, port, ssl=context, server_hostname=m.target), timeout=m.timeout_s)
    try:
        der = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
    finally:
        writer.close()
    return x509.load_der_x509_certificate(der)


def _issuer_name(cert):
    names = cert.issuer.get_attributes_for_oid(NameOID.COMMON_NAME)
    return names[0].value if names else cert.issuer.rfc4514_string()


async def check_ssl(m):
    port = m.port or 443
    context = ssl.create_default_context()
    start = time.perf_counter()
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(m.target, port, ssl=context, server_hostname=m.target), timeout=m.timeout_s)
    except ssl.SSLCertVerificationError as e:
        if "expired" in str(e).lower():
            return _failure("ssl_expired", f"Certificate for {m.target} has expired")
        try:
            cert = await _unverified_cert(m, port)
        except (TimeoutError, OSError, ssl.SSLError, ValueError):
            return _failure("tls_error", f"Certificate problem: {e.verify_message or e}")
        expires = cert.not_valid_after_utc
        if m.ignore_tls:  # expiry-only monitoring: the chain problem is known and accepted
            return Result(ok=True, response_ms=_ms(start), cert_expires_at=expires)
        if e.verify_code == 20:  # X509_V_ERR_UNABLE_TO_GET_ISSUER_CERT_LOCALLY
            message = (f"Incomplete certificate chain: the server doesn't send the intermediate "
                       f"certificate \"{_issuer_name(cert)}\". Browsers may still trust it, other clients won't")
        else:
            message = f"Certificate problem: {e.verify_message or e}"
        result = _failure("tls_error", message)
        result.cert_expires_at = expires
        return result
    except ssl.SSLError as e:
        return _failure("tls_error", f"TLS error: {e}")
    except (TimeoutError, OSError) as e:
        return _classify_os_error(e, m.target, port)
    elapsed = _ms(start)
    cert = writer.get_extra_info("peercert") or {}
    writer.close()
    try:
        await writer.wait_closed()
    except (OSError, ssl.SSLError):
        pass
    not_after = cert.get("notAfter")
    expires = (datetime.datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after), tz=datetime.UTC)
               if not_after else None)
    return Result(ok=True, response_ms=elapsed, cert_expires_at=expires)


CHECKERS = {
    Monitor.Type.PING: check_ping,
    Monitor.Type.TCP: check_tcp,
    Monitor.Type.HTTP: check_http,
    Monitor.Type.KEYWORD: check_http,
    Monitor.Type.DNS: check_dns,
    Monitor.Type.SSL: check_ssl,
}


async def run_check(monitor):
    """Run one check; never raises (an unexpected error is a failed check with the reason)."""
    try:
        # Hard ceiling in case a library ignores its own timeout.
        return await asyncio.wait_for(CHECKERS[monitor.type](monitor), timeout=monitor.timeout_s + 5)
    except TimeoutError:
        return _failure("timeout", f"No answer within {monitor.timeout_s}s")
    except Exception as e:  # noqa: BLE001 — a broken check must not stop the monitor process
        return _failure("error", f"Check failed: {e.__class__.__name__}: {str(e)[:150]}")
