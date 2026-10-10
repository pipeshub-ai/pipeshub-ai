"""`@assistant help`: a canned capability card computed as the message sender.

A message that is exactly an assistant alias (or its `<@assistant:self>` token)
plus `help` is answered here without a model call. The connector list comes from
the sender's own access, so a shared chat never shows what another participant
can reach.
"""

import json
import logging
import re
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Final, Protocol

from pydantic import BaseModel

from app.agents.agent_loop.protocol.agui import AGUIEventType, frame, new_id
from app.agents.agent_loop.protocol.formatter import AGUI_FORMATTER
from app.config.constants.arangodb import Connectors
from app.modules.agents.handles import ASSISTANT_ALIASES


class ConnectorTypeSource(Protocol):
    """The one graph call the card needs (`IGraphDBProvider`); declared here so this domain package imports no persistence module."""

    async def get_accessible_connector_types(self, user_id: str, org_id: str) -> list[str]: ...

_HELP_COMMAND: Final = re.compile(
    r"^\s*(?:<@assistant:self>|@(?:" + "|".join(ASSISTANT_ALIASES) + r"))[\s,:]+help\s*[?!.]?\s*$",
    re.IGNORECASE,
)

_CONNECTOR_LABELS: Final[dict[str, str]] = {
    Connectors.KNOWLEDGE_BASE.value: "Collections",
    **{c.value: c.name.replace("_", " ").title() for c in Connectors if c is not Connectors.KNOWLEDGE_BASE},
}


class HelpCard(BaseModel):
    answer: str
    connectors: list[str]
    participantCount: int


def is_help_command(query: str | None) -> bool:
    return bool(query) and _HELP_COMMAND.match(query or "") is not None


def _connector_label(connector_type: str) -> str:
    return _CONNECTOR_LABELS.get(connector_type, connector_type.replace("_", " ").title())


async def build_help_card(
    graph_provider: ConnectorTypeSource | None,
    *,
    user_id: str | None,
    org_id: str | None,
    participant_count: int,
    logger: logging.Logger,
) -> HelpCard:
    """Both inputs describe the sender; nothing here reads another participant's access."""
    types: list[str] = []
    if graph_provider is not None and user_id and org_id:
        try:
            types = await graph_provider.get_accessible_connector_types(user_id, org_id)
        except Exception:
            logger.warning("Could not list the sender's connectors for the help card", exc_info=True)
    labels = sorted({_connector_label(t) for t in types})

    lines = ["**What I can do in this chat**", ""]
    if labels:
        lines.append(f"- Search and answer from what *you* can access: {', '.join(labels)}.")
    else:
        lines.append("- Search and answer from what *you* can access. I could not find any connected sources for your account.")
    lines.append("- Answers use only your access, but everyone in this chat can read them.")
    if participant_count > 1:
        lines.append(f"- This chat has {participant_count} people; your message and my reply are visible to all of them.")
    else:
        lines.append("- Only you can see this chat right now.")
    lines.append("- Mention a teammate with @name to leave a note without asking me. Use @assistant to ask me.")
    return HelpCard(answer="\n".join(lines), connectors=labels, participantCount=participant_count)


async def stream_help_card(
    card: HelpCard, *, run_id: str | None, thread_id: str | None
) -> AsyncIterator[str]:
    """The same AG-UI frames a normal answer ends with, so Node and the UI need no special case."""
    run = run_id or new_id("run")
    context = SimpleNamespace(run_id=run, conversation_id=thread_id)
    completion = {
        "answer": card.answer,
        "citations": [],
        "confidence": "High",
        "answerMatchType": "Capability Card",
    }
    events = [
        frame(AGUIEventType.RUN_STARTED, threadId=thread_id, runId=run),
        *AGUI_FORMATTER.answer_delta(
            context, chunk=card.answer, accumulated=card.answer, citations=[], raw_length=len(card.answer)
        ),
        *AGUI_FORMATTER.answer_final(context, completion_data=completion),
    ]
    for evt in events:
        yield f"event: {evt['event']}\ndata: {json.dumps(evt['data'])}\n\n"
