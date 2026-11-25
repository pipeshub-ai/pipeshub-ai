from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.harness.contracts.generated.pipeshub.policy_data import BUILTIN_COMMANDS

"""Expands a /command into the text a turn sends (SLASH1 D3).

A pure function of (body, declared arguments, typed arguments): no shell, no
file includes, no network. A value is substituted once and never read again,
so `$x` or `/other` inside an argument stays literal and commands never nest.
`contracts/harness/vectors/command-expansion.json` pins this behaviour for
this module and its TypeScript port (`frontend/lib/commands/render.ts`).
"""

# Names a user command may never take (SLASH2 §2.4): every built-in, its aliases, and the reserved words.
RESERVED_COMMAND_NAMES = frozenset(
    [name for command in BUILTIN_COMMANDS.commands for name in (command.name, *(a.root for a in command.aliases))]
    + [word.name for word in BUILTIN_COMMANDS.reserved_words]
)
# Lengths count UTF-16 code units, as the chat routes' `query` limit does.
EXPANDED_MAX_UNITS = 100_000
ARGUMENT_MAX_UNITS = 4_000
MAX_ARGUMENTS = 20

ARGUMENT_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"
# What a person reads when declared arguments are refused (the validator's own text is not for them).
COMMAND_ARGUMENTS_RULE = (
    "The command's arguments can't be saved. Give each a unique name of lowercase letters, digits or _ "
    f"that starts with a letter, at most {MAX_ARGUMENTS} arguments, and a default that is one of its values."
)
_TYPED_RE = re.compile(r"^/([a-z0-9]+(?:-[a-z0-9]+)*)(?:\s+(.*))?$", re.DOTALL)
_TOKEN_RE = re.compile(r'(?:([a-z][a-z0-9_]*)=)?(?:"((?:[^"\\]|\\.)*)"|(\S+))', re.DOTALL)
_ESCAPE_RE = re.compile(r'\\(["\\])')
_PLACEHOLDER_RE = re.compile(r"\$(ARGUMENTS|[1-9]|[a-z][a-z0-9_]*)")


def utf16_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


class CommandArgument(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str = Field(pattern=ARGUMENT_NAME_PATTERN)
    description: str = Field(default="", max_length=200)
    required: bool = False
    enum: list[str] | None = Field(default=None, min_length=1, max_length=50)
    default: str | None = Field(default=None, max_length=ARGUMENT_MAX_UNITS)
    max_length: int | None = Field(default=None, alias="maxLength", ge=1, le=ARGUMENT_MAX_UNITS)

    @model_validator(mode="after")
    def _default_fits(self) -> CommandArgument:
        if self.enum is not None and self.default is not None and self.default not in self.enum:
            raise ValueError(f"default of {self.name!r} is not one of its values")
        return self

    def wire(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_defaults=True)


def parse_arguments(raw: object) -> list[CommandArgument]:
    """A command's declared arguments from frontmatter; ValueError when they are not valid together."""
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_ARGUMENTS:
        raise ValueError(f"arguments must be a list of at most {MAX_ARGUMENTS}")
    items = [CommandArgument.model_validate(item) for item in raw]
    if len({a.name for a in items}) != len(items):
        raise ValueError("argument names must be unique")
    return items


class CommandError(Exception):
    """A command that cannot be expanded as typed; ``code`` is the wire problem code."""

    def __init__(self, code: str, message: str, names: list[str] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.names = names or []
        # Every message here is written for the person who typed the command.
        self.user_message = message


class RenderedCommand(BaseModel):
    text: str
    values: dict[str, str]


def parse_typed(text: str) -> tuple[str, str] | None:
    """`/name rest of line` as (name, rest); None when the text is not a command invocation."""
    match = _TYPED_RE.match(text.strip())
    if match is None:
        return None
    return match.group(1), (match.group(2) or "").strip()


def is_reserved(name: str) -> bool:
    return name in RESERVED_COMMAND_NAMES


def _tokens(raw: str, declared: set[str]) -> tuple[dict[str, str], list[str]]:
    named: dict[str, str] = {}
    positional: list[str] = []
    for match in _TOKEN_RE.finditer(raw):
        key, quoted, bare = match.groups()
        value = _ESCAPE_RE.sub(r"\1", quoted) if quoted is not None else bare
        if key is not None and key in declared:
            named[key] = value
        elif key is not None:
            positional.append(match.group(0))
        else:
            positional.append(value)
    return named, positional


def render_command(body: str, arguments: list[CommandArgument], raw_args: str = "") -> RenderedCommand:
    raw = raw_args.strip()
    if utf16_units(raw) > EXPANDED_MAX_UNITS:
        raise CommandError("command_too_long", "The arguments are too long.")
    declared = {a.name: a for a in arguments}
    named, positional = _tokens(raw, set(declared))

    unfilled = [a.name for a in arguments if a.name not in named]
    values = dict(named)
    for index, name in enumerate(unfilled):
        if index >= len(positional):
            break
        # The last unfilled argument takes every remaining word.
        values[name] = " ".join(positional[index:]) if index == len(unfilled) - 1 else positional[index]

    missing: list[str] = []
    for arg in arguments:
        if arg.name not in values and arg.default is not None:
            values[arg.name] = arg.default
        if arg.name not in values or values[arg.name] == "":
            if arg.required:
                missing.append(arg.name)
            values.setdefault(arg.name, "")
    if missing:
        raise CommandError("command_argument_missing", f"Missing: {', '.join(missing)}.", missing)
    for arg in arguments:
        value = values[arg.name]
        if value and arg.enum is not None and value not in arg.enum:
            raise CommandError(
                "command_argument_invalid", f"{arg.name} must be one of {', '.join(arg.enum)}.", [arg.name]
            )
        if utf16_units(value) > min(arg.max_length or ARGUMENT_MAX_UNITS, ARGUMENT_MAX_UNITS):
            raise CommandError("command_argument_invalid", f"{arg.name} is too long.", [arg.name])

    used = False

    def substitute(match: re.Match[str]) -> str:
        nonlocal used
        key = match.group(1)
        if key == "ARGUMENTS":
            used = True
            return raw
        if key.isdigit():
            used = True
            index = int(key) - 1
            return positional[index] if index < len(positional) else ""
        if key in declared:
            used = True
            return values[key]
        return match.group(0)

    text = _PLACEHOLDER_RE.sub(substitute, body)
    if not used and raw:
        text = f"{body}\n\n{raw}"
    if utf16_units(text) > EXPANDED_MAX_UNITS:
        raise CommandError("command_too_long", "The expanded command is too long to send.")
    return RenderedCommand(text=text, values=values)
