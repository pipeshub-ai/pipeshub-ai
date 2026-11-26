"""PF-09 variants (R32-SECCOMP): the runtime benchmark samples any daemon runtime under a label, so the cost of an
operator flag (``runsc --oci-seccomp``) is measured the minute it is applied, and reported against runc by the
pre-registered rule and against plain gVisor on its own."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts" / "pf09_runtime_bench.py"
SAMPLES = ROOT / "backend" / "python" / "tests" / "fixtures" / "pf09" / "samples-2026-11-12.jsonl"


def _bench() -> ModuleType:
    spec = importlib.util.spec_from_file_location("pf09_runtime_bench", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bench = _bench()


def test_pfv1_variants_default_to_runc_and_runsc_and_a_label_renames_one() -> None:
    assert [(v.label, v.runtime) for v in bench.variants(None, None)] == [("runc", "runc"), ("runsc", "runsc")]
    chosen = bench.variants(["runc", "runsc"], ["runsc=runsc-oci-seccomp"])
    assert [(v.label, v.runtime) for v in chosen] == [("runc", "runc"), ("runsc-oci-seccomp", "runsc")]


@pytest.mark.parametrize(
    ("runtimes", "labels"),
    [(["runc"], ["runsc=x"]), (["runc", "runsc"], ["runsc"]), (["runc", "runsc"], ["runsc=runc"])],
)
def test_pfv2_a_label_for_a_runtime_not_sampled_or_a_clash_is_refused(runtimes: list[str], labels: list[str]) -> None:
    with pytest.raises(ValueError, match="runtime-label|share a label"):
        bench.variants(runtimes, labels)


def _rows(label: str, scale: float, cold: float) -> list[dict[str, object]]:
    room = {"cpu": 10, "mem": 30, "load1": 2.0}
    io = [{"kind": "io", "runtime": label, "room": room, "git_clone": 10 * scale, "npm_ci": 10 * scale,
           "pytest": 40 * scale} for _ in range(7)]
    return [*io, *({"kind": "cold", "runtime": label, "room": room, "cold_start": cold} for _ in range(7))]


def test_pfv3_a_runsc_variant_is_ruled_against_runc_and_measured_against_runsc() -> None:
    rows = [*_rows("runc", 1.0, 0.4), *_rows("runsc", 1.4, 0.45), *_rows("runsc-oci-seccomp", 1.47, 0.46)]
    compared = bench.compare(rows, conformance_ok=True)["compared"]
    assert set(compared) == {"runsc/runc", "runsc-oci-seccomp/runc", "runsc-oci-seccomp/runsc"}
    assert compared["runsc-oci-seccomp/runc"]["verdict"]["rule"] == "keep_gvisor_default"
    assert compared["runsc-oci-seccomp/runsc"]["verdict"]["io_ratio"] == pytest.approx(1.05)


def test_pfv4_the_committed_samples_still_give_the_adrs_decision() -> None:
    rows = [json.loads(line) for line in SAMPLES.read_text().splitlines() if line.strip()]
    decided = bench.compare(rows, conformance_ok=True)["compared"]["runsc/runc"]["verdict"]
    assert decided["rule"] == "keep_gvisor_default"
    assert round(decided["io_ratio"], 2) == 1.35
