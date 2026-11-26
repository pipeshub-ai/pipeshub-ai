"""SP-1..SP-7 (SIGNIN-1) live: the admitted Claude Code binary against the fake Anthropic, offline.

Skipped without a Docker daemon or without the admin-built tools image
(``deployment/sandbox-images/claude-code/build.sh --binary PATH``). Containers and the network are labelled
``pipeshub.harness.owner=signin`` and removed afterwards.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any

import pytest

from tests.unit.harness.engines.lane_releases import lane_release
from tests.unit.harness_engines.claude_code.signin_lab.conformance import CHECKS, Check
from tests.unit.harness_engines.claude_code.signin_lab.lab import scenario
from tests.unit.harness_engines.claude_code.signin_lab.record import reduce

pytestmark = [pytest.mark.docker, pytest.mark.timeout(900, method="thread")]

IMAGE = lane_release("claude_code").image


@pytest.fixture(scope="module")
def live() -> dict[str, Any]:
    if shutil.which("docker") is None:
        pytest.skip("no docker CLI")
    found = subprocess.run(
        ["docker", "image", "inspect", IMAGE], capture_output=True, check=False
    )
    if found.returncode != 0:
        pytest.skip(
            f"{IMAGE} is not built here (deployment/sandbox-images/claude-code/build.sh --binary PATH)"
        )
    return reduce(scenario(IMAGE))


@pytest.mark.parametrize("check", CHECKS, ids=lambda check: check.__name__)
def test_live_login_flow(live: dict[str, Any], check: Check) -> None:
    check(live)
