"""English words of a run's outcome, by key (ADR-RUN-01 D1). Only ``OutcomePresenter`` and ``OutcomeBriefing``
read them; a locale added beside this one holds the same keys (``test_run01_d1_presenter`` checks)."""

from __future__ import annotations

from typing import Final

_CONTINUE: Final = 'Send "continue" to carry on from here.'
_RESUME: Final = (
    'Send "continue" to pick up from here: what was done is saved, and the next reply does only what is still missing.'
)

MESSAGES: Final[dict[str, str]] = {
    # What stopped the run, per reason, as each sentence needs it.
    "outcome.reason.turns.notice": "This stopped early at its step limit, so the result above is partial.",
    "outcome.reason.budget.notice": "This stopped early at its spending limit, so the result above is partial.",
    "outcome.reason.time.notice": "This stopped early when its working time ran out, so the result above is partial.",
    "outcome.reason.stall.notice": (
        "This stopped early because its steps kept repeating without progress, so the result above is partial."
    ),
    "outcome.reason.provider.notice": (
        "This stopped early because the AI model kept refusing requests for several minutes (it was busy or getting "
        "too many requests), so the result above is partial."
    ),
    "outcome.reason.engine_error.notice": (
        "This stopped early because the coding engine ran into an error, so the result above is partial."
    ),
    "outcome.reason.secret_blocked.notice": (
        "This stopped early because PipesHub blocked a request that looked like it contained a secret (an API key or "
        "access token), so the result above is partial."
    ),
    "outcome.reason.turns.resume": _RESUME,
    "outcome.reason.budget.resume": (
        f"{_RESUME} If this keeps happening, ask an admin to raise the spending limit if it is the organization's."
    ),
    "outcome.reason.time.resume": _RESUME,
    "outcome.reason.stall.resume": (
        'Send "continue" to pick up from here, ideally with more detail or another approach: what was done is '
        "saved, and the next reply does only what is still missing."
    ),
    "outcome.reason.provider.resume": (
        "Try again in a few minutes, or ask your admin to check the model's limits. Then send \"continue\" to pick "
        "up from here: what was done is saved, and the next reply does only what is still missing."
    ),
    "outcome.reason.turns.stopped": (
        "This reply stopped early because it reached its step limit, before an answer could be written."
    ),
    "outcome.reason.budget.stopped": (
        "This reply stopped early because it reached its spending limit, before an answer could be written."
    ),
    "outcome.reason.time.stopped": (
        "This reply stopped early because it ran out of working time, before an answer could be written."
    ),
    "outcome.reason.stall.stopped": (
        "This reply stopped early because its steps kept repeating without making progress, before an answer "
        "could be written."
    ),
    "outcome.reason.provider.stopped": (
        "This reply stopped early because the AI model kept refusing requests for several minutes (it was busy or "
        "getting too many requests), before an answer could be written."
    ),
    "outcome.reason.engine_error.stopped": (
        "This reply stopped early because the coding engine ran into an error, before an answer could be written."
    ),
    "outcome.reason.secret_blocked.stopped": (
        "This reply stopped early because PipesHub blocked a request that looked like it contained a secret (an API "
        "key or access token), before an answer could be written."
    ),
    "outcome.reason.turns.continue": _CONTINUE,
    "outcome.reason.budget.continue": (
        'Send "continue" to carry on in a new reply, or ask an admin to raise the spending limit if it is the '
        "organization's."
    ),
    "outcome.reason.time.continue": _CONTINUE,
    "outcome.reason.stall.continue": (
        'Send "continue" to carry on from here, ideally with more detail or another approach to try.'
    ),
    "outcome.reason.provider.continue": (
        "Try again in a few minutes, or ask your admin to check the model's limits. Then send \"continue\" to carry "
        "on from here."
    ),
    "outcome.reason.engine_error.resume": _RESUME,
    "outcome.reason.engine_error.continue": _CONTINUE,
    "outcome.reason.secret_blocked.resume": (
        f"{_RESUME} If it happens again, remove the secret from the files or messages this task reads."
    ),
    "outcome.reason.secret_blocked.continue": (
        'Send "continue" to carry on. If it happens again, remove the secret from the files or messages this task '
        "reads."
    ),
    "outcome.reason.turns.because": "it reached its step limit",
    "outcome.reason.budget.because": "it reached its spending limit",
    "outcome.reason.time.because": "it ran out of working time",
    "outcome.reason.stall.because": "its steps kept repeating without making progress",
    "outcome.reason.provider.because": "the AI model kept refusing requests for several minutes",
    "outcome.reason.engine_error.because": "the coding engine ran into an error",
    "outcome.reason.secret_blocked.because": "PipesHub blocked a request that looked like it contained a secret",
    "outcome.reason.turns.limit": "its step limit",
    "outcome.reason.budget.limit": "its spending limit",
    "outcome.reason.time.limit": "its working-time limit",
    "outcome.reason.stall.limit": "a limit",
    "outcome.reason.provider.limit": "a limit",
    "outcome.reason.engine_error.limit": "an error in the coding engine",
    "outcome.reason.secret_blocked.limit": "a request PipesHub blocked because it looked like it contained a secret",
    "outcome.reason.unfinished.limit": "the end of its turn without that file",
    # A run that failed with a code of its own words (Node's run error message; the web chat words the code itself).
    "outcome.failed.mcp_tool_withdrawn": (
        "{servers} no longer offers a tool this task was using, so the task stopped. "
        "Send your message again to continue without it, or ask your admin to review the server's tools."
    ),
    "outcome.list.and": " and ",
    "outcome.list.comma": ", ",
    # A partial result's closing block.
    "outcome.partial.title": "**This result is partial.**",
    "outcome.partial.saved": "Saved so far: **{name}**, marked partial.",
    "outcome.partial.written": (
        "**{name}** holds what the task had written when it stopped; its last part may be missing."
    ),
    "outcome.opening.full_report": "The full report is in **{name}**.",
    "outcome.unfinished.written": (
        "**Written from what was gathered.** The task ended its turn without writing **{name}**, so it was written "
        'from the sources the task had gathered. Send "continue" to extend it.'
    ),
    # A requested file the run could not write: what exists, and the action that matches it.
    "outcome.unwritten.mark": "isn't written yet.**",
    "outcome.unwritten.lead.end": (
        "**{name} isn't written yet.** This task ended its turn without writing the file it was asked for."
    ),
    "outcome.unwritten.lead.cut": (
        "**{name} isn't written yet.** This task stopped because {because} while it was still gathering sources, "
        "before it wrote the file."
    ),
    "outcome.unwritten.lead.nothing.end": (
        "**{name} isn't written yet.** This task ended its turn before it started on the work, so it gathered "
        "nothing to write the file from."
    ),
    "outcome.unwritten.lead.nothing.cut": (
        "**{name} isn't written yet.** This task stopped because {because} before it started on the work, so it "
        "gathered nothing to write the file from."
    ),
    "outcome.unwritten.gathered": "What it gathered: {tools}.",
    "outcome.unwritten.calls.one": "{tool} (1 call)",
    "outcome.unwritten.calls.many": "{tool} ({count} calls)",
    "outcome.unwritten.sources": "Sources it read, among them:",
    "outcome.unwritten.more": "- and {count} more",
    "outcome.unwritten.kept": (
        "Everything it gathered is saved in this conversation's files ({handoff} lists it). "
        'Send "continue" to write {name} from it.'
    ),
    "outcome.unwritten.lost": (
        'What it gathered could not be saved, so a new attempt starts over. Send "continue" to try the task again.'
    ),
    "outcome.unwritten.retry": 'Send "continue" to try the task again.',
    # The banner over a partial file the run wrote for a cut-short task.
    "outcome.banner.synthesized": (
        "> **Partial**: written from the sources gathered before the task reached {limit}; what it could not cover "
        'is listed under "Still to do". Send "continue" in the chat to extend it.'
    ),
    "outcome.banner.cut": (
        "> **Partial**: this file was cut short when the task reached {limit}, so it is incomplete. Send "
        '"continue" in the chat to finish it.'
    ),
    # A loop run whose limit ran out when no model call could answer.
    "outcome.fallback.saved": "It saved {names} in this conversation's files.",
    "outcome.fallback.gathered": "What it had gathered so far:",
    "outcome.fallback.tool.one": "- {tool}",
    "outcome.fallback.tool.many": "- {tool} ({count} calls)",
    "outcome.fallback.more": "- and {count} more tools",
    # A parent's model, when the engine child it handed work to failed (OutcomeBriefing); the harness channel's frame
    # says whose words they are (ADR-RUN-01 D5).
    "briefing.failed.nothing": (
        "The coding engine this work was handed to failed and returned no answer. Do not hand it to the engine again. "
        "Answer now from what this conversation has already gathered, and say plainly in one line, in the person's "
        "language, that the coding engine could not finish the task, so this answer may be incomplete."
    ),
    "briefing.failed.unkept": (
        "The coding engine this work was handed to stopped with an error before it finished. It had saved {revisions} "
        "versions of its working files, which were kept apart and not added to this conversation's files, so nothing "
        "it wrote can be shown here. Do not hand it to the engine again in this message. Answer now from what this "
        "conversation has already gathered, and say plainly, in the person's language, that the coding engine could "
        "not finish, that this answer may be incomplete, and that they can send 'continue' to try the task again."
    ),
    "briefing.failed.kept": (
        "The coding engine this work was handed to stopped with an error before it finished. What it had saved was "
        "put in the folder “{folder}” in this conversation's files, apart from the finished files and not checked. Do "
        "not hand it to the engine again in this message. Answer now from what this conversation has already "
        "gathered, and say plainly, in the person's language, that the coding engine could not finish, that this "
        "answer may be incomplete, that its unfinished work is in the folder “{folder}”, and that they can send "
        "'continue' to try the task again."
    ),
    # A run that handed its task off up front and whose engine failed: its answer, written without a model.
    "briefing.answer.lead": (
        "**This answer is partial.** The coding engine this task was handed to stopped with an error before it "
        "finished, so there is no finished answer."
    ),
    "briefing.answer.kept": "What it had saved is in the folder “{folder}” in this conversation's files, not checked",
    "briefing.answer.unkept": (
        "It had saved {revisions} versions of its working files, which were kept apart and not added to this "
        "conversation's files."
    ),
    "briefing.answer.more": ", and {count} more",
    "briefing.answer.retry": 'Send "continue" to try the task again.',
}

__all__ = ["MESSAGES"]
