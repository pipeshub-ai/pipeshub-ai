"""Acquires a sandbox through the compose broker with an allowlist and calls the gateway through the proxy (dev-only smoke).

Runs where the app container would: on sandbox_plane, with the worker credential.
SM-2: the sandbox reaches model-gateway with its placeholder; SM-3: it reaches
nothing directly, and the proxy refuses a host that is not on its allowlist.
"""

import asyncio
import json
import os
import sys
import time
import uuid

sys.path[:0] = ["/app/python"]
sys.path.append("/site")

from app.harness.placement import deployment_region  # noqa: E402
from harness_core.sandbox.auth import StaticBearerAuth  # noqa: E402
from harness_core.sandbox.remote.client import RemoteSandboxBroker  # noqa: E402
from harness_core.sandbox.types import (  # noqa: E402
    EgressDestination,
    EgressPolicy,
    LeaseRef,
    RuntimeMode,
    SandboxSpec,
    TokenRoute,
)

PLACEHOLDER = "ph-d1-smoke-" + uuid.uuid4().hex[:16]
GATEWAY_WAIT_S = 75.0

CALL = r"""
import sys, urllib.request, urllib.error
url, bearer, proxy = sys.argv[1], sys.argv[2], sys.argv[3]
handlers = [urllib.request.ProxyHandler({"http": proxy} if proxy else {})]
opener = urllib.request.build_opener(*handlers)
req = urllib.request.Request(url, headers={"Authorization": "Bearer " + bearer})
try:
    r = opener.open(req, timeout=15)
    print(r.status, "-", r.read().decode())
except urllib.error.HTTPError as e:
    print(e.code, e.headers.get("X-Egress-Refusal"), e.read().decode())
except Exception as e:
    print(0, type(e).__name__, "")
"""
DIRECT = r"""
import socket, sys
s = socket.socket(); s.settimeout(3)
try:
    s.connect((sys.argv[1], int(sys.argv[2]))); print("connected")
except OSError as e:
    print("blocked", type(e).__name__)
"""


async def run(handle, argv):
    process = await handle.exec(argv, env={"HOME": "/tmp"})

    async def collect(stream):
        return b"".join([chunk async for chunk in stream]).decode()

    out, err = await asyncio.gather(collect(process.stdout), collect(process.stderr))
    return out.strip(), err.strip(), await process.wait(timeout_s=30)


def check(results, name, ok, detail):
    results.append({"case": name, "ok": bool(ok), "detail": detail})
    print(json.dumps(results[-1]), flush=True)


async def main() -> int:
    broker = await RemoteSandboxBroker.open(
        "http://sandbox-broker:8095",
        StaticBearerAuth(os.environ["SMOKE_WORKER_CREDENTIAL"]),
        # OWD-12: the broker places sandboxes of its own region only, as the app's client names it.
        region=deployment_region(),
    )
    spec = SandboxSpec(
        image_digest=os.environ["SANDBOX_DOCKER_IMAGE"],
        mode=RuntimeMode.DOCKER_HARDENED,
        egress=EgressPolicy(
            destinations=(EgressDestination("model-gateway", (8097,)),),
            token_routes=(TokenRoute(PLACEHOLDER, "model-gateway", "gateway:model"),),
        ),
        # The proxy asks Node for this attempt's gateway token.
        labels={"pipeshub.harness.owner": "x-lead", "smoke": "pxh-d1", "orgId": "org-smoke", "runId": "run-smoke", "attemptId": "att-smoke"},
    )
    results: list[dict[str, object]] = []
    handle = await broker.acquire(spec, lease=LeaseRef("pxh-d1-smoke", 1), idempotency_key=f"pxh-d1-{uuid.uuid4()}")
    print(json.dumps({"sandboxId": handle.id, "mode": str(handle.mode)}), flush=True)
    try:
        # The proxy re-reads the gateway endpoints Node (here: the seed job) wrote after it started.
        deadline = time.monotonic() + GATEWAY_WAIT_S
        while True:
            out, err, _ = await run(handle, ["python3", "-c", CALL, "http://model-gateway:8097/v1/models", PLACEHOLDER, ""])
            if out.startswith("200 ") or time.monotonic() > deadline:
                break
            await asyncio.sleep(3)
        body = out.split(" ", 2)[2] if out.count(" ") >= 2 else ""
        check(results, "SM-2 gateway through the proxy", out.startswith("200 "), out[:200] or err[:200])
        check(
            results,
            "SM-2 the sandbox sees only its placeholder",
            f"Bearer {PLACEHOLDER}" in body and "pxt_" not in body,
            body[:200],
        )
        out, _, _ = await run(handle, ["python3", "-c", DIRECT, "1.1.1.1", "80"])
        check(results, "SM-3 no direct route (1.1.1.1:80)", out.startswith("blocked"), out)
        # E2E4: the proxy's control plane listens only on the Compose networks; its data port is the positive control.
        out, _, _ = await run(handle, ["python3", "-c", DIRECT, "egress-proxy", "8098"])
        check(results, "SM-3 no path to the proxy's control port (egress-proxy:8098)", out.startswith("blocked"), out)
        out, _, _ = await run(handle, ["python3", "-c", DIRECT, "egress-proxy", "8097"])
        check(results, "SM-3 the proxy's data port answers (egress-proxy:8097)", out == "connected", out)
        out, _, _ = await run(
            handle,
            ["python3", "-c", CALL, "http://not-allowed.example:8097/x", PLACEHOLDER, "http://egress-proxy:8097"],
        )
        check(results, "SM-3 a host off the allowlist is refused", out.startswith("403 destination_not_allowed"), out[:200])
    finally:
        await broker.release(handle)
        await broker.aclose()
    return 0 if all(r["ok"] for r in results) else 1


sys.exit(asyncio.run(main()))
