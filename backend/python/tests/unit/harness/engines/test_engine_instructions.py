"""An engine run's task instructions (G2C-F3, G2C-2 F1..F3), and SR-17: S1R moved them out of stage 1 unchanged."""

from __future__ import annotations

import dataclasses
import hashlib

import pytest

from app.harness.engines.capability_limits import limits_instructions, run_limits
from app.harness.engines.instructions import (
    CITATIONS_IN_ANSWER,
    DELIVERABLE_SOURCES,
    ENGINE_TASK_INSTRUCTIONS,
    GIVEN_FACTS,
)
from tests.support.parity_cases.wave1_cases import load_cases, load_knowledge

# SHA-256 of ``ENGINE_TASK_INSTRUCTIONS``: stage 1's text at S1R's base (``9682364e1``), with two changes since:
# CTL-6's (files named by their file name, never an ``out/`` path) and FIX-P2-B1's (the rest of the directory is the
# conversation's kept workspace; a path the user names is honoured). Any other change needs a live engine check.
# DEEPROUTE: the first paragraph ends with TOOLS_AND_LONG_WORK (no direct internet, notes, large results to files),
# measured on the DEEPROUTE live A/B (impl/DEEPROUTE-plan.md section 6).
# DEEPROUTE2: then MEMORY_AND_FILE_SOURCES (keep a fact asked to be remembered with remember, not in a file; a figure the
# person gave gets a source file of its own), measured on DEEPROUTE2's live A/B (impl/release-notes/deeproute2.md).
# SPENDCONT: then WORK_THAT_SURVIVES_A_LIMIT (batched searches with fields, a requested file under out/ early,
# notes/progress.md kept current), after a Codex child fetched 77 issues one by one and lost its work to a budget stop.
# WP98-FU-4: TOOLS_AND_LONG_WORK's large results go through ph, which saves them under notes/tool-results/ itself (no
# shell redirect); no live engine run has checked it yet (DEC-B made no model call).
STAGE1_INSTRUCTIONS_SHA256 = "d54fa4efe2fe53fb2198feb3c52defdc326674bc79b6bab6270bfc29232d7960"


def test_sr_17_the_instructions_are_stage_1s_text_byte_for_byte() -> None:
    assert hashlib.sha256(ENGINE_TASK_INSTRUCTIONS.encode()).hexdigest() == STAGE1_INSTRUCTIONS_SHA256


async def test_sr_17_a_durable_engine_run_is_given_them() -> None:
    from harness_core.effects.log import RequestEffectLog
    from harness_core.testing.engines.durable import engine_profile
    from tests.unit.harness.assembly import world as w
    from tests.unit.harness.pinning.support import EngineRig, PendingHold

    rig = EngineRig()
    rig.world.journal.admit(
        "run-sr17", org_id=w.ORG, session_id="ses-sr17", spec=w.pinned_spec(tool_ids=[]), input_text="q"
    )
    claimed = await rig.world.journal.claim("run-sr17", worker_id="w-sr17")
    assert claimed is not None
    attempt = dataclasses.replace(
        rig.world.kernel().attempt_of(w.node_claim(claimed), engine_profile()),
        hold=PendingHold(), effect_log=RequestEffectLog(),
    )
    built = await rig.assembler.build(attempt)
    # The task instructions byte for byte; a run with no web access and no git host is told so after them (ADR-RUN-03 D5).
    assert built.instructions == ENGINE_TASK_INSTRUCTIONS + limits_instructions(run_limits(web=False, repository_hosts=[], unavailable=()))


def test_g2c_f3_the_knowledge_paragraph_starts_with_the_given_facts_rule() -> None:
    knowledge = ENGINE_TASK_INSTRUCTIONS.split("\n")[1]
    assert knowledge.startswith(GIVEN_FACTS + " For facts the task does not give, use the PipesHub tools")
    assert "use them as given" in GIVEN_FACTS and "[source](refN)" in GIVEN_FACTS
    assert "do not search or fetch to find them again" in GIVEN_FACTS
    # S1c's rule for what the engine looks up itself stays.
    assert "read the whole record with the fetch_record tool before you state its facts" in knowledge


def test_g2c2_f1_citations_go_in_the_answer_never_in_files() -> None:
    knowledge = ENGINE_TASK_INSTRUCTIONS.split("\n")[1]
    assert knowledge.endswith(CITATIONS_IN_ANSWER)
    assert "never write [source](refN), refN or block labels into a file you create" in CITATIONS_IN_ANSWER


def test_g2c2_f2_a_deliverables_knowledge_is_cited_in_the_answer() -> None:
    answer = ENGINE_TASK_INSTRUCTIONS.split("\n")[2]
    assert answer.startswith("Your last message is the answer the user reads.")
    assert DELIVERABLE_SOURCES in answer
    assert "given in the task or read by you" in DELIVERABLE_SOURCES
    assert "names the file" in DELIVERABLE_SOURCES and "cites each with its [source](refN)" in DELIVERABLE_SOURCES


def test_g2c2_f3_given_facts_are_cited_from_the_answer_and_not_read_again() -> None:
    assert "cite each one you use in your answer with the [source](refN) the task gives" in GIVEN_FACTS
    assert "keep the [source](refN) the task gives with a fact" not in GIVEN_FACTS


def test_fixp2_b1_the_rest_of_the_directory_is_the_conversations_kept_workspace() -> None:
    """FIX-P2-B1: the instructions called every file outside out/ "scratch, not delivered", so all three engines
    wrote the wiki the person asked for at wiki/index.md under out/wiki/ instead (S3 probes, 2026-10-22). An engine run
    in a conversation works in that conversation's workspace, which is kept from one turn to the next."""
    files = ENGINE_TASK_INSTRUCTIONS.split("\n")[0]
    assert "scratch" not in files
    assert "the rest of the current directory is this conversation's workspace, kept from one turn to the next" in files
    # A path the person or the workspace's notes name wins over out/ (a colleague's plan naming wiki/reconciliation.md
    # was written to out/reconciliation.md by Codex after the first wording, S3 resume-invoices-15m).
    assert "A file the user or the notes in the workspace name by a path (data/clean.csv, wiki/summary.md) goes at exactly that path" in files
    assert "copy it under out/ as well only when the user wants it delivered" in files
    # Deliverables are still what out/ holds (CTL-6).
    assert "Write every deliverable file under out/" in files


@pytest.mark.parametrize("text", [GIVEN_FACTS, CITATIONS_IN_ANSWER, DELIVERABLE_SOURCES])
def test_g2c_f3_the_rule_is_generic(text: str) -> None:
    text = text.lower()
    for case in load_cases():
        assert case.id.lower() not in text
        for name in case.inputs:
            assert name.lower() not in text
    for doc in load_knowledge():
        assert doc.title.lower() not in text
