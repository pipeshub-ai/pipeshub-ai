"""C-22 (P5.11): durable and legacy ``run_code`` give the same results on the same input.

One wave 1 data-analysis and one office-documents case, the scripted reference model, the host sandbox:
the legacy route (``legacy_driver``) and the durable path pinned ``agent-code`` (``durable_driver``) each run
it from a fresh registry seeded with the case's inputs. Every ``run_code`` call must return the same exit
code, stdout and artifact names, and the registry must end holding the same outputs, byte for byte (an office
file's creation timestamps aside).
"""

from __future__ import annotations

import io
import json
import zipfile
from typing import TYPE_CHECKING, Any

import pytest

from tests.harness_replay import durable_driver, legacy_driver
from tests.support.parity_cases.wave1_cases import select
from tests.support.parity_cases.wave1_reference import script_for
from tests.support.parity_cases.wave1_systems import (  # pyright: ignore[reportPrivateUsage]
    _registry,
    _spec,
    task_for,
)
from tests.support.sandbox_env import sandbox_backend, sandbox_env

if TYPE_CHECKING:
    from tests.harness_replay.artifacts import ParityArtifactRegistry
    from tests.harness_replay.legacy_driver import DriveResult

CASES = ("da-05-revenue-by-product-file", "od-01-incident-memo")


# Office files stamp their creation time; their content is the rest of the archive.
_TIMESTAMPED = frozenset({"docProps/core.xml", "docProps/app.xml"})


def _content(data: bytes) -> object:
    if not data.startswith(b"PK"):
        return data
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {n: archive.read(n) for n in sorted(archive.namelist()) if n not in _TIMESTAMPED}


def _run_code_results(result: DriveResult) -> list[dict[str, Any]]:
    shown: list[dict[str, Any]] = []
    for step in result.recording.tool_steps():
        if step.name != "run_code":
            continue
        data: Any = step.result
        if isinstance(data, str):
            data = json.loads(data)
        shown.append(
            {
                "status": step.status,
                "exit_code": data.get("exit_code"),
                "stdout": data.get("stdout"),
                "artifacts": sorted(a["name"] if isinstance(a, dict) else str(a) for a in data.get("artifacts") or []),
            }
        )
    return shown


async def _drive(case_id: str, *, durable: bool) -> tuple[DriveResult, ParityArtifactRegistry]:
    case = select(ids=[case_id])[0]
    task = task_for(case, 0, script_for(case))
    registry = _registry(task)
    spec = _spec(task, registry, chat_mode="quick", model="scripted-model", env=sandbox_env("host"))
    spec.script = task.script
    if durable:
        spec.sandbox_profile = "agent-code"
    with sandbox_backend("host"):
        result = await (durable_driver.drive(spec) if durable else legacy_driver.drive(spec))
    return result, registry


@pytest.mark.timeout(600)
@pytest.mark.parametrize("case_id", CASES)
async def test_c22_durable_and_legacy_run_code_agree(case_id: str) -> None:
    legacy, legacy_files = await _drive(case_id, durable=False)
    durable, durable_files = await _drive(case_id, durable=True)
    calls = _run_code_results(legacy)
    assert calls, "the reference script calls run_code"
    assert _run_code_results(durable) == calls
    outputs = {f.name: _content(f.content) for f in legacy_files.outputs()}
    assert outputs
    assert {f.name: _content(f.content) for f in durable_files.outputs()} == outputs
    assert durable.recording.final.status == legacy.recording.final.status == "completed"
