"""``app/telemetry_stub`` replaces ``app/telemetry`` in a build that leaves telemetry out.
Every name the rest of the app takes from the package must exist there with the same parameters."""

import ast
import importlib
import inspect
from pathlib import Path
from types import ModuleType

import pytest

APP = Path(__file__).resolve().parents[3] / "app"
PREFIX = "app.telemetry"


def _used_names() -> dict[str, set[str]]:
    """module -> names that code outside the package imports from it or reads off an alias."""
    used: dict[str, set[str]] = {}
    for path in APP.rglob("*.py"):
        if "telemetry" in path.relative_to(APP).parts[0]:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if (
                not isinstance(node, ast.ImportFrom)
                or not node.module
                or not node.module.startswith(PREFIX)
            ):
                continue
            for a in node.names:
                if node.module == f"{PREFIX}.modules" or node.module == PREFIX:
                    aliases[a.asname or a.name] = f"{node.module}.{a.name}"
                else:
                    used.setdefault(node.module, set()).add(a.name)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in aliases
            ):
                used.setdefault(aliases[node.value.id], set()).add(node.attr)
            if isinstance(node, ast.Name) and node.id in aliases:
                used.setdefault(aliases[node.id], set())
    return used


def _load(real: bool, module: str) -> ModuleType:
    return importlib.import_module(
        module.replace(PREFIX, "app.telemetry" if real else "app.telemetry_stub", 1)
    )


USED = _used_names()


@pytest.mark.parametrize("module", sorted(USED))
def test_stub_covers_what_the_app_uses(module: str) -> None:
    real, stub = _load(True, module), _load(False, module)
    for name in sorted(USED[module]):
        assert hasattr(stub, name), f"{module}.{name} is missing from the stub"
        r, s = getattr(real, name), getattr(stub, name)
        if inspect.isfunction(r):
            rp = [
                (p.name, p.kind, p.default)
                for p in inspect.signature(r).parameters.values()
            ]
            sp = [
                (p.name, p.kind, p.default)
                for p in inspect.signature(s).parameters.values()
            ]
            assert sp == rp, f"{module}.{name} takes different parameters"


def test_stub_bind_keeps_the_pusher() -> None:
    from fastapi import FastAPI

    from app.telemetry_stub.setup import setup_telemetry

    pending = setup_telemetry(FastAPI(), "test")
    bound = pending.bind(object(), object())
    assert pending.pusher is bound
