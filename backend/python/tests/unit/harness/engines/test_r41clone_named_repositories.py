"""R41CLONE-1..7: the repositories a task names reach the engine as clone commands, and one never cloned is said so.

In a manual research run, the person's request named github.com/onyx-dot-app/onyx and
github.com/pipeshub-ai/pipeshub-ai and asked to "check ... the GitHub code repo"; the run allowed github.com, but the
only clone guidance was a generic line in the engine's instructions. The Codex job read web pages, never ran
``ph git clone``, and its answer did not say it had not read either code base.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.harness.engines.capability_limits import (
    DECLINED,
    NOT_ALLOWED,
    NOT_CLONED,
    NOT_CLONED_WORDS,
    CapabilityLimit,
    could_not_do_view,
    met_items,
    uncloned,
)
from app.harness.repositories import (
    FETCHED_EVENT,
    named_repositories,
    named_repositories_line,
)
from harness_core.provenance.stamp import PROVENANCE_EVENT

ONYX_REQUEST = (
    "Do a deep research on how Onyx (onyx.app, github.com/onyx-dot-app/onyx) compares with PipesHub (pipeshub.com, "
    "github.com/pipeshub-ai/pipeshub-ai).\n\nFor both products, check the docs, the GitHub code repo, the website. "
    "Notes: inspect https://github.com/onyx-dot-app/onyx/blob/main/README.md and https://github.com/features/copilot "
    "and https://docs.onyx.app/admins/billing/overview."
)
GITHUB = [{"host": "github.com", "publicReadable": True}]


def _event(kind: str, data: dict[str, Any]) -> Any:  # noqa: ANN401
    return SimpleNamespace(type=kind, data=data)


def test_r41clone_1_the_onyx_request_names_both_repositories_once_each() -> None:
    named = named_repositories(ONYX_REQUEST, GITHUB)
    assert [r.label for r in named] == ["github.com/onyx-dot-app/onyx", "github.com/pipeshub-ai/pipeshub-ai"]
    assert all(r.allowed for r in named)


def test_r41clone_2_forge_pages_docs_and_other_hosts_are_not_repositories() -> None:
    text = "See github.com/features/copilot, github.com/orgs/acme, docs.onyx.app/admins/billing and example.com/a/b."
    assert named_repositories(text, GITHUB) == ()


def test_r41clone_3_the_task_line_gives_each_clone_command() -> None:
    line = named_repositories_line(named_repositories(ONYX_REQUEST, GITHUB))
    assert "`ph git clone https://github.com/onyx-dot-app/onyx`" in line
    assert "`ph git clone https://github.com/pipeshub-ai/pipeshub-ai`" in line
    assert "End your turn" not in line and "keep working while it waits" in line


def test_r41clone_4_a_repository_on_a_host_the_run_may_not_clone_from_is_named_as_such() -> None:
    named = named_repositories("Compare gitlab.com/acme/tool with github.com/acme/app.", GITHUB)
    assert [(r.label, r.allowed) for r in named] == [("gitlab.com/acme/tool", False), ("github.com/acme/app", True)]
    line = named_repositories_line(named)
    assert "`ph git clone https://github.com/acme/app`" in line and "may not clone gitlab.com/acme/tool" in line
    assert named_repositories_line(()) == ""


def test_r41clone_5_a_named_repository_never_cloned_is_a_could_not_do_item() -> None:
    named = named_repositories(ONYX_REQUEST, GITHUB)
    fetched = [_event(FETCHED_EVENT, {"host": "github.com", "repository": "pipeshub-ai/pipeshub-ai", "commit": "a" * 40})]
    limits = uncloned(fetched, named, (), typed=True)
    assert [(lim.kind, lim.target) for lim in limits] == [(NOT_CLONED, "github.com/onyx-dot-app/onyx")]
    view = could_not_do_view(met_items(limits))
    assert view is not None and view["line"] == (
        "I couldn't read github.com/onyx-dot-app/onyx: it was never cloned, so its code was not checked."
    )


def test_r41clone_6_a_clone_read_through_provenance_or_already_declined_is_not_told_twice() -> None:
    named = named_repositories(ONYX_REQUEST, GITHUB)
    read = [_event(PROVENANCE_EVENT, {"provenance": {"sources": [
        {"kind": "repository", "url": "https://github.com/onyx-dot-app/onyx", "commit": "b" * 40},
    ]}})]
    declined = (CapabilityLimit(DECLINED, "Read github.com/pipeshub-ai/pipeshub-ai", "the person declined the clone",
                                target="github.com/pipeshub-ai/pipeshub-ai"),)
    assert uncloned(read, named, declined, typed=True) == ()


def test_r41clone_7_an_older_node_gets_words_and_a_disallowed_host_its_own_kind() -> None:
    named = named_repositories("Read gitlab.com/acme/tool and github.com/acme/app.", GITHUB)
    limits = uncloned([], named, (), typed=False)
    assert [lim.kind for lim in limits] == [NOT_ALLOWED, NOT_CLONED_WORDS]
    # The words-only kind never reaches a typed view an older Node would refuse.
    assert [item["kind"] for item in met_items(limits)] == [NOT_ALLOWED]


async def test_r41clone_8_the_engine_run_is_assembled_with_the_named_clones_at_its_tasks_tail() -> None:
    """The worker's assembly: the run's task text names both repositories, the run may clone from github.com (the
    release default on a web run), and the first task's tail gives each clone command; the hand-back knows them."""
    import dataclasses

    from app.harness.engines.child_result import EngineChildResult
    from harness_core.effects.log import RequestEffectLog
    from harness_core.testing.engines.durable import engine_profile
    from tests.unit.harness.assembly import world as w
    from tests.unit.harness.pinning.support import EngineRig, PendingHold, pinned

    rig = EngineRig()
    rig.control.repositories(None)
    spec = pinned(w.pinned_spec(tool_ids=["web@duckduckgo/web_search"]), rig.control.pin())
    rig.world.journal.admit("run-r41", org_id=w.ORG, session_id="ses-r41", spec=spec, input_text=ONYX_REQUEST)
    claimed = await rig.world.journal.claim("run-r41", worker_id="w-r41")
    assert claimed is not None
    attempt = dataclasses.replace(
        rig.world.kernel().attempt_of(w.node_claim(claimed), engine_profile()),
        hold=PendingHold(), effect_log=RequestEffectLog(),
    )
    built = await rig.assembler.build(attempt)
    try:
        assert "`ph git clone https://github.com/onyx-dot-app/onyx`" in built.task_facts, built.task_facts
        assert "`ph git clone https://github.com/pipeshub-ai/pipeshub-ai`" in built.task_facts
        if isinstance(built.child_result, EngineChildResult):
            assert [r.label for r in built.child_result._named] == [  # pyright: ignore[reportPrivateUsage]
                "github.com/onyx-dot-app/onyx", "github.com/pipeshub-ai/pipeshub-ai",
            ]
    finally:
        if built.close is not None:
            await built.close()
