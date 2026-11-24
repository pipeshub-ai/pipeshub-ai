"""Every ``platform_events.emit`` in the application names a catalogued event and only its fields.

A typo in an event name or a field would otherwise be silent: nobody would be listening.
"""

import ast
from pathlib import Path

import pytest

from app.platform_events import catalog

APP = Path(__file__).resolve().parents[3] / "app"


def _is_emit(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "emit"
        and isinstance(node.value, ast.Name)
        and node.value.id == "platform_events"
    )


def _emits() -> list[tuple[str, int, list[ast.expr], list[ast.keyword]]]:
    """Every emit as (file, line, positional arguments, keywords).

    Also an emit handed to something that calls it later, with its arguments following it:
    ``weakref.finalize(obj, platform_events.emit, "name", field=...)``.
    """
    found = []
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        where = str(path.relative_to(APP.parent))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _is_emit(node.func):
                found.append((where, node.lineno, node.args, node.keywords))
            for index, argument in enumerate(node.args):
                if _is_emit(argument):
                    found.append((where, node.lineno, node.args[index + 1:], node.keywords))
    return found


EMITS = _emits()


def test_the_application_emits_events() -> None:
    assert len(EMITS) > 100


@pytest.mark.parametrize(("path", "line", "args", "keywords"), EMITS, ids=[f"{p}:{n}" for p, n, _a, _k in EMITS])
def test_emit_names_a_catalogued_event_and_its_fields(path: str, line: int, args: list, keywords: list) -> None:
    assert args and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str), (
        "the event name must be a string literal, so that it can be checked"
    )
    assert len(args) == 1, "facts are passed by keyword"
    name = args[0].value
    assert name in catalog.EVENTS, f"{name!r} is not in app/platform_events/catalog.py"
    for keyword in keywords:
        assert keyword.arg is not None, "no **payload: the fields must be visible at the call site"
        assert keyword.arg in catalog.EVENTS[name].fields, f"{name!r} has no field {keyword.arg!r}"


def test_every_catalogued_event_is_emitted_somewhere() -> None:
    emitted = {args[0].value for _p, _n, args, _k in EMITS if args and isinstance(args[0], ast.Constant)}
    assert sorted(set(catalog.EVENTS) - emitted) == []


def test_names_are_dot_separated_product_terms_and_fields_are_described() -> None:
    for name, event in catalog.EVENTS.items():
        assert name == event.name
        parts = name.split(".")
        assert len(parts) >= 2 and all(part and part.replace("_", "").isalnum() and part == part.lower() for part in parts), name
        assert event.summary.strip(), name
        for field in event.fields:
            assert field in catalog.FIELDS, f"{name}: field {field!r} has no description in catalog.FIELDS"
            assert field.isidentifier() and field == field.lower()
    used = {field for event in catalog.EVENTS.values() for field in event.fields}
    assert sorted(set(catalog.FIELDS) - used) == [], "a described field no event carries"


def test_no_name_or_field_speaks_of_how_it_is_measured() -> None:
    """Payloads carry product facts; metric names, label sets and handles belong to listeners."""
    banned = ("metric", "label", "counter", "gauge", "histogram", "prometheus", "handle", "timer")
    for name, event in catalog.EVENTS.items():
        for word in banned:
            assert word not in name, name
            assert all(word not in field for field in event.fields), (name, event.fields)


def test_a_misspelt_event_or_field_is_caught() -> None:
    assert "search.preformed" not in catalog.EVENTS
    assert "org" not in catalog.EVENTS["search.performed"].fields


def test_the_telemetry_plugin_listens_only_to_catalogued_events() -> None:
    from app.plugins.telemetry import listeners

    assert sorted(set(listeners.handlers()) - set(catalog.EVENTS)) == []
