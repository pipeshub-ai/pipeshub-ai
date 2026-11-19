import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from integration_discovery import discover, main

NEEDS = "NEO4J_IT_URI|ARANGO_IT_URL"


class IntegrationDiscovery(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.base)
        self.write("tests/__init__.py", "")
        self.write("tests/integration/__init__.py", "")

    def write(self, rel: str, text: str) -> None:
        p = self.base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def found(self, **kw: object) -> list[str]:
        return discover(Path("tests/integration"), NEEDS, **kw)

    def test_a_file_that_names_the_store_itself_is_found(self) -> None:
        self.write("tests/integration/test_direct.py", 'URI = os.environ.get("NEO4J_IT_URI")\n')
        self.write("tests/integration/test_unrelated.py", "def test_x(): pass\n")
        self.assertEqual(self.found(), ["tests/integration/test_direct.py"])

    def test_a_file_that_gets_its_store_from_another_test_module_is_found(self) -> None:
        self.write("tests/integration/graph_db/__init__.py", "")
        self.write("tests/integration/graph_db/test_base.py", 'URI = os.environ.get("NEO4J_IT_URI")\n')
        self.write("tests/integration/graph_db/test_alias.py", "from tests.integration.graph_db import test_base as base\n")
        self.write("tests/integration/graph_db/test_from.py", "from tests.integration.graph_db.test_base import Env\n")
        self.write("tests/integration/graph_db/test_plain.py", "import tests.integration.graph_db.test_base\n")
        self.write("tests/integration/graph_db/test_relative.py", "from .test_base import Env\n")
        self.assertEqual(
            self.found(),
            [f"tests/integration/graph_db/test_{n}.py" for n in ("alias", "base", "from", "plain", "relative")],
        )

    def test_imports_are_followed_through_helper_modules_at_any_depth(self) -> None:
        self.write("tests/integration/real_graph.py", 'URI = os.environ.get("ARANGO_IT_URL")\n')
        self.write("tests/integration/world.py", "from tests.integration import real_graph\n")
        self.write("tests/integration/test_deep.py", "from tests.integration.world import make\n")
        self.assertEqual(self.found(), ["tests/integration/test_deep.py"])

    def test_a_conftest_above_the_file_counts_and_one_beside_it_does_not(self) -> None:
        self.write("tests/integration/sim/conftest.py", 'URI = os.environ.get("NEO4J_IT_URI")\n')
        self.write("tests/integration/sim/test_a.py", "def test_x(): pass\n")
        self.write("tests/integration/other/test_b.py", "def test_x(): pass\n")
        self.assertEqual(self.found(), ["tests/integration/sim/test_a.py"])

    def test_a_pattern_the_file_also_needs_through_an_import_excludes_it(self) -> None:
        self.write("tests/integration/stored.py", 'S = os.environ.get("ARTIFACTS_IT_STORAGE_URL")\n')
        self.write("tests/integration/test_both.py", 'U = "NEO4J_IT_URI"\nfrom tests.integration import stored\n')
        self.write("tests/integration/test_graph.py", 'U = "NEO4J_IT_URI"\n')
        self.assertEqual(self.found(not_="ARTIFACTS_IT_STORAGE_URL"), ["tests/integration/test_graph.py"])
        self.assertEqual(self.found(also="ARTIFACTS_IT_STORAGE_URL"), ["tests/integration/test_both.py"])

    def test_an_excluded_directory_is_left_out_with_everything_under_it(self) -> None:
        self.write("tests/integration/sim/test_a.py", 'U = "NEO4J_IT_URI"\n')
        self.write("tests/integration/sim/deep/test_b.py", 'U = "NEO4J_IT_URI"\n')
        self.write("tests/integration/simulated/test_c.py", 'U = "NEO4J_IT_URI"\n')
        self.assertEqual(self.found(exclude_dirs=[Path("tests/integration/sim")]), ["tests/integration/simulated/test_c.py"])

    def test_helper_modules_are_followed_but_not_listed_and_cycles_end(self) -> None:
        self.write("tests/integration/a.py", 'from tests.integration import b\nU = "NEO4J_IT_URI"\n')
        self.write("tests/integration/b.py", "from tests.integration import a\n")
        self.write("tests/integration/test_c.py", "from tests.integration import b\n")
        self.assertEqual(self.found(), ["tests/integration/test_c.py"])

    def test_a_file_that_does_not_parse_is_judged_by_its_own_text(self) -> None:
        self.write("tests/integration/test_broken.py", 'U = "NEO4J_IT_URI"\ndef (:\n')
        self.assertEqual(self.found(), ["tests/integration/test_broken.py"])

    def test_cli_prints_one_path_per_line_and_rejects_an_absolute_root(self) -> None:
        self.write("tests/integration/test_direct.py", 'U = "NEO4J_IT_URI"\n')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["tests/integration", "--needs", NEEDS]), 0)
        self.assertEqual(out.getvalue(), "tests/integration/test_direct.py\n")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main([str(self.base / "tests/integration"), "--needs", NEEDS]), 2)


if __name__ == "__main__":
    unittest.main()
