"""The harness's extension point for edition ports: ``app.edition_services.harness_edition`` and
``harness_core.edition``. This build supplies no port, so every read answers its default."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol, runtime_checkable

import pytest

from app import edition_services
from harness_core.edition import NO_HARNESS_EDITION, HarnessEdition


class Greeter(Protocol):
    def greet(self) -> str: ...


@runtime_checkable
class Counter(Protocol):
    def count(self) -> int: ...


class Base:
    pass


class Derived(Base):
    pass


class _Hello:
    def greet(self) -> str:
        return "hello"


class _Ten:
    def count(self) -> int:
        return 10


DEFAULT_GREETER = _Hello()


def test_the_default_edition_supplies_no_port_and_the_build_supplies_an_edition() -> None:
    # Edition-blind: a build may bind its own edition; the shared suite checks the default and the seam's type.
    assert dict(NO_HARNESS_EDITION.ports) == {}
    assert isinstance(edition_services.harness_edition(), HarnessEdition)


def test_no_module_binds_the_edition_by_name_at_import() -> None:
    """``app.edition_services.harness_edition`` is the one place to pin it: a module-level ``from … import`` of it
    would keep the build's edition past a test's pin (``tests/support/harness_edition.py``)."""
    root = Path(__file__).resolve().parents[3]
    bound = sorted(
        str(path.relative_to(root))
        for top in ("app", "harness_core", "harness_engines")
        for path in (root / top).rglob("*.py")
        if re.search(r"^from app\.edition_services import .*\bharness_edition\b", path.read_text(encoding="utf-8"), re.M)
    )
    assert bound == []


def test_every_read_answers_its_default() -> None:
    assert edition_services.harness_edition().port(Greeter, DEFAULT_GREETER) is DEFAULT_GREETER  # type: ignore[type-abstract]


def test_a_supplied_port_replaces_the_default() -> None:
    supplied = _Ten()
    edition = HarnessEdition({Counter: supplied})
    assert edition.port(Counter, _Ten()) is supplied  # type: ignore[type-abstract]
    assert edition.port(Greeter, DEFAULT_GREETER) is DEFAULT_GREETER  # type: ignore[type-abstract]


def test_a_class_port_takes_a_subclass_and_refuses_anything_else() -> None:
    derived = Derived()
    assert HarnessEdition({Base: derived}).port(Base, Base()) is derived
    with pytest.raises(TypeError, match="does not implement"):
        HarnessEdition({Base: object()})


def test_a_runtime_checkable_port_is_checked() -> None:
    with pytest.raises(TypeError, match="does not implement"):
        HarnessEdition({Counter: _Hello()})


def test_a_plain_protocol_port_is_left_to_the_type_checker() -> None:
    supplied = _Hello()
    assert HarnessEdition({Greeter: supplied}).port(Greeter, DEFAULT_GREETER) is supplied  # type: ignore[type-abstract]


def test_none_is_refused_rather_than_read_as_the_default() -> None:
    with pytest.raises(TypeError, match="leave it out"):
        HarnessEdition({Base: None})


def test_the_ports_cannot_change_after_the_edition_is_built() -> None:
    given = {Base: Derived()}
    edition = HarnessEdition(given)
    given[Base] = Base()
    assert type(edition.port(Base, Base())) is Derived
    with pytest.raises(TypeError):
        edition.ports[Base] = Base()  # type: ignore[index]


def test_a_feature_with_no_default_reads_none_until_an_edition_supplies_it() -> None:
    assert edition_services.harness_edition().supplied(Counter) is None  # type: ignore[type-abstract]
    supplied = _Ten()
    assert HarnessEdition({Counter: supplied}).supplied(Counter) is supplied  # type: ignore[type-abstract]
