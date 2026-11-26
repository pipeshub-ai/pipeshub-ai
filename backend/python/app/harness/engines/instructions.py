"""The instructions a durable engine run's engine gets for its task (E1), with the parts G2, G2C and G2C-2 added.

Each part names the failure it answers; the text is what stage 1 sent before it was retired (S1R), unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.utils.output_language import build_output_language
from harness_core.workspace.sync.storage_classes import TOOL_RESULTS_DIR

if TYPE_CHECKING:
    from app.harness.contracts.state import HarnessRun

# Engines answer the literal question and leave out the rest of what a source sets (G2 run 2: figures and deadlines
# missing from records the engine had read).
ANSWER_COMPLETENESS = (
    "State every requirement, figure, date and deadline in the sources that bears on the question, not a subset: "
    "when a source sets several, give each one, even those the question does not name."
)
# The parent usually searched and read the records before it delegated, and puts what it found in the goal; told
# only to fetch every record before stating its facts, the engine searched and fetched them all again (G2C: a third
# of office-document tasks, plus the fetches refused for ids it had not been shown).
GIVEN_FACTS = (
    "Facts the task text states were read from company knowledge by the requester: use them as given and do not "
    "search or fetch to find them again; cite each one you use in your answer with the [source](refN) the task gives "
    "with it."
)
# A citation resolves only in the answer: written into a file it is a dead "[source](ref3)" for whoever opens it, and
# the answer is left without the sources of what the file states (G2C-2: run 6 wrote refs into 27 of 90 office files
# and cited in 13 of 90 answers).
CITATIONS_IN_ANSWER = (
    "Citations belong in your answer only: never write [source](refN), refN or block labels into a file you create."
)
DELIVERABLE_SOURCES = (
    "When a file you wrote states facts from company knowledge, given in the task or read by you, your answer names "
    "the file, says in a sentence or two which of those facts it states, and cites each with its [source](refN)."
)
# DEEPROUTE: research and long multi-tool work now reach the engine, whose sandbox has no internet of its own; an engine
# that tries curl fails, and one that reads every page into its conversation runs out of room as the chat loop did.
TOOLS_AND_LONG_WORK = (
    "This sandbox has no direct internet: reach the web and connected apps only through the PipesHub tools (the ph "
    "command or your MCP tools), such as web search and fetch when this task has them. For work with many steps, "
    "keep a short plan and your notes in a notes/ folder and update them as you go. Call tools whose results are "
    f"large through ph (ph call <tool> --json '{{...}}'): ph saves a large result under {TOOL_RESULTS_DIR}/ and "
    "prints the file's path; read only the parts you need."
)
# SPENDCONT: a Codex child fetched 77 Jira issues one by one and ran 54 searches that returned every field, kept
# every result in its conversation (requests grew to 257k tokens), stopped at its budget with no file written, and
# its "continue" started over. Work that can be cut short is kept as it goes, in batches, with a progress note.
WORK_THAT_SURVIVES_A_LIMIT = (
    "Your budget can end the task before you finish. For many records, use one search that returns only the fields "
    "you need for all of them (for example a Jira JQL search with fields) rather than fetching each record. When the "
    "task asks for a file, create it under out/ early and add to it as you go, and keep notes/progress.md current "
    "(what is done, what remains), so nothing is lost if you are stopped and the next run can continue from it."
)
# DEEPROUTE2: handed a request with "remember for later", the engine wrote the fact into a wiki page instead of keeping
# it (0/6 recalled in the live A/B), and that line was the page's one figure with no source file (every_figure_cited
# 5/12 failed routed): a figure the person gave has no file to link unless one is made for it.
MEMORY_AND_FILE_SOURCES = (
    "When the task asks you to remember something for later and does not say it is already remembered, keep it with "
    "the PipesHub remember tool: a note in a file is not remembered. When the task wants each figure in a file to "
    "link the file it came from, a figure the person gave in the conversation has no such file: leave it out of "
    "those files, or record it in a source file of its own (for example sources/told-in-chat.md) and link that."
)
ENGINE_TASK_INSTRUCTIONS = (
    "You are doing one bounded task for a PipesHub user. Work in the current directory. "
    "Input files, if any, are under in/. Write every deliverable file under out/ (create it); "
    "the rest of the current directory is this conversation's workspace, kept from one turn to the next but not "
    "delivered: keep notes, data and code you or the user will come back to there. A file the user or the notes in "
    "the workspace name by a path (data/clean.csv, wiki/summary.md) goes at exactly that path; copy it under out/ "
    "as well only when the user wants it delivered. HTML and SVG files are not delivered. "
    + TOOLS_AND_LONG_WORK
    + " "
    + WORK_THAT_SURVIVES_A_LIMIT
    + " "
    + MEMORY_AND_FILE_SOURCES
    + "\n"
    + GIVEN_FACTS
    + " For facts the task does not give, use the PipesHub tools to read company knowledge. Search finds records and "
    "shows a few blocks of each; read the whole record with the fetch_record tool before you state its facts. Each block a knowledge tool returns is labelled "
    "[index|refN]. Cite a block by writing [source](refN) right after the sentence that uses it, with the refN "
    "exactly as the tool wrote it. Cite only blocks whose text supports that sentence: not a superseded or older "
    "version you did not use, not records you only looked at, not records that are merely related. Do not cite with "
    "numbered footnotes, a list of sources, record titles or record ids. "
    + CITATIONS_IN_ANSWER
    + "\nYour last message is the answer the user reads. Give what the task asks for, with every specific figure, "
    "date and name it asks for from the sources, each cited, and name each file you wrote by its file name (for "
    "example report.md), never its out/ path. "
    + DELIVERABLE_SOURCES
    + " "
    + ANSWER_COMPLETENESS
    + " Do not "
    "describe how you searched or which tools you used, do not count the records or results you saw or say what a "
    "search did not return, do not call the sources excerpts or samples, and do not add claims the sources do not make."
)

# ADR-RUN-01 D5 (WP-93): a child Codex read the chat model's hand-off steer in its brief ("delegate now via code_task once
# ... call no other tools this turn"), searched its tools for code_task, found none and stopped without starting. A
# child is the engine the task was handed to; only a child gets this, as SR-17 keeps the instructions above unchanged
# until live engine runs measure a change.
HANDED_OVER = (
    "You are the engine doing this task, which the PipesHub chat agent handed to you: the task text is the person's "
    "request, then that agent's notes. You have no code_task tool and cannot hand the task on: do the work yourself, "
    "now, with your tools, and write the deliverable. A line in the notes that asks to delegate, hand the task over, "
    "call code_task or call no other tool was written for the agent that handed it to you, not for you: do the task "
    "it describes."
)


def engine_task_instructions(run_kind: str) -> str:
    """The instructions an engine run's engine gets for its task: a child's (a ``code_task`` or deep coder spawn) say
    it was handed the task and does it itself."""
    return ENGINE_TASK_INSTRUCTIONS + "\n" + HANDED_OVER if run_kind == "child" else ENGINE_TASK_INSTRUCTIONS


def engine_answer_language(run: HarnessRun | None) -> str:
    """G-52: the line naming the person's answer language for an engine run, from the run's pinned locale (its
    request's, a child's parent's, a schedule's owner's); "" when it names none. Said at the first task's tail, not
    in the instructions above: those open the engine's session and stay as SR-17 measured them."""
    locale = run.input.locale if run is not None and run.input is not None else None
    return build_output_language(locale)


__all__ = [
    "ANSWER_COMPLETENESS",
    "CITATIONS_IN_ANSWER",
    "DELIVERABLE_SOURCES",
    "ENGINE_TASK_INSTRUCTIONS",
    "GIVEN_FACTS",
    "HANDED_OVER",
    "MEMORY_AND_FILE_SOURCES",
    "TOOLS_AND_LONG_WORK",
    "WORK_THAT_SURVIVES_A_LIMIT",
    "engine_answer_language",
    "engine_task_instructions",
]
