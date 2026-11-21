"""R32-REPO (ADR-REPO-01, ADR-RUN-03 D5): a job asked to check out public code can, and says plainly when it cannot.

A deep-research request comparing Onyx with PipesHub, asking to check the docs and the GitHub code repo, went to a
Codex job. ``repository_fetch`` was off (the org had no git hosts, the release default), the brief
never named ``ph git clone``, and the answer never said it had not read either code base. Shown here at each layer:

- the default: a run with web access gets the release's default presets, each held by the deployment's cap; a run
  without web access gets none;
- the brief: a job that may clone is told how (``ph git clone``, the person allows it); one that may not is told what
  it can't do and that it must say so;
- the hand-back: a clone the person declined, a rule refused or a host not allowed reaches the parent as a plain
  instruction and the job step's line, built from the journal, never from the model's words.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from app import edition_config
from app.harness.engines.capability_limits import (
    LIMITS_KEY,
    LIMITS_NOTE_KEY,
    CapabilityLimit,
    limits_instructions,
    limits_note,
    limits_summary,
    refused_clones,
    run_limits,
)
from app.harness.engines.child_result import EngineChildResult
from app.harness.repositories import (
    TOOL_ID,
    UNAVAILABLE_EVENT,
    default_repository_policy,
    live_repository_hosts,
    repository_instructions,
)
from harness_core.contracts.generated.policy_data import WEBSITE_RULES

DEFAULT_HOSTS = [
    {"host": "github.com", "preset": "github-public"},
    {"host": "gitlab.com", "preset": "gitlab-public"},
    {"host": "bitbucket.org", "preset": "bitbucket-public"},
]


# --- R32REPO-1: the default --------------------------------------------------------------------------------------------


def test_r32repo_1_the_default_is_the_releases_public_presets() -> None:
    assert [p.id for p in WEBSITE_RULES.repository_presets or [] if p.default] == [
        "github-public", "gitlab-public", "bitbucket-public"
    ]
    assert default_repository_policy() == {"hosts": DEFAULT_HOSTS}


def test_r32repo_1_a_run_with_web_access_may_clone_public_repositories() -> None:
    # A live layer with no repositories member at all.
    assert live_repository_hosts(None, None, web=True) == DEFAULT_HOSTS
    assert live_repository_hosts({"deployment": {"hosts": [{"host": "github.com"}]}}, None, web=True) == DEFAULT_HOSTS[:1]


def test_r32repo_1_without_web_access_the_default_does_not_apply() -> None:
    assert live_repository_hosts(None, None, web=False) == []


def test_r32repo_1_without_an_edition_s_layers_an_org_list_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(edition_config, "org_network_layers", None, raising=False)
    assert live_repository_hosts({"org": {"hosts": []}}, None, web=True) == DEFAULT_HOSTS
    assert live_repository_hosts({"org": {"hosts": [{"host": "github.com"}]}}, None, web=False) == []
    narrowed = {"agents": {"a1": {"hosts": [{"host": "github.com", "prefixes": ["onyx-dot-app/"]}]}}}
    assert live_repository_hosts(narrowed, "a1", web=True) == DEFAULT_HOSTS


# --- R32REPO-2: the brief ---------------------------------------------------------------------------------------------


def test_r32repo_2_a_job_that_may_clone_is_told_the_command_and_that_the_person_allows_it() -> None:
    note = repository_instructions(DEFAULT_HOSTS)
    assert "may clone public repositories from bitbucket.org, github.com, gitlab.com" in note
    assert "run `ph git clone https://github.com/<owner>/<repo>`; PipesHub fetches it after the person allows it" in note
    assert "check out, read or compare a code repository, clone it" in note
    assert "never answer as if you had read it" in note
    assert limits_instructions(run_limits(web=True, repository_hosts=DEFAULT_HOSTS, unavailable=())) == ""


def test_r32repo_2_a_job_that_may_not_clone_is_told_what_it_cant_do_and_to_say_so() -> None:
    off = limits_instructions(run_limits(web=True, repository_hosts=[], unavailable=()))
    assert "What this task can't do here:" in off
    assert "Clone or read code repositories: cloning repositories is turned off for agents here" in off
    assert "ask whoever runs PipesHub to allow a repository host" in off
    assert "say plainly in your answer what you could not do, why, and what would enable it" in off
    no_web = limits_instructions(run_limits(web=False, repository_hosts=[], unavailable=()))
    assert "Search or read the web: this task has no web access" in no_web
    assert "Clone or read code repositories: this task has no web access" in no_web


def test_r32repo_2_a_source_the_person_has_not_connected_is_named() -> None:
    @dataclasses.dataclass
    class Item:
        kind: str
        name: str

    limits = run_limits(web=True, repository_hosts=DEFAULT_HOSTS, unavailable=[Item("mcp", "Linear")])
    assert [limit.sentence() for limit in limits] == [
        "Use Linear: the person has not connected it (connect Linear in PipesHub)"
    ]


def test_r32repo_2_the_engine_guide_names_ph_git_clone() -> None:
    from harness_engines.pipeshub import _resource

    guide = _resource("AGENTS.md")
    assert "ph git clone https://github.com/<owner>/<repo>   # a public repository, fetched after the person allows it" in guide
    assert "when a clone is declined or refused, say so in your answer" in guide


# --- R32REPO-3: the hand-back -----------------------------------------------------------------------------------------


@dataclasses.dataclass
class _Loose:
    type: str
    data: dict[str, Any]


def _clone(invocation: str) -> _Loose:
    return _Loose("tool.intent", {"invocationId": invocation, "toolId": TOOL_ID})


def test_r32repo_3_declined_refused_and_not_allowed_clones_are_read_from_the_journal() -> None:
    events = [
        _clone("i1"),
        _Loose("tool.denied", {"invocationId": "i1", "rule": "interaction_denied"}),
        _Loose("tool.outcome", {"invocationId": "i1", "status": "error"}),
        _clone("i2"),
        _Loose("tool.denied", {"invocationId": "i2", "rule": "flow"}),
        _Loose("tool.outcome", {"invocationId": "i2", "status": "error"}),
        _clone("i3"),
        _Loose(UNAVAILABLE_EVENT, {"host": "git.example.com", "path": "/acme/widgets", "reason": "host_not_allowed"}),
        _Loose("tool.outcome", {"invocationId": "i3", "status": "error"}),
        _clone("i4"),
        _Loose("tool.outcome", {"invocationId": "i4", "status": "ok"}),
        _Loose("tool.intent", {"invocationId": "w1", "toolId": "web@duckduckgo/web_search"}),
        _Loose("tool.outcome", {"invocationId": "w1", "status": "error"}),
    ]
    limits = refused_clones(events, TOOL_ID)  # type: ignore[arg-type]
    assert [(limit.kind, limit.sentence()) for limit in limits] == [
        ("repository.not_allowed", "Read git.example.com/acme/widgets: its host is not one this server may clone from "
         "(ask whoever runs PipesHub to allow a repository host)"),
        ("repository.declined", "Read the code repository it asked to clone: the person declined the clone"),
        ("repository.refused", "Read the code repository it asked to clone: a rule of your organization refused it"),
    ]
    assert refused_clones([_clone("ok"), _Loose("tool.outcome", {"invocationId": "ok", "status": "ok"})], TOOL_ID) == ()  # type: ignore[list-item]


def test_r32repo_3_a_declined_card_is_read_from_its_interaction_not_an_intent() -> None:
    """The lane's journal (R32-REPO MR-8, run 5658adbd): a clone the person declined is journaled as its card and the
    denial, with no ``tool.intent``; the card names the repository."""
    card = "ix.approve.k1"
    events = [
        _Loose("interaction.explained", {"interactionId": card, "toolId": TOOL_ID, "rule": "tool", "layer": "engine",
                                         "destination": {"key": "sha256:x", "label": "github.com/pxh-e2e/journey-9dfcf5 "
                                                         "(public, read-only, up to 500 MB)"}}),
        _Loose("interaction.requested", {"interactionId": card, "kind": "approval"}),
        _Loose("interaction.resolved", {"interactionId": card, "decision": "denied", "channel": "web"}),
        _Loose("tool.denied", {"invocationId": "inv_1", "rule": "interaction_denied"}),
        _Loose("tool.outcome", {"invocationId": "inv_1", "status": "error"}),
        # Another tool's declined card is not a clone.
        _Loose("interaction.explained", {"interactionId": "ix.other", "toolId": "mcp@x/create_ticket"}),
        _Loose("interaction.resolved", {"interactionId": "ix.other", "decision": "denied"}),
    ]
    limits = refused_clones(events, TOOL_ID)  # type: ignore[arg-type]
    assert [limit.sentence() for limit in limits] == [
        "Read github.com/pxh-e2e/journey-9dfcf5: the person declined the clone"
    ]
    approved = [*events[:2], _Loose("interaction.resolved", {"interactionId": card, "decision": "approved"})]
    assert refused_clones(approved, TOOL_ID) == ()  # type: ignore[arg-type]


def test_r32repo_3_the_job_hands_back_its_limits() -> None:
    class Context:
        query = "check out github.com/onyx-dot-app/onyx"
        tool_state: dict[str, Any] = {}

    run = (CapabilityLimit("web", "Search or read the web", "this task has no web access"),)
    shaper = EngineChildResult(Context(), lambda: ()).with_limits(run)
    events = [_clone("i1"), _Loose("tool.denied", {"invocationId": "i1", "rule": "interaction_denied"})]
    out = shaper({"output": "Here is what I found."}, events)  # type: ignore[arg-type]
    assert [limit["kind"] for limit in out[LIMITS_KEY]] == ["repository.declined", "web"]  # type: ignore[index,union-attr]
    assert LIMITS_KEY not in EngineChildResult(Context(), lambda: ())({"output": "x"}, [])


def test_r32repo_3_the_parent_is_told_plainly_and_the_step_says_it() -> None:
    declined = CapabilityLimit("repository.declined", "Read the code repository it asked to clone", "the person declined the clone")
    note = limits_note([declined])
    assert note.startswith("The coding engine could not do all of this: Read the code repository it asked to clone")
    assert "tell them plainly what could not be done, why, and what would enable it" in note
    assert limits_summary([declined]) == " It could not read the code repository it asked to clone (the person declined the clone)."
    assert limits_summary([CapabilityLimit("web", "Search or read the web", "no web")]) == ""
    assert limits_note([]) == "" and limits_instructions([]) == ""


async def test_r32repo_3_code_task_shows_the_note_to_the_parent_and_the_line_in_the_step() -> None:
    from app.harness.assembly.code_task import RESULT_PRESENTED, CodeTaskDelegation
    from harness_core.kernel.core.messages import ToolCall

    tools = CodeTaskDelegation()
    declined = CapabilityLimit("repository.declined", "Read the code repository it asked to clone", "the person declined the clone")
    document = {"success": True, "output": "Onyx has connectors X and Y.", LIMITS_KEY: [declined.to_json()]}
    outcome = await tools.outcome(ToolCall(id="c1", name="code_task", arguments={}), document)
    shown = outcome.content
    assert isinstance(shown, dict)
    assert LIMITS_KEY not in shown
    assert shown[LIMITS_NOTE_KEY] == limits_note([declined])
    frame = next(e for e in outcome.events if e.type == RESULT_PRESENTED)
    assert frame.data["resultSummary"].endswith(  # type: ignore[index,union-attr]
        "It could not read the code repository it asked to clone (the person declined the clone)."
    )


# --- R32REPO-4: the engine run's brief, built by the production assembler --------------------------------------------


@pytest.mark.parametrize(
    ("tool_ids", "org", "told", "not_told"),
    [
        # Web access, the org never saved git hosts.
        (["web@duckduckgo/web_search"], None, ["Public code: this task may clone public repositories from "
                                               "bitbucket.org, github.com, gitlab.com", "ph git clone"],
         ["What this task can't do here"]),
        # No web access at all.
        ([], None, ["Search or read the web: this task has no web access",
                    "Clone or read code repositories: this task has no web access"], ["Public code:"]),
    ],
    ids=["web-default-on", "no-web"],
)
async def test_r32repo_4_the_engine_brief_says_what_the_run_can_and_cant_do(
    tool_ids: list[str], org: dict[str, Any] | None, told: list[str], not_told: list[str]
) -> None:
    from harness_core.effects.log import RequestEffectLog
    from harness_core.testing.engines.durable import engine_profile
    from tests.unit.harness.assembly import world as w
    from tests.unit.harness.pinning.support import EngineRig, PendingHold, pinned

    rig = EngineRig()
    rig.control.repositories(org)
    spec = pinned(w.pinned_spec(tool_ids=tool_ids), rig.control.pin())
    rig.world.journal.admit("run-r32", org_id=w.ORG, session_id="ses-r32", spec=spec, input_text="q")
    claimed = await rig.world.journal.claim("run-r32", worker_id="w-r32")
    assert claimed is not None
    attempt = dataclasses.replace(
        rig.world.kernel().attempt_of(w.node_claim(claimed), engine_profile()),
        hold=PendingHold(), effect_log=RequestEffectLog(),
    )
    built = await rig.assembler.build(attempt)
    try:
        for text in told:
            assert text in built.instructions, (text, built.instructions[-1500:])
        for text in not_told:
            assert text not in built.instructions, (text, built.instructions[-1500:])
    finally:
        if built.close is not None:
            await built.close()
