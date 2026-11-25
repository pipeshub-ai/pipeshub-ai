"""R13-SALV: one salvage path for a failed hand-off (WP-56 ``keep_unfinished`` x WP-65 ``engine_failed_answer``).

WP-56 keeps a failed child's own files under "Unfinished work/<UTC time>" in the parent's workspace and names them in
the child's merge report; WP-65 answers an up-front hand-off whose engine failed from that report, without a model,
and the model's instruction for any other failed hand-off names the same folder. The files are written once (a
re-driven join writes nothing new and reports the same folder), and the answer names the folder, at most 10 files and
how many more there are. The report lists at most 50 paths, so the count it carries is the true one past 50.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.harness.assembly import handoff_instructions as instructions
from harness_core.engines.outcome.briefing import BRIEFING, ChildReport
from harness_core.failpoints import Raise
from harness_core.testing import armed
from harness_core.workspace.forks import REPORT_PATHS
from tests.unit.harness_core.durable.support import RUN, Crash, reply, scripted
from tests.unit.harness_core.durable.test_child_forks import SCOPE, WS, _Forks, _texts

if TYPE_CHECKING:
    from harness_core.kernel.durable.harness_channel import HarnessInstruction


def engine_failed_answer(content: object) -> str:
    """WP-89: the failed up-front hand-off's answer is the outcome briefing's, read once from the child's report."""
    return BRIEFING.failed_answer(ChildReport.read(content))


def engine_failed_instruction(content: object) -> HarnessInstruction:
    """WP-89: the failed hand-off's instruction is the outcome briefing's, read once from the child's report."""
    return instructions.engine_failed_briefed(ChildReport.read(content))


FOLDER = "Unfinished work/2026-10-03 00.00.00 UTC"
SAVED = 60


async def _failed_child_with(f: _Forks, count: int) -> dict[str, object]:
    _, port = scripted(reply("", ("delegate", {"goal": "x", "task_id": "a"})), reply("final"))
    _ = await f.world.run(f.parent(port))
    (child,) = f.world.journal.children_of(RUN)
    for n in range(count):
        await f.write(child, f"notes/source-{n:02d}.md", f"source {n}")
    _, failing = scripted()
    assert (await f.world.run(f.world.kernel(failing), child)).status == "failed"
    assert (await f.world.run(f.parent(port))).status == "completed"
    (shown,) = _texts(port)
    return shown  # pyright: ignore[reportReturnType]


async def test_r13_salv_1_the_report_counts_every_kept_file_past_the_listed_fifty() -> None:
    f = _Forks()
    shown = await _failed_child_with(f, SAVED)
    unfinished = shown["workspace"]["unfinished"]  # type: ignore[index]
    assert unfinished["folder"] == FOLDER
    assert len(unfinished["files"]) == REPORT_PATHS < SAVED
    assert unfinished["count"] == SAVED


async def test_r13_salv_2_the_answer_names_the_folder_ten_files_and_the_true_rest() -> None:
    f = _Forks()
    shown = await _failed_child_with(f, SAVED)
    text = engine_failed_answer(shown)
    assert f"“{FOLDER}”" in text
    assert "notes/source-00.md" in text and "notes/source-09.md" in text and "notes/source-10.md" not in text
    assert f"and {SAVED - 10} more" in text
    # The model's instruction for any other failed hand-off names the same folder.
    assert FOLDER in engine_failed_instruction(shown).text


async def test_r13_salv_3_the_files_are_written_once_even_when_the_join_is_driven_again() -> None:
    f = _Forks()
    _, port = scripted(reply("", ("delegate", {"goal": "x", "task_id": "a"})), reply("final"))
    _ = await f.world.run(f.parent(port))
    (child,) = f.world.journal.children_of(RUN)
    for n in range(12):
        await f.write(child, f"notes/source-{n:02d}.md", f"source {n}")
    _, failing = scripted()
    assert (await f.world.run(f.world.kernel(failing), child)).status == "failed"
    # The worker dies right after the join kept the files: the next attempt joins again.
    with armed({"worker.join.after_merge": Raise(Crash)}):
        await f.world.crash(f.parent(port))
    kept = await f.files()
    revision = (await f.store.head(SCOPE, WS)).revision
    assert len(kept) == 12 and all(p.startswith(f"{FOLDER}/") for p in kept)
    assert (await f.world.run(f.parent(port))).status == "completed"
    assert await f.files() == kept and (await f.store.head(SCOPE, WS)).revision == revision
    assert [r.unfinished for r in f.waves.reports if r is not None][-1] == FOLDER
    (shown,) = _texts(port)
    assert shown["workspace"]["unfinished"]["count"] == 12  # type: ignore[index]


def test_r13_salv_4_an_older_report_without_a_count_still_reads_its_listed_files() -> None:
    files = [f"a{n}.md" for n in range(14)]
    text = engine_failed_answer({"workspace": {"unfinished": {"folder": FOLDER, "files": files}}})
    assert "and 4 more" in text
