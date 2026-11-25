"""SITEUP-07 on the real lane (runc, WP-63 upload card): a read-write website held to read-only after a private read,
in an engine image's sandbox behind the real egress proxy container. Its reads go through; a write is refused
``site_upload_held`` and the site never sees it; the worker's grant at the proxy's control plane
(``POST /v1/site-grants``) for that sandbox, host and run lets the next write through; a grant for another run is
refused, and once the sandbox is bound again the hold is back. The rig is the WP-63 site rig (a ``pxh-`` https site
container on the rig bridge, ``EGRESS_TEST_HOSTS``, the test authority).
"""

from __future__ import annotations

import contextlib
import json
import uuid
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from harness_core.engines.controller import engine_mode
from harness_core.engines.sites import install_site_trust, site_env
from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress.client import RemoteEgressBindings
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
from tests.unit.harness.engines.test_wp63_sites_docker import (
    PROBE,
    REGISTRY,
    RW,
    SITE_SERVER,
    _json,
    _site_pki,
)
from tests.unit.harness.sandbox_broker.conftest import cleanup, cleanup_networks

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from harness_core.sandbox.ports import SandboxHandle
    from tests.unit.harness.engines.real_stack import RealStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600, method="thread")]

# The site rig's probe helper (CONNECT, TLS through the proxy's authority, one request), then one call per run.
CALL = PROBE.split("out = {", 1)[0] + r"""
import sys
method, path = sys.argv[1], sys.argv[2]
host = sys.argv[3] if len(sys.argv) > 3 else '%(rw)s'
print(json.dumps(call(host, method, path, body=b'artifact' if method == 'POST' else b'')))
""" % {"rw": RW}


@pytest.fixture(scope="module")
def site_stack() -> Iterator[RealStack]:
    available, reason = lane_available()
    if not available:
        pytest.skip(reason)
    ca, cert, key = _site_pki()
    site = f"pxh-siteup-{uuid.uuid4().hex[:8]}"

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
            "EGRESS_TEST_HOSTS": json.dumps({RW: [address], REGISTRY: [address]}),
            "EGRESS_PRIVATE_ALLOW": f"{RW}:443,{REGISTRY}:443",
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
    namespace = f"io.pipeshub.siteup.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-siteup-" + namespace.rsplit(".", 1)[1]
    created, provider = await engine_broker(site_stack, namespace, prefix)
    yield created
    await created.aclose()
    await provider.run_blocking(cleanup, provider, namespace, prefix)
    await provider.run_blocking(cleanup_networks, provider, namespace, site_stack.proxy)
    provider.close()


async def _call(sandbox: SandboxHandle, method: str, path: str = "/upload", host: str = RW) -> dict[str, object]:
    process = await sandbox.exec(["python3", "-c", CALL, method, path, host], env={"HOME": "/tmp", **site_env()})
    out = b"".join([chunk async for chunk in process.stdout])
    err = b"".join([chunk async for chunk in process.stderr])
    assert await process.wait(timeout_s=120) == 0, err.decode("utf-8", "replace")
    return json.loads(out.decode().strip().splitlines()[-1])


async def test_siteup07_a_held_site_reads_refuses_writes_until_the_run_is_granted_then_writes(
    broker: DockerSandboxBroker, site_stack: RealStack
) -> None:
    mode = engine_mode(broker.modes())
    assert mode is not None
    egress = EgressPolicy(destinations=(EgressDestination(RW, (443,), site=SiteAccess(SiteMode.READ_WRITE, held=True)),))
    spec = SandboxSpec(
        image_digest=IMAGE, mode=mode, mounts=(MountSpec("/workspace"),), egress=egress,
        labels={"pxh.test": "siteup", "runId": "run-siteup", "orgId": "o1"},
    )
    lease, key = LeaseRef("worker-1", 1), f"siteup-{uuid.uuid4().hex[:8]}"
    sandbox = await broker.acquire(spec, lease=lease, idempotency_key=key)
    control = RemoteEgressBindings(site_stack.control_url, StaticBearerAuth(site_stack.control_token))
    try:
        await install_site_trust(sandbox)
        # SEC-A-01: a held site's reads wait as its writes do: a URL carries data as a body does.
        read = await _call(sandbox, "GET", "/upload?d=canary")
        assert (read.get("status"), read.get("refusal")) == ("403", "site_request_held"), read
        held = await _call(sandbox, "POST")
        assert (held.get("status"), held.get("refusal")) == ("403", "site_upload_held"), held
        with pytest.raises(Exception):  # noqa: B017 - the proxy refuses a grant for another run
            await control.grant_site(sandbox_id=sandbox.id, host=RW, run_id="another-run")
        assert (await _call(sandbox, "POST")).get("refusal") == "site_upload_held"
        await control.grant_site(sandbox_id=sandbox.id, host=RW, run_id="run-siteup")
        allowed = await _call(sandbox, "POST")
        assert allowed.get("status") == "200", allowed
        assert _json(allowed["body"])["method"] == "POST"
        assert (await _call(sandbox, "GET", "/upload?d=canary")).get("status") == "200"
        # The same run bound again keeps its grant; bound for another run, the grant went with the binding.
        same_run = await broker.acquire(spec, lease=LeaseRef("worker-1", 2), idempotency_key=key)
        assert same_run.id == sandbox.id and (await _call(sandbox, "POST")).get("status") == "200"
        next_run = replace(spec, labels={**spec.labels, "runId": "run-next"})
        rebound = await broker.acquire(next_run, lease=LeaseRef("worker-1", 3), idempotency_key=key)
        assert rebound.id == sandbox.id
        again = await _call(sandbox, "POST")
        assert again.get("refusal") == "site_upload_held", again
    finally:
        await control.aclose()
        await broker.release(sandbox)


async def test_sec_a_01_02_a_private_read_mid_turn_holds_the_live_sandbox_and_package_installs_keep_working(
    broker: DockerSandboxBroker, site_stack: RealStack
) -> None:
    """The lane half of SEC-A-01/02 on the real proxy container: a sandbox bound before any private read reaches its
    sites; the worker's live hold (``POST /v1/flow-holds``, what ``LiveFlowHold`` sends after the gateway call that
    read private data) holds every request that could carry it at once, in the same sandbox and turn, while a
    registry's own files still answer an install; the person's grant releases the site."""
    mode = engine_mode(broker.modes())
    assert mode is not None
    egress = EgressPolicy(destinations=(
        EgressDestination(RW, (443,), site=SiteAccess(SiteMode.READ_WRITE)),
        EgressDestination(REGISTRY, (443,), site=SiteAccess(SiteMode.READ)),
    ))
    spec = SandboxSpec(
        image_digest=IMAGE, mode=mode, mounts=(MountSpec("/workspace"),), egress=egress,
        labels={"pxh.test": "seca", "runId": "run-seca", "orgId": "o1"},
    )
    sandbox = await broker.acquire(spec, lease=LeaseRef("worker-1", 1), idempotency_key=f"seca-{uuid.uuid4().hex[:8]}")
    control = RemoteEgressBindings(site_stack.control_url, StaticBearerAuth(site_stack.control_token))
    try:
        await install_site_trust(sandbox)
        assert (await _call(sandbox, "GET", "/before")).get("status") == "200"
        assert (await _call(sandbox, "GET", "/simple/requests/", REGISTRY)).get("status") == "200"
        with pytest.raises(Exception):  # noqa: B017 - the proxy holds only the run the sandbox serves
            await control.hold_flow(sandbox_id=sandbox.id, run_id="another-run", hold="held")
        await control.hold_flow(sandbox_id=sandbox.id, run_id="run-seca", hold="held")
        read = await _call(sandbox, "GET", "/exfil?d=7365636120636172792d37663365")
        assert (read.get("status"), read.get("refusal")) == ("403", "site_request_held"), read
        upload = await _call(sandbox, "POST")
        assert (upload.get("status"), upload.get("refusal")) == ("403", "site_upload_held"), upload
        # A package install after the private read: the registry's own path answers, a query string waits.
        install = await _call(sandbox, "GET", "/simple/requests/", REGISTRY)
        assert install.get("status") == "200", install
        search = await _call(sandbox, "GET", "/search?q=7365636120636172792d37663365", REGISTRY)
        assert search.get("refusal") == "site_request_held", search
        await control.grant_site(sandbox_id=sandbox.id, host=RW, run_id="run-seca")
        assert (await _call(sandbox, "POST")).get("status") == "200"
    finally:
        await control.aclose()
        await broker.release(sandbox)
