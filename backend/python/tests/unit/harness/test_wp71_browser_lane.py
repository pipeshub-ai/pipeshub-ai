"""WP-71: the browser lane's JUnit check (``deployment/e2e/run.sh``) and its place in the repo's verify flow.

The harness project keeps the real-service rule (a skip, or no test, fails the lane: a lane that skips reads green
while testing nothing). The authenticated project may skip a spec for a reason about the stack (no SMTP, no indexing,
an Enterprise build), and each skip is listed with its reason; a skip that gives none fails it. The function is cut out
of run.sh and run by bash, so the test reads the code the lane runs.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[5]
RUN_SH = REPO / "deployment" / "e2e" / "run.sh"
VERIFY_SH = REPO / "scripts" / "verify.sh"


def _check_junit(tmp_path: Path, xml: str, mode: str | None) -> subprocess.CompletedProcess[str]:
    script = RUN_SH.read_text()
    start = script.index("check_junit() {")
    end = script.index("\n}\n", start) + 3
    (tmp_path / "pw").mkdir(exist_ok=True)
    (tmp_path / "pw" / "junit-t.xml").write_text(xml)
    call = f"check_junit t {mode}" if mode else "check_junit t"
    return subprocess.run(
        ["bash", "-c", f"{script[start:end]}\nWORK={tmp_path}\n{call}"],
        capture_output=True,
        text=True,
        timeout=60,
    )


def _suite(*cases: str) -> str:
    skipped = sum("<skipped" in case for case in cases)
    return f'<testsuites><testsuite name="s" tests="{len(cases)}" skipped="{skipped}">{"".join(cases)}</testsuite></testsuites>'


PASSED = '<testcase name="a passes"></testcase>'
SKIPPED_WITH_REASON = (
    '<testcase name="needs SMTP"><properties><property name="skip" value="SMTP is not configured"/></properties>'
    "<skipped/></testcase>"
)
SKIPPED_WITHOUT_REASON = '<testcase name="silent"><skipped/></testcase>'


def test_wp71_lane_1_the_harness_project_still_fails_on_any_skip(tmp_path: Path) -> None:
    result = _check_junit(tmp_path, _suite(PASSED, SKIPPED_WITH_REASON), None)
    assert result.returncode != 0
    assert "skipped on a real-service lane" in result.stderr


def test_wp71_lane_2_the_authenticated_project_lists_each_stated_skip_and_passes(tmp_path: Path) -> None:
    result = _check_junit(tmp_path, _suite(PASSED, SKIPPED_WITH_REASON), "listed")
    assert result.returncode == 0, result.stderr
    assert "E2E: skipped: needs SMTP: SMTP is not configured" in result.stdout
    assert "2 tests, 1 skipped" in result.stdout


def test_wp71_lane_3_a_skip_without_a_reason_fails_the_authenticated_project(tmp_path: Path) -> None:
    result = _check_junit(tmp_path, _suite(PASSED, SKIPPED_WITHOUT_REASON), "listed")
    assert result.returncode != 0
    assert "give no reason" in result.stderr


@pytest.mark.parametrize("mode", [None, "listed"])
def test_wp71_lane_4_no_test_at_all_fails_either_project(tmp_path: Path, mode: str | None) -> None:
    assert _check_junit(tmp_path, "<testsuites></testsuites>", mode).returncode != 0


def test_wp71_lane_5_the_verify_flow_runs_the_browser_lane_only_when_opted_in() -> None:
    listed = subprocess.run(
        ["bash", str(VERIFY_SH), "--list", "e2e"],
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"},
    )
    assert listed.returncode == 0, listed.stderr
    assert "opt-in: set PXH_VERIFY_E2E=1" in listed.stdout
    assert "bash deployment/e2e/run.sh browser" in VERIFY_SH.read_text()


def _assign_subnets(tmp_path: Path, names: list[str], taken: list[str]) -> str:
    """Runs run.sh's `assign_subnets` with Compose and Docker stubbed: Compose names the networks, Docker's inspect
    reports the subnets other networks hold."""
    script = RUN_SH.read_text()
    start = script.index("assign_subnets() {")
    end = script.index("\n}\n", start) + 3
    inspect = [{"IPAM": {"Config": [{"Subnet": subnet}]}} for subnet in taken]
    stubs = f"""
compose() {{ [[ "$*" == "config --networks" ]] && printf '%s\\n' {' '.join(names)}; }}
docker() {{
  case "$1 $2" in
    "network ls") printf 'id%s\\n' {' '.join(str(i) for i in range(len(taken)))} ;;
    "network inspect") cat <<'JSON'
{__import__('json').dumps(inspect)}
JSON
    ;;
  esac
}}
export -f docker
"""
    result = subprocess.run(
        ["bash", "-c", f"{stubs}\n{script[start:end]}\nWORK={tmp_path}\nassign_subnets"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return (tmp_path / "networks.yml").read_text()


def test_wp71_lane_6_every_network_of_a_lane_stack_gets_its_own_free_subnet(tmp_path: Path) -> None:
    """A lane stack's networks took Docker's default pools, which a shared daemon runs out of ("all predefined
    address pools have been fully subnetted"); each now gets an explicit /26 of the lanes' own range that no other
    network holds."""
    import ipaddress

    taken = [str(s) for s in ipaddress.ip_network("10.216.0.0/16").subnets(new_prefix=17)][:1]
    text = _assign_subnets(tmp_path, ["pipeshub", "sandbox_plane", "web-gateway"], taken)
    subnets = [ipaddress.ip_network(line.split("subnet:")[1].strip()) for line in text.splitlines() if "subnet:" in line]
    assert [line.strip().rstrip(":") for line in text.splitlines() if line.startswith("  ") and not line.startswith("   ")] == [
        "pipeshub",
        "sandbox_plane",
        "web-gateway",
    ]
    assert len(subnets) == 3 and len(set(subnets)) == 3
    assert all(s.prefixlen == 26 and s.subnet_of(ipaddress.ip_network("10.216.0.0/16")) for s in subnets)
    assert not any(s.overlaps(ipaddress.ip_network(taken[0])) for s in subnets)


def test_wp71_lane_7_a_stack_keeps_the_subnets_it_was_given(tmp_path: Path) -> None:
    (tmp_path / "networks.yml").write_text("networks: {}\n")
    assert _assign_subnets(tmp_path, ["pipeshub"], []) == "networks: {}\n"


def test_r15_e2e_1_a_spec_that_rewrites_org_policy_runs_only_on_a_disposable_stack() -> None:
    """R15-E2E-1 (WP-71 hygiene x round 14's admin specs): a spec that writes the org's policy sections changes what
    everyone on the stack runs under while it runs, and leaves its saves in the sections' history; on a shared stack that is
    people's data. Such a spec is tagged ``@disposable``, which playwright.config.ts leaves out there."""
    e2e = Path(__file__).resolve().parents[5] / "frontend" / "tests" / "e2e"
    writes = re.compile(r"/policy/org/current")
    untagged = [
        str(path.relative_to(e2e))
        for path in sorted(e2e.rglob("*.spec.ts"))
        if writes.search(text := path.read_text(encoding="utf-8")) and ".put(" in text and "@disposable" not in text
    ]
    assert untagged == [], f"tag these @disposable: {untagged}"


def test_r15_e2e_2_a_node_modules_link_to_a_link_is_mounted_at_every_hop(tmp_path: Path) -> None:
    """R15-E2E-2 (round 15 gate): the gate's worktree links frontend/node_modules to the candidate's worktree, whose
    own node_modules links to a checkout. Only the last target was mounted, so inside the Playwright container the
    first link pointed nowhere ("Cannot find module '@playwright/test'"). Every hop now holds the target."""
    script = (Path(__file__).resolve().parents[5] / "deployment" / "e2e" / "run.sh").read_text(encoding="utf-8")
    start = script.index("  local modules=() hop=")
    end = script.index("\n  fi\n", start) + len("\n  fi\n")
    checkout = tmp_path / "checkout" / "node_modules"
    checkout.mkdir(parents=True)
    candidate = tmp_path / "candidate" / "frontend"
    candidate.mkdir(parents=True)
    (candidate / "node_modules").symlink_to(checkout)
    gate = tmp_path / "gate" / "frontend"
    gate.mkdir(parents=True)
    (gate / "node_modules").symlink_to(candidate / "node_modules")
    body = f'f() {{\n{script[start:end]}  printf "%s\\n" "${{modules[@]}}"\n}}\nROOT={tmp_path / "gate"}\nf'
    result = subprocess.run(["bash", "-c", body], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    mounts = [line for line in result.stdout.splitlines() if line != "-v"]
    target = str(checkout.resolve())
    assert f"{target}:{candidate / 'node_modules'}:ro" in mounts
    assert f"{target}:{checkout}:ro" in mounts
