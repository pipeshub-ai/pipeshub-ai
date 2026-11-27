"""E2E3-F3: a durable engine run's answer carries resolved citations, and a ref the run never read is dropped.

Found by the tool matrix (KN-6): ``PipesHubEngineRunPresenter.finished`` wrote ``citations: []`` and kept the engine's
text as it was, so a durable engine's cited answer showed no sources and kept an invented ``refN``.
"""

from __future__ import annotations

import json
from typing import Any

from app.harness.engines.answer import EngineAnswer
from app.harness.engines.presenter import ANSWER, PipesHubEngineRunPresenter
from harness_core.engines.durable.ports import ProgressStamp
from tests.unit.agents.adapter.conftest import make_context
from tests.unit.harness.engines.knowledge_fixtures import Q3, KnowledgeGraphSearch


async def _searched() -> tuple[Any, list[str]]:
    """A run whose one knowledge call returned Q3's first block, labelled by the run's mapper."""
    context = make_context()
    search = KnowledgeGraphSearch(context.tool_state, {"q3": Q3})
    output = await search.execute(q="q3")
    return context, [str(output.data)]


async def test_f3_a_ref_the_run_read_becomes_a_citation_and_an_invented_one_is_dropped() -> None:
    context, read = await _searched()
    text, citations = EngineAnswer(context, lambda: read)("Revenue grew 11% [q](ref1), margins [m](ref42).")
    assert "ref42" not in text and "ref1" not in text
    assert [c["metadata"]["recordId"] for c in citations] == ["rec-q3"]


async def test_f3_a_footnote_naming_a_record_the_run_read_is_cited() -> None:
    context, read = await _searched()
    text, citations = EngineAnswer(context, lambda: read)("Revenue grew 11% [1].\n\nSources:\n[1] Q3 report")
    assert [c["metadata"]["recordId"] for c in citations] == ["rec-q3"], text


async def test_f3_a_ref_journaled_by_an_earlier_attempt_is_kept() -> None:
    """A resumed attempt restores the run's mapper but did not see the earlier attempt's texts."""
    context, _ = await _searched()
    _, citations = EngineAnswer(context, list)("Revenue grew 11% [q](ref1).")
    assert [c["metadata"]["recordId"] for c in citations] == ["rec-q3"]


async def test_f3_the_finished_answer_event_carries_the_citations() -> None:
    context, read = await _searched()
    stamp = ProgressStamp("run-1", "trn_1", iter(range(100)).__next__)
    presenter = PipesHubEngineRunPresenter(answer=EngineAnswer(context, lambda: read))
    events = presenter.finished(stamp, success=True, reason=None, output="Revenue grew 11% [q](ref1) [x](ref9).")
    (answer,) = [e.data for e in events if e.type == ANSWER]
    assert "ref9" not in str(answer["answer"])
    assert "rec-q3" in json.dumps(answer["citations"])
    plain = PipesHubEngineRunPresenter().finished(stamp, success=True, reason=None, output="text [q](ref1)")
    (unresolved,) = [e.data for e in plain if e.type == ANSWER]
    assert unresolved["citations"] == []


def test_chat_tl_r2_a_file_the_run_published_is_linked_to_its_artifact_not_its_sandbox_path() -> None:
    from harness_core.artifacts.types import ArtifactCard

    stamp = ProgressStamp("run-1", "trn_1", iter(range(100)).__next__)
    presenter = PipesHubEngineRunPresenter()

    def card(name: str, artifact_id: str, lifecycle: str) -> ArtifactCard:
        return ArtifactCard(artifact_id, 1, name, "text/markdown", 10, "DOCUMENT", lifecycle)  # type: ignore[arg-type]

    presenter.artifact(stamp, card("travel_policy.md", "rec-2", "draft"))
    presenter.artifact(stamp, card("travel_policy.md", "rec-2", "final"))
    presenter.artifact(stamp, card("old.md", "rec-3", "discarded"))
    output = "I wrote **`out/travel_policy.md`** and [out/old.md](out/old.md)."
    # MR-18: the kernel links the final text once (``LinksFiles``) and ends the run with it.
    events = presenter.finished(stamp, success=True, reason=None, output=presenter.linked(output))
    (answer,) = [e.data for e in events if e.type == ANSWER]
    assert answer["answer"] == "I wrote **[travel_policy.md](/record/rec-2)** and old.md."
    assert answer["partTexts"] == [answer["answer"]]


def test_finishing_an_engine_answer_never_imports_the_legacy_streaming_module() -> None:
    """Batch 74: ``finished`` imported ``app.utils.streaming`` (langchain and every provider SDK, ~4s) on the worker's
    event loop at the end of an engine run; under load that starved the lease renewal and the run was lost
    (``lease expired (self-fence)``). The link sanitizer lives in a module that imports only ``re``."""
    import subprocess
    import sys

    code = (
        "import sys\n"
        "from app.harness.engines.presenter import PipesHubEngineRunPresenter\n"
        "from harness_core.engines.durable.ports import ProgressStamp\n"
        "stamp = ProgressStamp('run-1', 'trn_1', iter(range(10)).__next__)\n"
        "PipesHubEngineRunPresenter().finished(stamp, success=True, reason=None, output='see out/a.md')\n"
        "assert 'app.utils.streaming' not in sys.modules, 'the legacy streaming module was imported'\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 0, done.stderr[-2000:]
