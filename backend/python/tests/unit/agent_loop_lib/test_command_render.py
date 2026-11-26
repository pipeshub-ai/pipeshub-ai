"""SLASH1 D3: the /command renderer against the shared vectors (the TypeScript preview reads the same file)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agent_loop_lib.commands.base import Command
from app.agent_loop_lib.commands.registry import CommandRegistry
from app.agent_loop_lib.commands.render import (
    ARGUMENT_MAX_UNITS,
    EXPANDED_MAX_UNITS,
    RESERVED_COMMAND_NAMES,
    CommandError,
    parse_arguments,
    parse_typed,
    render_command,
)

VECTORS = json.loads(
    (Path(__file__).resolve().parents[5] / "contracts/harness/vectors/command-expansion.json").read_text("utf-8")
)


def _args(case: dict) -> str:
    repeat = case.get("argsRepeat")
    return repeat["text"] * repeat["times"] if repeat else case["args"]


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda c: c["id"])
def test_vector(case: dict) -> None:
    arguments = parse_arguments(case["arguments"])
    if "error" in case:
        with pytest.raises(CommandError) as exc:
            render_command(case["body"], arguments, _args(case))
        assert exc.value.code == case["error"]["code"]
        assert exc.value.names == case["error"]["names"]
        return
    rendered = render_command(case["body"], arguments, _args(case))
    assert rendered.text == case["expected"]
    assert rendered.values == case["values"]


@pytest.mark.parametrize("case", VECTORS["typed"], ids=lambda c: repr(c["text"]))
def test_typed(case: dict) -> None:
    parsed = parse_typed(case["text"])
    expected = case["expected"]
    assert parsed == (None if expected is None else (expected["name"], expected["args"]))


REGISTRY = json.loads(
    (Path(__file__).resolve().parents[5] / "contracts/harness/ext/pipeshub/commands/builtin-commands.json").read_text("utf-8")
)
# SLASH1's hand-written list: SLASH2 may add names, never free one a user command could then take.
SLASH1_RESERVED = {"compact", "clear", "model", "config", "effort", "init", "help", "new", "review", "login", "mcp"}


def test_reserved_names_are_the_registry() -> None:
    expected = {n for c in REGISTRY["commands"] for n in (c["name"], *c["aliases"])}
    expected |= {w["name"] for w in REGISTRY["reservedWords"]}
    assert expected == RESERVED_COMMAND_NAMES
    assert SLASH1_RESERVED <= RESERVED_COMMAND_NAMES
    assert {"status", "usage", "cost", "stop", "fork", "exit", "logout"} <= RESERVED_COMMAND_NAMES


def test_limits_match_the_vectors() -> None:
    assert VECTORS["limits"] == {"expandedMaxUnits": EXPANDED_MAX_UNITS, "argumentMaxUnits": ARGUMENT_MAX_UNITS}


@pytest.mark.parametrize(
    "raw",
    [
        [{"name": "Bad"}],
        [{"name": "a"}, {"name": "a"}],
        [{"name": "a", "enum": ["x"], "default": "y"}],
        [{"name": "a", "shell": "!ls"}],
        "not a list",
    ],
)
def test_invalid_declarations_are_refused(raw: object) -> None:
    with pytest.raises(ValueError):
        parse_arguments(raw)


def test_cli_registry_keeps_its_arguments_behaviour() -> None:
    registry = CommandRegistry()
    registry.register(Command(name="fix", body="Fix: $ARGUMENTS"))
    registry.register(Command(name="plain", body="Plain."))
    assert registry.render("fix", "the build") == "Fix: the build"
    assert registry.render("plain", "extra words") == "Plain.\n\nextra words"
    assert registry.render("plain") == "Plain."
