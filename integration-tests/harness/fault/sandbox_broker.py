"""The sandbox broker's HTTP app over the fake sandbox, as its own process (QA-17).

    python sandbox_broker.py <port> <token>

``create_broker_app`` is the broker's production wire; only the sandboxes behind it are in memory. The degraded
scenarios kill this process while a run is in flight.
"""

from __future__ import annotations

import sys

from harness_core.clock import SystemClock
from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.remote.server import create_broker_app
from harness_core.testing.sandbox import FakeSandboxBroker

from serve import serve


def main() -> None:
    port, token = int(sys.argv[1]), sys.argv[2]
    serve(create_broker_app(FakeSandboxBroker(clock=SystemClock()), StaticBearerAuth(token)), port, "SANDBOX BROKER READY")


if __name__ == "__main__":
    main()
