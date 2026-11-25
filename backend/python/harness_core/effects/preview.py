"""What a held call would do, for its approval card (C3.71, UX-03): a diff of the text it changes and what it costs.

Read from the call's own arguments and the tool's declaration, never from a fetch: building the card must not
itself act. A diff is shown when the arguments name the text before and after (an edit: ``old_string`` /
``new_string`` and the like); a cost when the tool declares one per call (``Tag("cost_usd", "0.04")``). Either may
be absent; a call with neither has no preview, and the card shows its content-first arguments as before.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from pydantic import JsonValue

    from harness_core.tools.base import Tag

# The card shows a diff, not a file: past this the rest is cut and the card says so.
MAX_DIFF_CHARS: Final = 8_000
COST_TAG: Final = "cost_usd"
# (before, after) argument names an edit carries, in the order tools name them.
EDIT_PAIRS: Final = (
    ("old_string", "new_string"),
    ("old_str", "new_str"),
    ("old_text", "new_text"),
    ("old_content", "new_content"),
    ("original", "updated"),
    ("before", "after"),
)
# What names the thing changed, for the diff's header.
_TARGET_KEYS: Final = ("path", "file_path", "filename", "file", "title", "page_title", "name")


@dataclass(frozen=True, slots=True)
class EffectPreview:
    diff: str | None = None
    diff_truncated: bool = False
    cost_usd: float | None = None

    def wire(self) -> dict[str, JsonValue]:
        """The preview as an approval line carries it (camelCase, absent fields left out)."""
        data: dict[str, JsonValue] = {}
        if self.diff is not None:
            data["diff"] = self.diff
            if self.diff_truncated:
                data["diffTruncated"] = True
        if self.cost_usd is not None:
            data["costEstimate"] = {"usd": self.cost_usd, "basis": "declared"}
        return data


def _text(value: JsonValue | None) -> str | None:
    return value if isinstance(value, str) else None


def edit_diff(arguments: Mapping[str, JsonValue]) -> tuple[str, bool] | None:
    """A unified diff of the edit the arguments describe, bounded; ``None`` when they describe none."""
    for before_key, after_key in EDIT_PAIRS:
        before, after = _text(arguments.get(before_key)), _text(arguments.get(after_key))
        if before is None or after is None or before == after:
            continue
        target = next((t for k in _TARGET_KEYS if (t := _text(arguments.get(k)))), "change")
        lines = difflib.unified_diff(
            before.splitlines(), after.splitlines(), fromfile=f"a/{target}", tofile=f"b/{target}", lineterm=""
        )
        text = "".join(f"{line}\n" for line in lines)
        if len(text) <= MAX_DIFF_CHARS:
            return text, False
        return text[:MAX_DIFF_CHARS], True
    return None


def declared_cost(tags: Iterable[Tag]) -> float | None:
    """The tool's own estimate of one call's cost in USD; a malformed or negative one is no estimate."""
    for tag in tags:
        if tag.key != COST_TAG:
            continue
        try:
            cost = float(tag.value)
        except (TypeError, ValueError):
            return None
        return cost if cost >= 0 else None
    return None


def preview_effect(arguments: Mapping[str, JsonValue], tags: Iterable[Tag] = ()) -> EffectPreview | None:
    diff = edit_diff(arguments)
    cost = declared_cost(tags)
    if diff is None and cost is None:
        return None
    return EffectPreview(
        diff=diff[0] if diff is not None else None,
        diff_truncated=diff[1] if diff is not None else False,
        cost_usd=cost,
    )


__all__ = [
    "COST_TAG",
    "EDIT_PAIRS",
    "MAX_DIFF_CHARS",
    "EffectPreview",
    "declared_cost",
    "edit_diff",
    "preview_effect",
]
