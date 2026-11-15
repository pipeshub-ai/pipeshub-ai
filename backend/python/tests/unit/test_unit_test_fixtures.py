"""The unit conftest gives every unit test the fixtures ``unit_test_fixtures()`` returns."""

from pathlib import Path

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


def test_a_returned_name_may_not_replace_a_conftest_fixture(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    @pytest.fixture
    def frozen_heap() -> None:
        return None

    monkeypatch.setattr(
        edition_defaults, "unit_test_fixtures", lambda: {"frozen_heap": frozen_heap}
    )

    result = _run_unit_conftest(pytester, "def test_probe():\n    pass\n")

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    assert "redefines names in this conftest: ['frozen_heap']" in result.stderr.str()
