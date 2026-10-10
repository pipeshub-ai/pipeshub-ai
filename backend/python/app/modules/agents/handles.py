"""Agent @mention handles: slug rules, reserved words and collision candidates.

Pure: no I/O besides loading the shared reserved-alias file at import.
"""

import json
import re
import unicodedata
from pathlib import Path
from typing import Final

_ALIASES_FILE: Final = Path(__file__).resolve().parents[2] / "config" / "reserved_mention_aliases.json"

_ALIASES: Final[dict[str, list[str]]] = json.loads(_ALIASES_FILE.read_text(encoding="utf-8"))

# `assistant` aliases address the default assistant; `inert` words never resolve to a mention.
ASSISTANT_ALIASES: Final[tuple[str, ...]] = tuple(_ALIASES["assistant"])
RESERVED: Final[frozenset[str]] = frozenset(_ALIASES["assistant"]) | frozenset(_ALIASES["inert"])

HANDLE_PATTERN: Final = re.compile(r"^[a-z0-9-]{2,40}$")
MAX_LENGTH: Final = 40
MAX_SUFFIX: Final = 99
# Leaves room for "-99" so a suffixed candidate still fits MAX_LENGTH.
_MAX_BASE: Final = MAX_LENGTH - len(f"-{MAX_SUFFIX}")
_FALLBACK_BASE: Final = "new-agent"


def slugify(name: str) -> str:
    """Lower-case ASCII slug of `name`, never reserved, always a valid handle."""
    folded = unicodedata.normalize("NFKD", name or "")
    ascii_only = folded.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only).strip("-")[:_MAX_BASE].strip("-")
    if not slug:
        return _FALLBACK_BASE
    if len(slug) < 2:
        slug = f"{slug}-agent"
    if slug in RESERVED:
        slug = f"{slug}-agent"
    return slug


def next_candidate(base: str, n: int) -> str:
    """`base` for n <= 1, else `base-n`, trimmed so the result fits MAX_LENGTH."""
    if n <= 1:
        return base
    if n > MAX_SUFFIX:
        raise ValueError(f"no candidates left for {base!r} beyond -{MAX_SUFFIX}")
    suffix = f"-{n}"
    return f"{base[: MAX_LENGTH - len(suffix)].rstrip('-')}{suffix}"


def is_valid_format(handle: str) -> bool:
    return bool(HANDLE_PATTERN.fullmatch(handle))


def is_reserved(handle: str) -> bool:
    return handle in RESERVED


def first_free_candidate(base: str, taken: set[str], start: int = 1) -> str | None:
    for n in range(max(start, 1), MAX_SUFFIX + 1):
        candidate = next_candidate(base, n)
        if candidate not in taken:
            return candidate
    return None
