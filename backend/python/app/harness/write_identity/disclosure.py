"""Write identity and AI disclosure on external writes (OWD-18), applied in the toolset write path.

A run's external write that people will read (a message, an email, a ticket, a page, an event, a commit) is sent with
the user's own account and carries a disclosure naming the PipesHub agent and a link to the session, appended in the
target's markup. The wording is fixed product text per action type (WP-90 words each kind of
write).

Who a write is from is an extension point (``app.harness.write_identity.ports.WriteIdentityPort``): its settings may
answer another identity per action type, its own sentence and trailer, and a refusal for a write it cannot send as
chosen. ``WriteIdentitySettings`` itself is this build's rule: every write as the user, with the disclosure.

It is a PRE_TOOL_USE middleware (the hook stage that may rewrite a call's arguments), so the native loop's tool port
and the engines' Tool Gateway apply the same rule through the same executor. A text that already carries this run's
disclosure is left as it is: a re-driven call or a model that copied the footer never doubles it.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final
from urllib.parse import urlencode

from app.harness.write_identity.targets import DisclosureTarget, Markup, target_of
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from harness_core.contracts.generated.enums import WriteAction
    from harness_core.kernel.hooks.middleware.context import ToolCallContext
    from harness_core.kernel.hooks.middleware.pipeline import Middleware, Next

# Product wording (OWD-18 keeps the wording flexible; the agent's name and the session link are what it must carry),
# one sentence per action type.
DISCLOSURE: Final = "Sent via PipesHub agent {agent}"
DISCLOSURES: Final[dict[str, str]] = {
    "email": DISCLOSURE,
    "message": DISCLOSURE,
    "ticket": "Written via PipesHub agent {agent}",
    "document": "Written via PipesHub agent {agent}",
    "event": "Created via PipesHub agent {agent}",
    "commit": "Committed via PipesHub agent {agent}",
}
LINK_LABEL: Final = "View the session"
ASSISTANT: Final = "Assistant"
_LOG: Final = HarnessLog.named("app.harness.write_identity")
# Who a write is from: ``user`` in this build; an edition's port may answer another identity.
type WriteIdentity = str


@dataclass(frozen=True, slots=True)
class WriteIdentitySettings:
    """One run's write identity: every write as the user, disclosed with the agent's name and the session's link.
    An edition's port may answer a subclass that overrides ``identity``, ``sentence_for``, ``co_author`` and
    ``refusal``; the middleware and the pull request tool read nothing else."""

    session_id: str
    agent_name: str = ASSISTANT
    # The web app's public address; ``None``: the disclosure names the session's id without a link.
    app_url: str | None = None
    log: HarnessLog = field(default=_LOG)

    def identity(self, action: WriteAction) -> WriteIdentity:
        return "user"

    @property
    def session_link(self) -> str | None:
        if not self.app_url:
            return None
        return f"{self.app_url.rstrip('/')}/chat?{urlencode({'conversationId': self.session_id})}"

    @property
    def sentence(self) -> str:
        return DISCLOSURE.format(agent=self.agent_name)

    def sentence_for(self, action: WriteAction) -> str:
        return DISCLOSURES[action].format(agent=self.agent_name)

    def co_author(self, action: WriteAction) -> str | None:
        """A trailer a write of ``action`` also carries (a commit's ``Co-authored-by``); none for the user's own."""
        return None

    def refusal(self, action: WriteAction, tool_path: str, what: str | None = None) -> str | None:
        """Why the write at ``tool_path`` may not be sent as chosen, in the person's words (``what``: the kind of write
        when it is not the action type's own); ``None``: it is sent."""
        return None


def render(settings: WriteIdentitySettings, markup: Markup, action: WriteAction = "message") -> str:
    """The disclosure as the target reads it, without the separator that joins it to the text."""
    sentence, link = settings.sentence_for(action), settings.session_link
    match markup:
        case "html":
            anchor = (
                f' · <a href="{html.escape(link, quote=True)}">{html.escape(LINK_LABEL)}</a>'
                if link
                else f" · session {html.escape(settings.session_id)}"
            )
            return f"<p><em>{html.escape(sentence)}</em>{anchor}</p>"
        case "markdown":
            tail = f" · [{LINK_LABEL}]({link})" if link else f" · session {settings.session_id}"
            return f"_{sentence}_{tail}"
        case "slack":
            tail = f" · <{link}|{LINK_LABEL}>" if link else f" · session {settings.session_id}"
            return f"_{sentence}_{tail}"
        case _:
            tail = f" · {LINK_LABEL}: {link}" if link else f" · session {settings.session_id}"
            return f"{sentence}{tail}"


_SEPARATOR: Final[dict[str, str]] = {"html": "", "markdown": "\n\n---\n", "slack": "\n\n", "text": "\n\n— "}


def _markup(target: DisclosureTarget, arguments: Mapping[str, object]) -> Markup:
    if target.markup_switch is None:
        return target.markup
    name, switched = target.markup_switch
    value = arguments.get(name)
    on = value is True or (isinstance(value, str) and value.strip().lower() in ("html", "true"))
    return switched if on else target.markup


def disclosed(settings: WriteIdentitySettings, target: DisclosureTarget, arguments: dict[str, object]) -> bool:
    """Adds the disclosure to ``arguments`` in place; whether it changed them."""
    current = arguments.get(target.field)
    if current is not None and not isinstance(current, str):
        # A structured body (a Jira ADF document) is the connector's to build; it is never rewritten as text.
        return False
    text = current or ""
    if not text.strip() and not target.creates:
        return False
    markup = _markup(target, arguments)
    footer = render(settings, markup, target.action)
    if footer in text:
        return False
    trailer = settings.co_author(target.action)
    if trailer is not None:
        footer = f"{footer}\n\n{trailer}"
    arguments[target.field] = f"{text}{_SEPARATOR[markup]}{footer}" if text.strip() else footer
    return True


def write_identity_middleware(
    settings: WriteIdentitySettings,
    accepts: Callable[[str], frozenset[str] | None] | None = None,
    declared: Callable[[str], str] | None = None,
) -> Middleware[ToolCallContext]:
    """PRE_TOOL_USE: a disclosed write gets its disclosure, a write its settings refuse is denied. ``accepts``:
    the arguments the tool at a path declares; a create's missing text is added only to a tool known to take it.
    ``declared``: the path a tool declares for the path it is registered at, which differs for one of several
    instances of a type (``/tools/slack_1a2b/send_message`` declares ``/tools/slack/send_message``)."""

    async def _middleware(ctx: ToolCallContext, next_fn: Next) -> None:
        target = target_of(declared(ctx.tool_path) if declared is not None else ctx.tool_path)
        if target is None:
            await next_fn()
            return
        identity = settings.identity(target.action)
        refused = settings.refusal(target.action, ctx.tool_path)
        if refused is not None:
            settings.log.warning("write_identity.refused", toolPath=ctx.tool_path, action=target.action)
            ctx.deny(refused)
            return
        if target.field not in ctx.tool_input:
            arguments = accepts(ctx.tool_path) if accepts is not None else None
            if arguments is None or target.field not in arguments:
                await next_fn()
                return
        if disclosed(settings, target, ctx.tool_input):
            settings.log.info(
                "write_identity.disclosed", toolPath=ctx.tool_path, action=target.action, identity=identity
            )
        await next_fn()

    return _middleware


__all__ = [
    "ASSISTANT",
    "DISCLOSURE",
    "DISCLOSURES",
    "LINK_LABEL",
    "WriteIdentitySettings",
    "disclosed",
    "render",
    "write_identity_middleware",
]
