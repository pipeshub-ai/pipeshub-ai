"""A steering message's attachments, given to the model as a new turn gives them (P1b C-21b).

Node authorized the attachments and records at admission and queued them by
reference; the kernel asks this renderer for the message's parts once, at the
boundary that consumes it, and journals them. The text and images come from
the resolvers a new turn's goal is built with: every run's
``resolve_attachments_for_goal`` text with its citation hint (the goal's
description) and images (the first user message's), and for the built-in
assistant also the chat modes' attachment text (its goal's constraints). The
citation sources they add are journaled with the consumption.

Record ids reach the model as a new turn's do: not at all (neither the durable
nor the legacy turn renders ``recordIds``); they are carried and authorized.

A file of the message the model was not given is journaled with the
consumption, and the final answer names it as it names the turn's own
(R34FIX-FU-1, ADR-RUN-03 D5).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from harness_core.kernel.core.messages import Part, TextPart
from harness_core.kernel.durable.ports import RenderedInput

if TYPE_CHECKING:
    from app.harness.assembly.tool_environment import ToolEnvironment
    from app.harness.presentation import PipesHubPresenter
    from harness_core.kernel.durable.ports import SteeringInput
    from harness_core.logs import HarnessLog


class PipesHubInputRenderer:
    def __init__(
        self, env: ToolEnvironment, presenter: PipesHubPresenter, log: HarnessLog, *, counted: bool = True
    ) -> None:
        self._env = env
        # Whether the claim's Node reads an unnamed item's ``count`` (``attachment_count``, R47POLISH-FU-1).
        self._counted = counted
        self._presenter = presenter
        self._log = log

    async def render(self, message: SteeringInput) -> RenderedInput:
        from app.agents.agent_loop.hooks import resolve_attachments_for_goal
        from app.harness.assembly.attachment_limits import (
            steered_unread,
            unread_attachments,
        )
        from app.harness.assembly.context import Attachment
        from app.harness.assembly.loop import ATTACHMENT_HINT
        from app.utils.chat_helpers import image_dict_to_part
        from app.web.addresses import web_addresses

        # The legacy resolvers log file names and ids; the harness logs counts only.
        quiet = _quiet_logger()
        context = self._env.context
        attachments = await self._readable([dict(a) for a in message.attachments], quiet)
        text = message.text
        # The person's own message: a link in it may be opened after a private read.
        web_addresses(context.tool_state).note_user_text(text)
        goal_text, images = (
            await resolve_attachments_for_goal(context, quiet, attachments=attachments)
            if attachments
            else ("", [])
        )
        unread = unread_attachments(
            [a for item in message.attachments if (a := Attachment.of(item)) is not None],
            attachments,
            context.tool_state.get("virtual_record_id_to_result") or {},
            multimodal=bool(context.is_multimodal_llm),
            counted=self._counted,
        )
        if goal_text:
            text = f"{text}\n\n{goal_text}{ATTACHMENT_HINT}"
        parts: list[Part] = [TextPart(text=text)]
        parts.extend(p for image in images if (p := image_dict_to_part(image)) is not None)
        sources = self._presenter.citation_sources()
        self._log.info(
            "input.rendered",
            messageSeq=message.message_seq,
            attachments=len(attachments),
            records=len(message.record_ids),
            attachmentText=bool(goal_text),
            images=len(parts) - 1,
            unread=len(unread),
        )
        events = [e for e in (sources, steered_unread(message.message_seq, unread)) if e is not None]
        return RenderedInput(parts=parts, events=events)

    async def _readable(self, attachments: list[dict[str, Any]], log: logging.Logger) -> list[dict[str, Any]]:
        """A steered message's attachments the reader may access, each naming the record the check found: the
        client's recordId is never trusted (main #4255, as the run's own attachments)."""
        if not attachments:
            return attachments
        from app.harness.knowledge.caller import caller_user_id_of
        from app.utils.attachment_utils import keep_accessible_attachments

        context = self._env.context
        kept, _ = await keep_accessible_attachments(
            context.graph_provider,
            org_id=context.org_id,
            user_id=caller_user_id_of(context.user_info) or context.user_id,
            is_service_account=False,
            attachments=attachments,
            previous_conversations=[],
            logger=log,
        )
        return kept


def _quiet_logger() -> logging.Logger:
    log = logging.getLogger("app.harness.steering.attachments")
    log.propagate = False
    if not log.handlers:
        log.addHandler(logging.NullHandler())
    return log


__all__ = ["PipesHubInputRenderer"]
