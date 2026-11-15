"""The unit conftest gives every unit test the fixtures ``unit_test_fixtures()`` returns."""

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.support import edition_defaults

pytest_plugins = ("pytester",)

_UNIT_CONFTEST = Path(__file__).with_name("conftest.py")


def _run_unit_conftest(pytester: pytest.Pytester, test_source: str) -> pytest.RunResult:
    # A copy of the real conftest, so the binding under test is the one every unit test gets.
    pytester.makeconftest(_UNIT_CONFTEST.read_text(encoding="utf-8"))
    pytester.makepyfile(test_probe=test_source)
    return pytester.runpytest_inprocess("-p", "no:cacheprovider")


def test_a_returned_fixture_is_found_by_name(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    @pytest.fixture
    def added_value() -> str:
        return "from the hook"

    monkeypatch.setattr(
        edition_defaults, "unit_test_fixtures", lambda: {"added_value": added_value}
    )

    result = _run_unit_conftest(
        pytester,
        "def test_probe(added_value):\n    assert added_value == 'from the hook'\n",
    )

    result.assert_outcomes(passed=1)


def test_a_returned_autouse_fixture_runs_for_every_test(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    @pytest.fixture(autouse=True)
    def added_autouse(request: pytest.FixtureRequest) -> None:
        calls.append(request.node.name)

    monkeypatch.setattr(
        edition_defaults, "unit_test_fixtures", lambda: {"added_autouse": added_autouse}
    )

    result = _run_unit_conftest(
        pytester, "def test_one():\n    pass\n\ndef test_two():\n    pass\n"
    )

    result.assert_outcomes(passed=2)
    assert calls == ["test_one", "test_two"]


def _a_fixture(name: str | None = None) -> object:
    # Built per test: a fixture defined at module level would also be one of this module's.
    @pytest.fixture(name=name)
    def probe() -> None:
        return None

    return probe


def _an_older_pytest_fixture(name: str) -> object:
    # The shape ``@pytest.fixture(name=...)`` gives a function under pytest < 8.4.
    def probe() -> None:
        return None

    probe._pytestfixturefunction = SimpleNamespace(name=name)  # type: ignore[attr-defined]
    return probe


# pytest registers a fixture under its decorator's ``name=`` when it has one, else under
# the attribute it is bound to; either way it must not take a name already registered.
@pytest.mark.parametrize(
    ("returned", "clash"),
    [
        pytest.param(lambda: {"frozen_heap": _a_fixture()}, "frozen_heap", id="by-key"),
        pytest.param(
            lambda: {"z_heap": _a_fixture("frozen_heap")},
            "frozen_heap",
            id="by-decorator-name",
        ),
        pytest.param(
            lambda: {"z_heap": _an_older_pytest_fixture("frozen_heap")},
            "frozen_heap",
            id="by-decorator-name-older-pytest",
        ),
        pytest.param(
            lambda: {"first": _a_fixture("added"), "second": _a_fixture("added")},
            "added",
            id="twice-in-the-hook",
        ),
    ],
)
def test_a_returned_fixture_may_not_take_a_registered_name(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    returned: Callable[[], dict[str, object]],
    clash: str,
) -> None:
    monkeypatch.setattr(edition_defaults, "unit_test_fixtures", returned)

    result = _run_unit_conftest(pytester, "def test_probe():\n    pass\n")

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    assert f"redefines names in this conftest: ['{clash}']" in result.stderr.str()
