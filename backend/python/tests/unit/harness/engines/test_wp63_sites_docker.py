"""WP-63 on the real lane (runc): an engine image's sandbox reaches allowed websites only through the real egress
proxy, which ends their TLS with its authority and checks every request.

A ``pxh-`` https site container on the rig bridge stands for the outside world (names from ``EGRESS_TEST_HOSTS``, a
test authority the proxy verifies it with, ``EGRESS_PRIVATE_ALLOW`` opening its private address as a company's
internal site would be). Nothing leaves the host and no real site is touched. From inside the sandbox, with the
engine shell's own environment (``site_env``) and trust bundle:

- a read of an allowed path works, and the sandbox's planted ``Authorization`` and cookie never reach the site;
- a POST, a path outside the entry's limits, and git's smart-HTTP are refused at a read-only site;
- a read-write site takes a POST;
- a site not on the list, and a listed site that resolves to a private address, are refused at the ``CONNECT``;
- a request naming another host than its connection is 421, and a TLS name other than the ``CONNECT``'s fails the
  handshake (no fronting);
- the proxy's memory and per-request time stay laptop-sized (measured, printed, bounded).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import uuid
from typing import TYPE_CHECKING

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from harness_core.engines.controller import engine_mode
from harness_core.engines.sites import install_site_trust, site_env
from harness_core.sandbox.types import (
    EgressDestination,
    EgressPolicy,
    LeaseRef,
    MountSpec,
    SandboxSpec,
    SiteAccess,
    SiteMode,
)
from tests.unit.harness.engines.real_stack import (
    HELPER_IMAGE,
    IMAGE,
    OWNER,
    engine_broker,
    lane_available,
    start_stack,
    stop_stack,
)
from tests.unit.harness.sandbox_broker.conftest import cleanup, cleanup_networks

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from harness_core.sandbox.ports import SandboxHandle
    from tests.unit.harness.engines.real_stack import RealStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600, method="thread")]

DOCS, RW, OTHER, INTERNAL = "docs.pxh-site.test", "rw.pxh-site.test", "other.pxh-site.test", "internal.pxh-site.test"
SITE_SERVER = r"""
import http.server, json, os, ssl
open('/tmp/c.pem', 'w').write(os.environ['SITE_CERT']); open('/tmp/k.pem', 'w').write(os.environ['SITE_KEY'])
class H(http.server.BaseHTTPRequestHandler):
    def _answer(self):
        size = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(size) if size else b''
        out = json.dumps({'method': self.command, 'path': self.path, 'headers': dict(self.headers), 'body': len(body)}).encode()
        self.send_response(200); self.send_header('Content-Length', str(len(out))); self.end_headers(); self.wfile.write(out)
    do_GET = do_POST = do_PUT = do_HEAD = do_OPTIONS = _answer
    def log_message(self, *a): pass
server = http.server.ThreadingHTTPServer(('0.0.0.0', 443), H)
context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH); context.load_cert_chain('/tmp/c.pem', '/tmp/k.pem')
server.socket = context.wrap_socket(server.socket, server_side=True)
server.serve_forever()
"""
PROBE = r"""
import json, os, socket, ssl, sys, time
BUNDLE = os.environ['SSL_CERT_FILE']
PROXY = os.environ['HTTPS_PROXY'].split('//', 1)[1].rstrip('/').rsplit(':', 1)
def call(target, method='GET', path='/', headers=(), body=b'', sni=None, host=None):
    s = socket.create_connection((PROXY[0], int(PROXY[1])), timeout=20)
    s.sendall(f'CONNECT {target}:443 HTTP/1.1\r\nHost: {target}:443\r\n\r\n'.encode())
    head = b''
    while b'\r\n\r\n' not in head:
        chunk = s.recv(4096)
        if not chunk: break
        head += chunk
    status = head.split(b' ', 2)[1].decode() if head else 'closed'
    if status != '200':
        return {'connect': status, 'head': head.decode('latin-1')}
    ctx = ssl.create_default_context(cafile=BUNDLE)
    if sni is not None:
        ctx.check_hostname = False
    try:
        t = ctx.wrap_socket(s, server_hostname=sni or target)
    except Exception as exc:
        return {'tls': type(exc).__name__}
    hdrs = {'Host': host or target, 'Connection': 'close', **dict(headers)}
    if body:
        hdrs['Content-Length'] = str(len(body))
    t.sendall((f'{method} {path} HTTP/1.1\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in hdrs.items()) + '\r\n').encode() + body)
    data = b''
    while True:
        try:
            chunk = t.recv(65536)
        except Exception:
            break
        if not chunk:
            break
        data += chunk
    head, _, rest = data.partition(b'\r\n\r\n')
    lines = head.decode('latin-1').split('\r\n')
    refusal = next((l.split(':', 1)[1].strip() for l in lines if l.lower().startswith('x-egress-refusal:')), None)
    return {'status': lines[0].split(' ')[1] if lines and ' ' in lines[0] else 'closed', 'refusal': refusal, 'body': rest.decode('latin-1')}
planted = (('Authorization', 'token ghp_attacker0000000000000000000000000000'), ('Cookie', 'session=attacker'))
out = {
    'read': call('%(docs)s', path='/api/v2?q=1', headers=planted),
    'post': call('%(docs)s', 'POST', '/api/v2', body=b'secret'),
    'path': call('%(docs)s', path='/blog'),
    'git': call('%(docs)s', path='/api/r.git/info/refs?service=git-upload-pack'),
    'rw_post': call('%(rw)s', 'POST', '/upload', body=b'artifact'),
    'other': call('%(other)s', path='/'),
    'internal': call('%(internal)s', path='/api/x'),
    'host_mismatch': call('%(docs)s', path='/api/v2', host='%(other)s'),
    'sni_mismatch': call('%(docs)s', path='/api/v2', sni='%(other)s'),
}
started = time.monotonic()
for _ in range(int(os.environ.get('PROBE_REPEAT', '0'))):
    call('%(docs)s', path='/api/v2')
out['repeat_s'] = time.monotonic() - started
print(json.dumps(out))
""" % {"docs": DOCS, "rw": RW, "other": OTHER, "internal": INTERNAL}
REPEAT = 100


# SEC-A-01: a rig stand-in for a release registry (pypi.org), served by the rig's own site, never the real one.
REGISTRY = "pypi.org"


def _site_pki() -> tuple[str, str, str]:
    """A test authority and one certificate for every rig name: (ca, cert, key) PEMs."""
    now = dt.datetime.now(dt.UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "pxh wp63 test authority")])
    ca = (
        x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(hours=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, DOCS)]))
        .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(hours=1)).not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(h) for h in (DOCS, RW, OTHER, INTERNAL, REGISTRY)]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    pem = serialization.Encoding.PEM
    key_pem = key.private_bytes(pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return ca.public_bytes(pem).decode(), cert.public_bytes(pem).decode(), key_pem.decode()


@pytest.fixture(scope="module")
def site_stack() -> Iterator[RealStack]:
    available, reason = lane_available()
    if not available:
        pytest.skip(reason)
    ca, cert, key = _site_pki()
    site = f"pxh-wp63-site-{uuid.uuid4().hex[:8]}"

    def upstreams(stack: RealStack) -> dict[str, str]:
        host_config = stack.client.api.create_host_config(
            network_mode=stack.network, cap_drop=["ALL"], cap_add=["NET_BIND_SERVICE"],
            security_opt=["no-new-privileges:true"], read_only=True, tmpfs={"/tmp": "size=4m"},
            mem_limit=128 * 1024 * 1024,
        )
        created = stack.client.api.create_container(
            HELPER_IMAGE, command=["python", "-c", SITE_SERVER], name=site, labels=OWNER,
            environment={"SITE_CERT": cert, "SITE_KEY": key}, host_config=host_config,
        )
        stack.client.api.start(created["Id"])
        address = stack.client.api.inspect_container(site)["NetworkSettings"]["Networks"][stack.network]["IPAddress"]
        return {
            "EGRESS_TEST_INTERCEPT": "1",
            "EGRESS_TEST_UPSTREAM_CA": ca,
            "EGRESS_TEST_HOSTS": json.dumps({DOCS: [address], RW: [address], OTHER: [address], INTERNAL: ["10.215.254.254"]}),
            # The site's address is private on the rig, as a company's internal docs site is: the deployment opens
            # these names; INTERNAL stays refused.
            "EGRESS_PRIVATE_ALLOW": f"{DOCS}:443,{RW}:443,{OTHER}:443",
        }

    stack = start_stack(upstreams=upstreams)
    try:
        yield stack
    finally:
        stop_stack(stack)
        with contextlib.suppress(Exception):
            stack.client.api.remove_container(site, force=True)


@pytest.fixture
async def broker(site_stack: RealStack) -> AsyncIterator[DockerSandboxBroker]:
    namespace = f"io.pipeshub.wp63.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-wp63-" + namespace.rsplit(".", 1)[1]
    created, provider = await engine_broker(site_stack, namespace, prefix)
    yield created
    await created.aclose()
    await provider.run_blocking(cleanup, provider, namespace, prefix)
    await provider.run_blocking(cleanup_networks, provider, namespace, site_stack.proxy)
    provider.close()


async def _probe(sandbox: SandboxHandle, repeat: int = 0) -> dict[str, object]:
    process = await sandbox.exec(
        ["python3", "-c", PROBE], env={"HOME": "/tmp", **site_env(), "PROBE_REPEAT": str(repeat)}
    )
    out = b"".join([chunk async for chunk in process.stdout])
    err = b"".join([chunk async for chunk in process.stderr])
    code = await process.wait(timeout_s=300)
    assert code == 0, err.decode("utf-8", "replace")
    return json.loads(out.decode().strip().splitlines()[-1])


def _json(body: object) -> dict[str, object]:
    """The site's JSON answer out of the proxy's chunked body."""
    text = str(body)
    return json.loads(text[text.index("{") : text.rindex("}") + 1])


def _proxy_memory_mib(stack: RealStack) -> float:
    stats = stack.client.api.stats(stack.proxy, stream=False)
    return stats["memory_stats"]["usage"] / 1024 / 1024


async def test_c6_11_sec_eg_07_an_engine_sandbox_reaches_allowed_sites_only_as_listed(
    broker: DockerSandboxBroker, site_stack: RealStack
) -> None:
    mode = engine_mode(broker.modes())
    assert mode is not None
    egress = EgressPolicy(
        destinations=(
            EgressDestination(DOCS, (443,), site=SiteAccess(SiteMode.READ, paths=("/api/",))),
            EgressDestination(RW, (443,), site=SiteAccess(SiteMode.READ_WRITE)),
            EgressDestination(INTERNAL, (443,), site=SiteAccess(SiteMode.READ)),
        )
    )
    spec = SandboxSpec(image_digest=IMAGE, mode=mode, mounts=(MountSpec("/workspace"),), egress=egress, labels={"pxh.test": "wp63"})
    sandbox = await broker.acquire(spec, lease=LeaseRef("worker-1", 1), idempotency_key=f"wp63-{uuid.uuid4().hex[:8]}")
    try:
        await install_site_trust(sandbox)
        before = _proxy_memory_mib(site_stack)
        found = await _probe(sandbox, repeat=REPEAT)
        after = _proxy_memory_mib(site_stack)

        read = found["read"]
        assert isinstance(read, dict) and read["status"] == "200", found
        seen = _json(read["body"])
        assert seen["path"] == "/api/v2?q=1"
        assert "Authorization" not in seen["headers"] and "Cookie" not in seen["headers"], seen

        for name, reason in (("post", "site_read_only"), ("path", "site_path_not_allowed"), ("git", "site_git_refused")):
            result = found[name]
            assert isinstance(result, dict) and result["status"] == "403" and result["refusal"] == reason, (name, found)

        rw = found["rw_post"]
        assert isinstance(rw, dict) and rw["status"] == "200" and _json(rw["body"])["body"] == len(b"artifact")

        other, internal = found["other"], found["internal"]
        # Not a listed site: refused at the CONNECT (tunnel_refused where the deployment's tunnel switch is off).
        assert isinstance(other, dict) and other["connect"] == "403", found
        assert "destination_not_allowed" in other["head"] or "tunnel_refused" in other["head"], found
        # An inspected site is resolved when its first request is forwarded: refused there, never dialled.
        assert isinstance(internal, dict) and internal.get("status") == "403", found
        assert internal["refusal"] == "destination_private_address", found

        host_mismatch, sni_mismatch = found["host_mismatch"], found["sni_mismatch"]
        assert isinstance(host_mismatch, dict) and host_mismatch["status"] == "421", found
        assert isinstance(sni_mismatch, dict) and "tls" in sni_mismatch, found

        per_request_ms = float(found["repeat_s"]) / REPEAT * 1000  # type: ignore[arg-type]
        grown = after - before
        print(f"wp63 footprint: proxy {before:.1f} -> {after:.1f} MiB after {REPEAT} inspected requests; {per_request_ms:.1f} ms each")
        assert grown < 64, f"the proxy grew {grown:.1f} MiB over {REPEAT} inspected requests"
        assert per_request_ms < 250, per_request_ms
    finally:
        await broker.release(sandbox)
