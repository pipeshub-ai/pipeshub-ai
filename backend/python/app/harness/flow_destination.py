"""Where a PipesHub tool call sends its content, as the approval card names it (UX1).

The flow rule asks a person before an external write; the person decides faster when the card says
"to #sales" than when it shows the arguments. The connector toolsets name their destination with a few
argument names, listed here once; a web search names the search provider its tool id pins. A call whose destination is not named here has none: the card shows the
arguments alone and does not offer "allow for this conversation", because a grant needs a destination it
can be held to.
"""

from __future__ import annotations

import re
from functools import cache
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit

from harness_core.effects.flow import AskDestination, destination_key
from harness_core.security.display_safe import display_safe
from harness_core.security.token_patterns import redact_tokens

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.effects.types import ToolEffect
    from harness_core.kernel.core.messages import ToolCall

# Each argument that says where a call's content goes, in the order the label names them. Every one present
# is part of the grant's key: adding a recipient is a new destination.
DESTINATION_ARGS: Final = (
    "channel",
    "channels",
    "channel_name",
    "channel_id",
    "user",
    "mail_to",
    "to_recipients",
    "to",
    "recipients",
    "mail_cc",
    "cc_recipients",
    "cc",
    "mail_bcc",
    "bcc_recipients",
    "bcc",
    "project_key",
    "space_key",
    "space_id",
    "page_id",
    "parent_page_id",
    "repo",
    "repository",
    "chat_id",
    "team_id",
    "conversation_id",
    "webhook_url",
)
# A request to a URL the model chose (FG2): the card names the site (its registrable domain), and a grant holds for
# that site and its subdomains, never for another site under the same public suffix (``foo.github.io``, ``x.co.uk``).
URL_ARGS: Final = ("url",)
# A web search sends its query to the provider the run's grant pins in the tool id (``web@<provider>/web_search``).
_WEB_SEARCH: Final = re.compile(r"^web@(?P<provider>[^/]+)/web_search$")
_CHANNELS: Final = frozenset({"channel", "channels", "channel_name"})
# The arguments that each name people a message goes to: the label names them as one list (SEC-B-04), so a recipient
# in ``cc`` is on the card as one in ``to`` is.
RECIPIENT_ARGS: Final = frozenset(
    {"mail_to", "to_recipients", "to", "recipients", "mail_cc", "cc_recipients", "cc", "mail_bcc", "bcc_recipients", "bcc"}
)
# Domains of recipients the label has no room for, named after the count.
_MAX_UNSHOWN_DOMAINS: Final = 3
# A Slack conversation id (C…, G…, D…) is shown as it is; a name gets its "#".
_SLACK_ID: Final = re.compile(r"^[CGD][A-Z0-9]{6,}$")
MAX_LABEL_CHARS: Final = 120


def _values(raw: JsonValue) -> list[str]:
    items = raw if isinstance(raw, list) else [raw]
    return [str(item).strip() for item in items if isinstance(item, (str, int)) and str(item).strip()]


def _shown(name: str, value: str) -> str:
    if name in _CHANNELS and not value.startswith("#") and not _SLACK_ID.match(value):
        return f"#{value}"
    return value


def _domain(value: str) -> str:
    local, at, domain = value.rpartition("@")
    return domain.strip().lower() if at and local else ""


def _label(found: Mapping[str, list[str]]) -> str:
    """The destination as the card's title names it (SEC-B-04). Every recipient argument is one list, the least
    common domain first: eight colleagues before ``drop@attacker.example`` cannot push it out of the title. A name is
    shown whole or not at all, and what has no room is counted with the domains it goes to: ``(+6 @corp.example)``."""
    primary_name = next(iter(found))
    names = [n for n in found if n in RECIPIENT_ARGS] if primary_name in RECIPIENT_ARGS else [primary_name]
    values = list(dict.fromkeys(v for n in names for v in found[n]))
    counts = {d: sum(1 for v in values if _domain(v) == d) for d in {_domain(v) for v in values}}
    rank = {v: (counts[_domain(v)] if _domain(v) else len(values) + 1, i) for i, v in enumerate(values)}
    ordered = sorted(values, key=rank.__getitem__)
    others = sum(len(v) for n, v in found.items() if n not in names)
    # Masked before the cut: a clipped token is too short for its pattern and would show in part.
    items = [display_safe(redact_tokens(_shown(primary_name, v))) for v in ordered]

    def suffix(unshown: list[str]) -> str:
        count = len(unshown) + others
        if not count:
            return ""
        domains = list(dict.fromkeys(d for d in map(_domain, unshown) if d))
        named = ", ".join(f"@{display_safe(d)}" for d in domains[:_MAX_UNSHOWN_DOMAINS])
        more = ", …" if len(domains) > _MAX_UNSHOWN_DOMAINS else ""
        return f" (+{count} {named}{more})" if named else f" (+{count})"

    shown = items[:1]
    for item in items[1:]:
        candidate = ", ".join([*shown, item])
        if len(candidate) + len(suffix(ordered[len(shown) + 1 :])) > MAX_LABEL_CHARS:
            break
        shown.append(item)
    return ", ".join(shown) + suffix(ordered[len(shown) :])


def registrable_domain(host: str) -> str:
    """``host``'s site by the public suffix list the release pins (``tld``): ``docs.example.com`` is
    ``example.com``, ``foo.github.io`` stays itself. An address, a single label or an unlisted suffix is its own
    site."""
    from tld import get_fld

    host = host.lower().rstrip(".")
    try:
        domain = get_fld(host, fix_protocol=True, fail_silently=True)
    except ValueError:
        domain = None
    return domain if isinstance(domain, str) and domain else host


def _site(raw: JsonValue) -> list[str]:
    try:
        host = urlsplit(raw).hostname if isinstance(raw, str) else None
    except ValueError:
        return []
    return [registrable_domain(host)] if host else []


@cache
def _search_provider(provider: str) -> str | None:
    """The provider's name as its settings show it; ``None`` for one this build does not know."""
    from app.web.search import PROVIDERS

    spec = PROVIDERS.get(provider)
    return spec.name if spec is not None else None


def _web_search(effect: ToolEffect) -> AskDestination | None:
    match = _WEB_SEARCH.match(effect.tool_id)
    name = _search_provider(match["provider"]) if match is not None else None
    if match is None or name is None:
        return None
    return AskDestination(label=name, key=destination_key(effect.tool_id, f"provider={match['provider']}"))


def pipeshub_destination(call: ToolCall, effect: ToolEffect) -> AskDestination | None:
    # WP-63 part A: a repository fetch goes to one repository, and its grant holds for that repository only, never the
    # whole host its URL names.
    from app.harness.repositories import TOOL_ID, repository_destination

    if effect.tool_id == TOOL_ID:
        return repository_destination(call, effect)
    searched = _web_search(effect)
    if searched is not None:
        return searched
    found = {name: _values(call.arguments[name]) for name in DESTINATION_ARGS if name in call.arguments}
    found |= {name: _site(call.arguments[name]) for name in URL_ARGS if name in call.arguments}
    found = {name: values for name, values in found.items() if values}
    if not found:
        return None
    key_parts = ";".join(f"{name}={','.join(sorted(v.lower() for v in values))}" for name, values in sorted(found.items()))
    return AskDestination(label=_label(found), key=destination_key(effect.tool_id, key_parts))


__all__ = ["DESTINATION_ARGS", "RECIPIENT_ARGS", "URL_ARGS", "pipeshub_destination", "registrable_domain"]
