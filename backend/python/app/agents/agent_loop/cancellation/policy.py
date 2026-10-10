"""Who may cancel a registered run; shared by both registries."""

from __future__ import annotations

from app.agents.agent_loop.cancellation.registry import RunOwner

__all__ = ["CancelRequester", "may_cancel"]


class CancelRequester(RunOwner):
    """`via_participant_grant` is set only by the service-token route, after Node has
    checked the caller's role. It defaults to False so a payload from an older worker
    fails closed."""

    via_participant_grant: bool = False


def may_cancel(owner: RunOwner, requester: RunOwner) -> bool:
    if owner.org_id != requester.org_id:
        return False
    if isinstance(requester, CancelRequester) and requester.via_participant_grant:
        return bool(
            owner.conversation_id
            and requester.conversation_id
            and owner.conversation_id == requester.conversation_id
        )
    if owner.user_id != requester.user_id:
        return False
    # Enforced only when both sides carry one: an older Node build, or a first turn
    # registered before a conversationId existed, must not regress to a hard 403.
    return not (
        owner.conversation_id
        and requester.conversation_id
        and owner.conversation_id != requester.conversation_id
    )
