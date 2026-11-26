"""RB-1, PR-2, PR-3 (E0b): opencode on its real binary in the tools image, through ``EngineController``.

E0's real lane (``real_stack.py``, ``test_real_controller.Lane``): the broker's hardened sandbox behind
the egress proxy, the real HTTP gateways, the scripted model on the deployment's model route. The run
image is the release one (the tools variant); PR-3 runs the engine-only variant.
"""

from __future__ import annotations

import io
import uuid
from typing import TYPE_CHECKING

import pytest

from harness_engines.release import release_allowlist
from tests.unit.harness.engines.real_stack import engine_broker
from tests.unit.harness.engines.test_real_controller import Lane, task, user_texts
from tests.unit.harness.sandbox_broker.conftest import cleanup, cleanup_networks

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from tests.unit.harness.engines.real_stack import RealStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(900, method="thread")]

RELEASE = next(r for r in release_allowlist() if r.version.kind == "opencode")
SALES = b"region,amount\nnorth,120\nsouth,80\nnorth,30\neast,55\nsouth,20\n"
EXPECTED = {"east": 55, "north": 150, "south": 100}
# One shell line (the scripted model's `shell` directive): totals per region to a sheet, and a bar chart.
ANALYSIS = (
    "mkdir -p out && python3 -c \""
    "import pandas as pd; "
    "t = pd.read_csv('in/sales.csv').groupby('region', as_index=False)['amount'].sum()"
    ".rename(columns={'amount': 'total'}); "
    "t.to_excel('out/totals.xlsx', index=False); "
    "t.plot.bar(x='region', y='total').figure.savefig('out/chart.png'); "
    "print(t.to_csv(index=False))\""
)


@pytest.fixture
def lane(stack: RealStack, broker: DockerSandboxBroker) -> Lane:
    assert RELEASE.image == RELEASE.variants["tools"]
    return Lane(stack, broker)


@pytest.fixture
async def engine_only_lane(stack: RealStack) -> AsyncIterator[Lane]:
    image = RELEASE.variants["engine"]
    namespace = f"io.pipeshub.e0breal.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-e0br-" + namespace.rsplit(".", 1)[1]
    broker, provider = await engine_broker(stack, namespace, prefix, image=image)
    yield Lane(stack, broker, image=image)
    await provider.run_blocking(cleanup, provider, namespace, prefix)
    await provider.run_blocking(cleanup_networks, provider, namespace, stack.proxy)
    provider.close()


def _system(lane: Lane) -> str:
    return lane.gateways[min(lane.gateways)].model_calls[-1].system


async def test_rb1_a_csv_becomes_a_chart_and_a_sheet_with_the_right_totals_published_as_artifacts(lane: Lane) -> None:
    import openpyxl

    await lane.seed({"in/sales.csv": SALES})
    outcome = await lane.controller.run_turn(lane.run(1), task(0, f"RB1\nshell {ANALYSIS}\nsay done"))
    assert (outcome.reason, outcome.final_text.strip()) == ("completed", "done")
    head = await lane.head()
    assert head["out/chart.png"][:8] == b"\x89PNG\r\n\x1a\n"
    sheet = openpyxl.load_workbook(io.BytesIO(head["out/totals.xlsx"])).active
    assert sheet is not None
    rows = [tuple(r) for r in sheet.iter_rows(values_only=True)]
    assert rows[0] == ("region", "total")
    assert {str(r[0]): r[1] for r in rows[1:]} == EXPECTED
    assert sorted(p.path for p in outcome.published) == ["chart.png", "totals.xlsx"]
    history = await lane.publications.history(lane.run(1).binding)
    finals = {c.name for c in history.cards if c.lifecycle == "final"}
    assert finals == {"chart.png", "totals.xlsx"}
    tool_results = [m.content for m in lane.gateways[1].model_calls[-1].messages if m.role == "tool"]
    assert any("north,150" in content for content in tool_results)


async def test_pr2_the_model_is_told_what_the_image_ships(lane: Lane) -> None:
    outcome = await lane.controller.run_turn(lane.run(1), task(0, "PR2\nsay ready"))
    assert outcome.reason == "completed"
    system = _system(lane)
    assert "Instructions from: /usr/share/pxh-engine/RUNTIME.md" in system
    for name in ("pandas", "matplotlib", "python-docx", "python-pptx", "openpyxl"):
        assert name in system
    assert "no package installer" in system and "no network access" in system


async def test_pr3_the_engine_only_image_has_no_note_and_still_runs(engine_only_lane: Lane) -> None:
    outcome = await engine_only_lane.controller.run_turn(
        engine_only_lane.run(1), task(0, "PR3\nshell command -v python3 || echo no-python\nsay ready")
    )
    assert (outcome.reason, outcome.final_text.strip()) == ("completed", "ready")
    assert "RUNTIME.md" not in _system(engine_only_lane)
    assert any("PR3" in text for text in user_texts(engine_only_lane))
    results = [m.content for m in engine_only_lane.gateways[1].model_calls[-1].messages if m.role == "tool"]
    assert any("no-python" in content for content in results)
