"""Every code sandbox capacity / live limit default, pinned against the docs.

The defaults are read from the code (parsing an empty environment) and the
documented values are parsed from the files operators read, so a change on
either side fails here instead of drifting:

- ``backend/env.template`` and ``deployment/docker-compose/env.template``
  (``# SANDBOX_X=value`` lines),
- ``deployment/docker-compose/ADVANCED_DEPLOYMENT.md`` (the variable tables,
  including the documented clamp ranges),
- ``deployment/helm/pipeshub-ai/values.yaml`` and its ``README.md``.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

from app.agent_loop_lib.sandbox import capacity as capacity_mod
from app.agent_loop_lib.sandbox.capacity import (
    CapacitySettings,
    MachineSize,
    SandboxCapacity,
)
from app.agent_loop_lib.sandbox.coding.settings import (
    SandboxSettings,
    max_run_timeout_from_env,
)
from app.agent_loop_lib.sandbox.live_settings import LiveSettings
from app.agent_loop_lib.sandbox.live_yield import YIELD_MIN_WAIT_S

REPO = Path(__file__).resolve().parents[7]
BACKEND_ENV = REPO / "backend" / "env.template"
COMPOSE_ENV = REPO / "deployment" / "docker-compose" / "env.template"
ADVANCED = REPO / "deployment" / "docker-compose" / "ADVANCED_DEPLOYMENT.md"
HELM_VALUES = REPO / "deployment" / "helm" / "pipeshub-ai" / "values.yaml"
HELM_README = REPO / "deployment" / "helm" / "pipeshub-ai" / "README.md"
HELM_DEPLOYMENT = REPO / "deployment" / "helm" / "pipeshub-ai" / "templates" / "deployment.yaml"

GiB = 2**30

_CAP = CapacitySettings.from_env({})
_LIVE = LiveSettings.from_env({})

# Variable -> the code's default with nothing set. ``None``: unset by default
# (sized from the machine, no cap, or a percentage of memory).
CODE_DEFAULTS: dict[str, object] = {
    "SANDBOX_MAX_TOTAL": _CAP.max_total,
    "SANDBOX_MAX_PER_ORG": _CAP.max_per_org,
    "SANDBOX_MAX_PER_REQUEST": capacity_mod.max_per_request({}),
    "SANDBOX_MIN_TOTAL": _CAP.min_total,
    "SANDBOX_MEMORY_SHARE": _CAP.memory_share,
    "SANDBOX_CPU_FACTOR": _CAP.cpu_factor,
    "SANDBOX_HOST_MEMORY_BYTES": _CAP.host_memory_bytes,
    "SANDBOX_HOST_CPU_MILLIS": _CAP.host_cpu_millis,
    "SANDBOX_HOST_PID_LIMIT": _CAP.host_pid_limit,
    "SANDBOX_CAPACITY_DIVISOR": None if _CAP.divisor == 1 else _CAP.divisor,
    "SANDBOX_SLOT_WAIT_S": SandboxSettings.model_fields["slot_wait_s"].default,
    "SANDBOX_MAX_RUN_TIMEOUT_S": max_run_timeout_from_env({}),
    "SANDBOX_LIVE_LIMIT": "auto" if _LIVE.enabled else "off",
    "SANDBOX_LIVE_INTERVAL_S": _LIVE.interval_s,
    "SANDBOX_HOST_MEM_FLOOR_MB": _LIVE.host_mem_floor_mb,
    "SANDBOX_HOST_MEM_SOFT_MB": _LIVE.host_mem_soft_mb,
    "SANDBOX_HOST_CPU_BRAKE": _LIVE.host_cpu_brake,
    "SANDBOX_HOST_CPU_PSI_BRAKE": _LIVE.host_cpu_psi_brake,
    "SANDBOX_MEMORY_OVERCOMMIT": _LIVE.memory_overcommit,
    "SANDBOX_SIDECAR_SOURCE": _LIVE.sidecar_source,
    "SANDBOX_SIDECAR_MEM_HIGH": _LIVE.sidecar_mem_high,
    "SANDBOX_SIDECAR_MEM_HARD": _LIVE.sidecar_mem_hard,
    "SANDBOX_SIDECAR_CPU_BRAKE": _LIVE.sidecar_cpu_brake,
    "SANDBOX_SIDECAR_CPU_THROTTLE": _LIVE.sidecar_cpu_throttle,
    "SANDBOX_YIELD_INDEXING": "on" if _LIVE.yield_indexing else "off",
    "SANDBOX_YIELD_TTL_S": _LIVE.yield_ttl_s,
}

# How each variable parses, for the documented ranges.
PARSERS = {
    "SANDBOX_MEMORY_SHARE": lambda v: CapacitySettings.from_env({"SANDBOX_MEMORY_SHARE": v}).memory_share,
    "SANDBOX_CPU_FACTOR": lambda v: CapacitySettings.from_env({"SANDBOX_CPU_FACTOR": v}).cpu_factor,
    "SANDBOX_MAX_RUN_TIMEOUT_S": lambda v: max_run_timeout_from_env({"SANDBOX_MAX_RUN_TIMEOUT_S": v}),
    "SANDBOX_LIVE_INTERVAL_S": lambda v: LiveSettings.from_env({"SANDBOX_LIVE_INTERVAL_S": v}).interval_s,
    "SANDBOX_MEMORY_OVERCOMMIT": lambda v: LiveSettings.from_env({"SANDBOX_MEMORY_OVERCOMMIT": v}).memory_overcommit,
    "SANDBOX_YIELD_TTL_S": lambda v: LiveSettings.from_env({"SANDBOX_YIELD_TTL_S": v}).yield_ttl_s,
}

_UNSET_WORDS = ("sized", "none", "unset")


def _same(doc: str, code: object) -> bool:
    doc = doc.strip().strip("`")
    if code is None:
        return doc == "" or doc.split()[0].lower() in _UNSET_WORDS or "%" in doc
    if isinstance(code, str):
        return doc.split()[0].lower() == code
    match = re.match(r"-?\d+(?:\.\d+)?", doc)
    return match is not None and math.isclose(float(match.group()), float(code))  # type: ignore[arg-type]


def _env_template(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        match = re.match(r"^#\s*(SANDBOX_[A-Z_]+)=(.*)$", line.strip())
        if match:
            values.setdefault(match.group(1), match.group(2).strip())
    return values


def _md_rows(path: Path) -> dict[str, tuple[str, str]]:
    """``| `VAR` (, `VAR2`) | default | meaning |`` rows -> (default, meaning)."""
    rows: dict[str, tuple[str, str]] = {}
    for line in path.read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        for name in re.findall(r"`([A-Za-z_.]+)`", cells[0]):
            rows.setdefault(name, (cells[1], cells[2]))
    return rows


ADV_ROWS = _md_rows(ADVANCED)
HELM_ROWS = _md_rows(HELM_README)


@pytest.mark.parametrize("path", [BACKEND_ENV, COMPOSE_ENV], ids=["backend", "compose"])
def test_env_templates_show_the_code_defaults(path: Path) -> None:
    documented = _env_template(path)
    checked = [name for name in CODE_DEFAULTS if name in documented]
    assert checked, f"no sandbox capacity variables found in {path}"
    wrong = {
        name: (documented[name], CODE_DEFAULTS[name])
        for name in checked if not _same(documented[name], CODE_DEFAULTS[name])
    }
    assert wrong == {}


def test_backend_env_template_lists_every_variable() -> None:
    missing = sorted(set(CODE_DEFAULTS) - set(_env_template(BACKEND_ENV)))
    assert missing == []


ADV_VARS = sorted(n for n in CODE_DEFAULTS if n in ADV_ROWS)


def test_advanced_deployment_documents_the_compose_variables() -> None:
    # The sidecar and divisor/pid variables are Helm-only; everything else
    # an operator of Compose can set is in the tables.
    helm_only = {
        "SANDBOX_CAPACITY_DIVISOR", "SANDBOX_HOST_PID_LIMIT", "SANDBOX_SIDECAR_SOURCE",
        "SANDBOX_SIDECAR_MEM_HIGH", "SANDBOX_SIDECAR_MEM_HARD", "SANDBOX_SIDECAR_CPU_BRAKE",
        "SANDBOX_SIDECAR_CPU_THROTTLE",
    }
    assert sorted(set(CODE_DEFAULTS) - helm_only - set(ADV_ROWS)) == []


@pytest.mark.parametrize("name", ADV_VARS)
def test_advanced_deployment_default(name: str) -> None:
    default, _ = ADV_ROWS[name]
    assert _same(default, CODE_DEFAULTS[name]), (name, default, CODE_DEFAULTS[name])


_RANGE = re.compile(r"\((\d+(?:\.\d+)?)\s*[–-]\s*(\d+(?:\.\d+)?)\)")
RANGED = sorted(n for n in PARSERS if n in ADV_ROWS and _RANGE.search(ADV_ROWS[n][1]))


def test_every_parser_has_a_documented_range() -> None:
    assert RANGED == sorted(PARSERS)


@pytest.mark.parametrize("name", RANGED)
def test_advanced_deployment_range_is_the_clamp(name: str) -> None:
    match = _RANGE.search(ADV_ROWS[name][1])
    assert match is not None
    low, high = float(match.group(1)), float(match.group(2))
    parse = PARSERS[name]
    assert parse(str(low)) == pytest.approx(low)
    assert parse(str(high)) == pytest.approx(high)
    assert parse(str(high * 10 + 1)) == pytest.approx(high)
    # Below the range: clamped up (a run timeout of zero or less is the default instead).
    below = parse(str(low / 2)) if low > 0 else parse("-1")
    assert below == pytest.approx(low)


# Helm README value -> the variable it feeds.
HELM_KEYS = {
    "sandbox.capacity.minTotal": "SANDBOX_MIN_TOTAL",
    "sandbox.capacity.cpuFactor": "SANDBOX_CPU_FACTOR",
    "sandbox.capacity.slotWaitSeconds": "SANDBOX_SLOT_WAIT_S",
    "sandbox.capacity.maxTotal": "SANDBOX_MAX_TOTAL",
    "sandbox.capacity.maxPerOrg": "SANDBOX_MAX_PER_ORG",
    "sandbox.capacity.maxPerRequest": "SANDBOX_MAX_PER_REQUEST",
    "sandbox.capacity.pidLimit": "SANDBOX_HOST_PID_LIMIT",
    "sandbox.capacity.memoryOvercommit": "SANDBOX_MEMORY_OVERCOMMIT",
    "sandbox.dind.liveLimit.memHigh": "SANDBOX_SIDECAR_MEM_HIGH",
    "sandbox.dind.liveLimit.memHard": "SANDBOX_SIDECAR_MEM_HARD",
    "sandbox.dind.liveLimit.cpuBrake": "SANDBOX_SIDECAR_CPU_BRAKE",
    "sandbox.dind.liveLimit.cpuThrottle": "SANDBOX_SIDECAR_CPU_THROTTLE",
}


@pytest.mark.parametrize("key", sorted(HELM_KEYS))
def test_helm_readme_default(key: str) -> None:
    assert key in HELM_ROWS, key
    name = HELM_KEYS[key]
    assert _same(HELM_ROWS[key][0], CODE_DEFAULTS[name]), (key, HELM_ROWS[key][0], CODE_DEFAULTS[name])


def test_helm_values_comments_name_the_code_defaults() -> None:
    text = HELM_VALUES.read_text()
    match = re.search(r"Empty values use the defaults: (\d+\.\d+), (\d+\.\d+), (\d+\.\d+) and (\d+\.\d+)", text)
    assert match is not None
    assert [float(v) for v in match.groups()] == [
        CODE_DEFAULTS["SANDBOX_SIDECAR_MEM_HIGH"], CODE_DEFAULTS["SANDBOX_SIDECAR_MEM_HARD"],
        CODE_DEFAULTS["SANDBOX_SIDECAR_CPU_BRAKE"], CODE_DEFAULTS["SANDBOX_SIDECAR_CPU_THROTTLE"],
    ]
    for key, name in (("cpuFactor", "SANDBOX_CPU_FACTOR"), ("slotWaitSeconds", "SANDBOX_SLOT_WAIT_S"),
                      ("memoryOvercommit", "SANDBOX_MEMORY_OVERCOMMIT")):
        # The comment block just above the key says "(default N...)".
        block = re.search(r"((?:^[ \t]*#.*\n)+)[ \t]*" + key + r":", text, re.M)
        assert block is not None, key
        said = re.search(r"\(default (\d+(?:\.\d+)?)", block.group(1))
        assert said is not None, key
        assert float(said.group(1)) == pytest.approx(float(CODE_DEFAULTS[name])), key  # type: ignore[arg-type]
    timeout = re.search(r"^\s*sandboxMaxRunTimeoutS:\s*(\d+)", text, re.M)
    assert timeout is not None
    assert float(timeout.group(1)) == CODE_DEFAULTS["SANDBOX_MAX_RUN_TIMEOUT_S"]


def test_helm_memory_share_is_the_same_everywhere_in_the_chart() -> None:
    values = re.search(r'^\s*memoryShare:\s*"([\d.]+)"', HELM_VALUES.read_text(), re.M)
    template = re.search(r'memoryShare \| default "([\d.]+)"', HELM_DEPLOYMENT.read_text())
    assert values is not None and template is not None
    readme = HELM_ROWS["sandbox.capacity.memoryShare"][0]
    assert values.group(1) == template.group(1)
    assert float(readme) == float(values.group(1))
    # Inside the documented range of the variable it feeds.
    assert CapacitySettings.from_env({"SANDBOX_MEMORY_SHARE": values.group(1)}).memory_share == float(values.group(1))


@pytest.mark.parametrize(
    ("memory_gib", "cpus", "ceiling"), [(16, 4, 6), (64, 16, 24), (192, 48, 72)],
)
def test_advanced_deployment_worked_examples(memory_gib: int, cpus: int, ceiling: int) -> None:
    text = ADVANCED.read_text()
    assert re.search(rf"{memory_gib} GB / {cpus} CPU (host )?gets {ceiling}", text), (memory_gib, cpus, ceiling)
    cap = SandboxCapacity("compose", CapacitySettings(), slot_files=256)
    cap._apply(MachineSize(memory_gib * GiB, float(cpus), None, "docker_info"), None)
    assert cap.ceiling() == ceiling


def test_advanced_deployment_sizing_retry_schedule() -> None:
    text = ADVANCED.read_text()
    match = re.search(r"retries \(after (\d+) s, (\d+) s, (\d+) s, then every minute\)", text)
    assert match is not None
    assert tuple(float(v) for v in match.groups()) == capacity_mod._SIZING_RETRY_S
    assert capacity_mod._SIZING_RETRY_EVERY_S == 60.0


def test_advanced_deployment_yield_wait() -> None:
    match = re.search(r"When a run has waited (\d+) s for memory", ADVANCED.read_text())
    assert match is not None
    assert float(match.group(1)) == YIELD_MIN_WAIT_S


def test_advanced_deployment_interval_sentence() -> None:
    match = re.search(r"Every (\d+) s \(`SANDBOX_LIVE_INTERVAL_S`\)", ADVANCED.read_text())
    assert match is not None
    assert float(match.group(1)) == CODE_DEFAULTS["SANDBOX_LIVE_INTERVAL_S"]
