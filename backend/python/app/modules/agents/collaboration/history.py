import re
import unicodedata
from collections.abc import Iterable

from app.modules.agents.collaboration.mentions import MentionRef, render_mentions
from app.modules.agents.collaboration.models import PreviousConversationTurn

MAX_DISPLAY_NAME_LEN = 64
FALLBACK_DISPLAY_NAME = "Participant"

# Same rules as Node's `sanitizeDisplayName` (participant-roster.ts); both run
# backend/nodejs/apps/tests/fixtures/collaboration/display-names.json.
_STRIPPED_CHARS = re.compile(r"[\[\]<>]")
_SPACES = re.compile(r" +")
_EMAIL_LIKE = re.compile(r"\S+@\S+")


def sanitize_display_name(name: str | None) -> str:
    text = unicodedata.normalize("NFKC", name or "")
    text = "".join(
        " " if ch.isspace() else ch
        for ch in text
        if ch.isspace() or unicodedata.category(ch)[0] != "C"
    )
    text = _STRIPPED_CHARS.sub("", text)
    text = _SPACES.sub(" ", text).strip()
    if not text or _EMAIL_LIKE.search(text):
        return FALLBACK_DISPLAY_NAME
    return text[:MAX_DISPLAY_NAME_LEN].strip()


def _opens_with_bracket(line: str) -> bool:
    # NFKC folds fullwidth brackets; category Ps also catches lookalikes such as 【 or 〔.
    head = unicodedata.normalize("NFKC", line).lstrip()
    return bool(head) and unicodedata.category(head[0]) == "Ps"


def escape_leading_bracket(text: str) -> str:
    """Escapes every line that opens with a bracket, so no line of a message can
    pass for a `[participant_n]:` label of its own."""
    return "".join(
        "\\" + line if _opens_with_bracket(line) else line for line in text.splitlines(keepends=True)
    )


def render_user_turn(content: str, author_ref: str | None, *, collaborative: bool) -> str:
    if not collaborative:
        return content
    escaped = escape_leading_bracket(content)
    return f"[{author_ref}]: {escaped}" if author_ref else escaped


def render_note_turn(
    content: str,
    author_ref: str | None,
    mentions: list[MentionRef] | None = None,
) -> str:
    """A note between participants, shown to the model as information that was not addressed to it
    (D13). The body is escaped like any other participant text; the label and addressee line are
    built from roster refs only."""
    label = f"[{author_ref}] (note)" if author_ref else "(note from another participant)"
    rendered = f"{label}: {escape_leading_bracket(content)}"
    addressed = render_mentions(mentions, {})
    return f"{rendered}\n(addressed to {addressed})" if addressed else rendered


def turns_to_dicts(turns: Iterable[PreviousConversationTurn]) -> list[dict]:
    """`exclude_unset` keeps absent keys absent, so solo payloads round-trip unchanged."""
    return [t.model_dump(exclude_unset=True) for t in turns]
