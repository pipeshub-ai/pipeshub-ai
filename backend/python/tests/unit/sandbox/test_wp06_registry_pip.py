"""SR-17 (WP-06, OWD-1): a real pip installs from a credentialed registry through the egress proxy, holding only a
placeholder.

pip runs as the install sandbox runs it (``coding.credentials.proxied_registry``): its index URL carries a
placeholder as the Basic password, ``HTTPS_PROXY`` points at the proxy, and ``PIP_CERT`` at a bundle holding the
proxy's authority. The proxy ends the TLS, swaps in the operator's credential and forwards to the registry (here a
local index that insists on that credential). Nothing pip is given, and nothing it logs, holds the password.
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import subprocess
import sys
import zipfile
from typing import TYPE_CHECKING

import pytest
from aiohttp import web

from app.agent_loop_lib.sandbox.coding.credentials import proxied_registry, registry
from harness_core.clock import SystemClock
from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress import (
    EgressBinding,
    EgressProxy,
    InMemoryEgressBindings,
    InMemoryTokenSource,
    ProxyLimits,
    StaticResolver,
)
from harness_core.sandbox.egress.interception import EgressAuthority
from harness_core.sandbox.egress.sealed import (
    CredentialSealer,
    RoutedTokenSource,
    SealedTokenSource,
)
from harness_core.sandbox.egress.server import EgressServer
from harness_core.sandbox.types import EgressDestination, EgressPolicy
from harness_core.testing.ids import SeededIds

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.timeout(180, method="thread")

HOST = "pypi.corp.example"
USER, PASSWORD = "ci-bot", "Sr17-registry-pass-0123456789"
SEALER = CredentialSealer(b"sr17-deployment-key")


def _wheel() -> tuple[str, bytes]:
    name = "pxh_sr17_demo-1.0-py3-none-any.whl"
    files = {
        "pxh_sr17_demo/__init__.py": b"VALUE = 17\n",
        "pxh_sr17_demo-1.0.dist-info/METADATA": b"Metadata-Version: 2.1\nName: pxh-sr17-demo\nVersion: 1.0\n",
        "pxh_sr17_demo-1.0.dist-info/WHEEL": b"Wheel-Version: 1.0\nGenerator: sr17\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    record = "".join(
        f"{path},sha256={base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()},{len(data)}\n"
        for path, data in files.items()
    ) + "pxh_sr17_demo-1.0.dist-info/RECORD,,\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, data in files.items():
            archive.writestr(path, data)
        archive.writestr("pxh_sr17_demo-1.0.dist-info/RECORD", record)
    return name, buffer.getvalue()


async def test_sr17_pip_installs_from_a_credentialed_registry_through_the_proxy(tmp_path: Path) -> None:
    wheel_name, wheel = _wheel()
    expected = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
    seen: list[str] = []

    async def index(request: web.Request) -> web.StreamResponse:
        seen.append(request.headers.get("Authorization", ""))
        if request.headers.get("Authorization") != expected:
            return web.Response(status=401, headers={"WWW-Authenticate": 'Basic realm="corp"'})
        if request.path.rstrip("/") == "/simple/pxh-sr17-demo":
            body = f'<html><body><a href="/files/{wheel_name}">{wheel_name}</a></body></html>'
            return web.Response(text=body, content_type="text/html")
        if request.path == f"/files/{wheel_name}":
            return web.Response(body=wheel, content_type="application/octet-stream")
        return web.Response(status=404)

    app = web.Application()
    _ = app.router.add_get("/{tail:.*}", index)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    upstream_port = runner.addresses[0][1]

    found = registry(f"https://{USER}:{PASSWORD}@{HOST}/simple")
    proxied = proxied_registry(found, SEALER, "pxh-ph-registry-sr17-0123456789")
    # The registry stands on loopback here: the route's destination is pointed at it, as a deployment's trusted
    # override would; everything else is the install's own allowlist and route.
    (destination,) = proxied.egress.destinations
    egress = EgressPolicy(
        destinations=(EgressDestination(destination.host, destination.ports, upstream=f"http://127.0.0.1:{upstream_port}"),),
        token_routes=proxied.egress.token_routes,
    )
    clock = SystemClock()
    proxy = EgressProxy(
        bindings=InMemoryEgressBindings(),
        tokens=RoutedTokenSource(InMemoryTokenSource(b"t", clock), SealedTokenSource(SEALER, clock)),
        resolver=StaticResolver({}),
        upstreams={},
        clock=clock,
        ids=SeededIds(),
        limits=ProxyLimits(intercepts=True),
    )
    await proxy.bindings.register(EgressBinding("sbx-sr17", "127.0.0.2", "127.0.0.1", egress))
    authority = EgressAuthority.generate(clock)
    server = EgressServer(proxy, StaticBearerAuth("c"), instance="sr17", authority=authority)
    data_port, _ = await server.start(host="127.0.0.2", data_port=0, control_port=0, control_host="127.0.0.1")
    bundle = tmp_path / "ca-bundle.pem"
    bundle.write_bytes(authority.certificate_pem)
    target = tmp_path / "deps"
    env = {
        **{k: v for k, v in proxied.env.items() if k != "PXH_EGRESS_TRUST"},
        "HTTPS_PROXY": f"http://127.0.0.2:{data_port}",
        "https_proxy": f"http://127.0.0.2:{data_port}",
        "HTTP_PROXY": f"http://127.0.0.2:{data_port}",
        "http_proxy": f"http://127.0.0.2:{data_port}",
        "PIP_CERT": str(bundle),
        "SSL_CERT_FILE": str(bundle),
        "REQUESTS_CA_BUNDLE": str(bundle),
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path),
    }
    command = [
        sys.executable, "-m", "pip", "install", "--no-deps", "--no-cache-dir", "--disable-pip-version-check",
        "--target", str(target), "--index-url", proxied.pip_url(), "pxh-sr17-demo",
    ]
    try:
        import asyncio

        done = await asyncio.to_thread(subprocess.run, command, env=env, capture_output=True, text=True, timeout=120, check=False)
    finally:
        await server.stop()
        await runner.cleanup()
    assert done.returncode == 0, done.stdout + done.stderr
    assert (target / "pxh_sr17_demo" / "__init__.py").read_text() == "VALUE = 17\n"
    assert seen and all(value == expected for value in seen)
    shown = " ".join(command) + repr(env) + done.stdout + done.stderr
    assert PASSWORD not in shown and base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode() not in shown
