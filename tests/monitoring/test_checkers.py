"""Each checker against real local servers (PLAN.md §9 "Monitoring")."""

import asyncio
import datetime
import http.server
import socket
import ssl
import threading
import time

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from apps.monitoring import checkers
from apps.monitoring.checkers import run_check
from apps.monitoring.models import Monitor


def monitor(**kw):
    kw.setdefault("timeout_s", 2)
    return Monitor(name="t", **kw)


def check(m):
    return asyncio.run(run_check(m))


# --------------------------------------------------------------------------
# Local HTTP server
# --------------------------------------------------------------------------


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/slow":
            time.sleep(3)
        code = {"/ok": 200, "/fail": 503, "/moved": 302}.get(self.path, 404)
        body = b"<h1>Welcome to the portal</h1>"
        self.send_response(code)
        if code == 302:
            self.send_header("Location", "/ok")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def web():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestHTTP:
    def test_ok(self, web):
        r = check(monitor(type="http", target=f"{web}/ok"))
        assert r.ok and r.status_code == 200 and r.response_ms >= 1

    def test_bad_status(self, web):
        r = check(monitor(type="http", target=f"{web}/fail"))
        assert not r.ok and r.error_class == "http_status" and r.message.startswith("HTTP 503")

    def test_accepted_codes(self, web):
        assert check(monitor(type="http", target=f"{web}/fail", expected_status="200-299,503")).ok

    def test_redirects(self, web):
        assert check(monitor(type="http", target=f"{web}/moved")).status_code == 200
        r = check(monitor(type="http", target=f"{web}/moved", follow_redirects=False))
        assert not r.ok and r.status_code == 302

    def test_keyword(self, web):
        assert check(monitor(type="keyword", target=f"{web}/ok", keyword="Welcome")).ok
        r = check(monitor(type="keyword", target=f"{web}/ok", keyword="Maintenance"))
        assert not r.ok and r.error_class == "keyword_missing" and "Maintenance" in r.message

    def test_timeout(self, web):
        r = check(monitor(type="http", target=f"{web}/slow", timeout_s=1))
        assert not r.ok and r.error_class == "timeout"

    def test_refused(self):
        r = check(monitor(type="http", target=f"http://127.0.0.1:{free_port()}/"))
        assert not r.ok and r.error_class == "connection_refused"

    def test_unresolvable(self):
        r = check(monitor(type="http", target="http://does-not-exist.invalid/"))
        assert not r.ok and r.error_class in ("dns_failure", "host_unreachable")


class TestTCP:
    def test_open_port(self, web):
        port = int(web.rsplit(":", 1)[1])
        assert check(monitor(type="tcp", target="127.0.0.1", port=port)).ok

    def test_closed_port(self):
        r = check(monitor(type="tcp", target="127.0.0.1", port=free_port()))
        assert not r.ok and r.error_class == "connection_refused" and "127.0.0.1" in r.message


class TestPing:
    def test_localhost(self):
        r = check(monitor(type="ping", target="127.0.0.1"))
        if not r.ok and "permission" in r.message.lower():
            pytest.skip("Unprivileged ping not allowed here (Linux: set net.ipv4.ping_group_range)")
        assert r.ok

    def test_unresolvable(self):
        r = check(monitor(type="ping", target="does-not-exist.invalid"))
        assert not r.ok and r.error_class in ("dns_failure", "host_unreachable")


class TestDNS:
    class Answer(list):
        pass

    class Record:
        def __init__(self, text):
            self.text = text

        def to_text(self):
            return self.text

    def fake(self, monkeypatch, result):
        async def resolve(self_, name, rdtype):
            if isinstance(result, Exception):
                raise result
            return [TestDNS.Record(v) for v in result]

        monkeypatch.setattr(checkers.dns.asyncresolver.Resolver, "resolve", resolve)

    def test_resolves(self, monkeypatch):
        self.fake(monkeypatch, ["129.205.100.132"])
        assert check(monitor(type="dns", target="acme.example", dns_expected="129.205.100.132")).ok

    def test_wrong_answer(self, monkeypatch):
        self.fake(monkeypatch, ["10.0.0.9"])
        r = check(monitor(type="dns", target="acme.example", dns_expected="129.205.100.132"))
        assert not r.ok and r.error_class == "dns_mismatch"

    def test_nxdomain(self, monkeypatch):
        self.fake(monkeypatch, checkers.dns.resolver.NXDOMAIN())
        r = check(monitor(type="dns", target="nope.example"))
        assert not r.ok and r.error_class == "dns_failure"


# --------------------------------------------------------------------------
# TLS: a real local server with certificates signed by a test CA
# --------------------------------------------------------------------------


def _key():
    return ec.generate_private_key(ec.SECP256R1())


def _name(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    folder = tmp_path_factory.mktemp("pki")
    now = datetime.datetime.now(datetime.UTC)
    ca_key = _key()
    ca = (x509.CertificateBuilder().subject_name(_name("Test CA")).issuer_name(_name("Test CA"))
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - datetime.timedelta(days=30)).not_valid_after(now + datetime.timedelta(days=365))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                       content_commitment=False, key_encipherment=False, data_encipherment=False,
                                       key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    (folder / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))

    def server_cert(name, not_after, signer_key=ca_key, issuer=ca.subject):
        key = _key()
        cert = (x509.CertificateBuilder().subject_name(_name("localhost")).issuer_name(issuer)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(days=60)).not_valid_after(not_after)
                .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
                .sign(signer_key if signer_key is not None else key, hashes.SHA256()))
        (folder / f"{name}.pem").write_bytes(
            cert.public_bytes(serialization.Encoding.PEM)
            + key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()))
        return folder / f"{name}.pem", cert.not_valid_after_utc

    return {
        "ca": folder / "ca.pem",
        "valid": server_cert("valid", now + datetime.timedelta(days=40)),
        "expired": server_cert("expired", now - datetime.timedelta(days=1)),
    }


def tls_check(pki, cert_name, monkeypatch, **kw):
    cert_file, _ = pki[cert_name]
    real_factory = ssl.create_default_context

    def trusting_context():
        return real_factory(cafile=str(pki["ca"]))

    monkeypatch.setattr(checkers.ssl, "create_default_context", trusting_context)

    async def scenario():
        server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_ctx.load_cert_chain(str(cert_file))

        async def handle(reader, writer):
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=server_ctx)
        port = server.sockets[0].getsockname()[1]
        try:
            return await checkers.run_check(monitor(type="ssl", target="localhost", port=port, **kw))
        finally:
            server.close()

    return asyncio.run(scenario())


class TestSSL:
    def test_valid_certificate_reports_expiry(self, pki, monkeypatch):
        r = tls_check(pki, "valid", monkeypatch)
        assert r.ok and r.cert_expires_at is not None
        assert abs((r.cert_expires_at - pki["valid"][1]).total_seconds()) < 2

    def test_expired_certificate(self, pki, monkeypatch):
        r = tls_check(pki, "expired", monkeypatch)
        assert not r.ok and r.error_class == "ssl_expired"

    def test_untrusted_certificate(self, pki, monkeypatch):
        monkeypatch.setattr(checkers.ssl, "create_default_context", ssl.create_default_context)

        async def scenario():
            server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server_ctx.load_cert_chain(str(pki["valid"][0]))
            server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0, ssl=server_ctx)
            port = server.sockets[0].getsockname()[1]
            try:
                return await checkers.run_check(monitor(type="ssl", target="localhost", port=port))
            finally:
                server.close()

        r = asyncio.run(scenario())  # system trust store doesn't know the test CA
        assert not r.ok and r.error_class == "tls_error"
        assert "Incomplete certificate chain" in r.message and '"Test CA"' in r.message
        assert r.cert_expires_at is not None  # expiry still known despite the broken chain

    def test_expiry_only_ignores_chain_errors(self, pki, monkeypatch):
        monkeypatch.setattr(checkers.ssl, "create_default_context", ssl.create_default_context)

        async def scenario():
            server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server_ctx.load_cert_chain(str(pki["valid"][0]))
            server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0, ssl=server_ctx)
            port = server.sockets[0].getsockname()[1]
            try:
                return await checkers.run_check(monitor(type="ssl", target="localhost", port=port, ignore_tls=True))
            finally:
                server.close()

        r = asyncio.run(scenario())
        assert r.ok and abs((r.cert_expires_at - pki["valid"][1]).total_seconds()) < 2


def test_unexpected_errors_never_escape(monkeypatch):
    async def broken(m):
        raise RuntimeError("boom")

    monkeypatch.setitem(checkers.CHECKERS, "ping", broken)
    r = check(monitor(type="ping", target="x"))
    assert not r.ok and r.error_class == "error" and "boom" in r.message
