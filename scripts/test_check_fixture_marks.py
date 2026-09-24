"""A mark on a fixture is caught before pytest 9.1 turns it into a run-wide error.

Run: python3 -m unittest discover -s scripts -p 'test_*.py'
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_fixture_marks as check  # noqa: E402


class TestMarkedFixtures(unittest.TestCase):
    def test_it_finds_a_marked_fixture(self) -> None:
        source = (
            "import pytest\n"
            "@pytest.mark.skip(reason='later')\n"
            "@pytest.fixture\n"
            "def thing():\n"
            "    return 1\n"
        )
        self.assertEqual(check.marked_fixtures(source), [(2, "thing", "skip")])

    def test_it_finds_one_under_an_async_fixture(self) -> None:
        source = (
            "import pytest, pytest_asyncio\n"
            "@pytest.mark.skipif(True, reason='later')\n"
            "@pytest_asyncio.fixture(scope='module')\n"
            "async def thing():\n"
            "    yield 1\n"
        )
        self.assertEqual(check.marked_fixtures(source), [(2, "thing", "skipif")])

    def test_a_mark_on_a_test_is_fine(self) -> None:
        source = (
            "import pytest\n"
            "@pytest.mark.skip(reason='later')\n"
            "def test_thing():\n"
            "    pass\n"
        )
        self.assertEqual(check.marked_fixtures(source), [])

    def test_a_fixture_without_marks_is_fine(self) -> None:
        source = (
            "import pytest\n"
            "@pytest.fixture\n"
            "def thing():\n"
            "    pytest.skip('the supported way')\n"
        )
        self.assertEqual(check.marked_fixtures(source), [])

    def test_it_reports_every_mark_on_one_fixture(self) -> None:
        source = (
            "import pytest\n"
            "@pytest.mark.skip(reason='later')\n"
            "@pytest.mark.slow\n"
            "@pytest.fixture\n"
            "def thing():\n"
            "    return 1\n"
        )
        self.assertEqual(
            [mark for _, _, mark in check.marked_fixtures(source)], ["skip", "slow"]
        )


class TestTheTestTreesAreClean(unittest.TestCase):
    def test_no_fixture_in_the_repo_carries_a_mark(self) -> None:
        hits = check.scan()
        detail = "\n".join(
            f"  {path}:{line} @pytest.mark.{mark} on fixture '{name}'"
            for path, line, name, mark in hits
        )
        self.assertEqual(
            hits,
            [],
            "A mark on a fixture does nothing, and pytest 9.1 makes it a "
            "collection error that fails the whole run. Call pytest.skip(reason) "
            f"in the fixture body instead.\n{detail}",
        )


if __name__ == "__main__":
    unittest.main()
