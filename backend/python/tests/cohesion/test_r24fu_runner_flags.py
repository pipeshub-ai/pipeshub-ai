"""R24-FU: no runner of the Python suites turns pytest's warnings plugin off.

``-p no:warnings`` unregisters the ``filterwarnings`` marker with the plugin, and ``pytest.ini``'s
``--strict-markers`` then fails the collection of every module that uses it (the messaging worker-loop suites did,
under ``scripts/verify.sh``). ``--disable-warnings`` hides the summary and keeps the plugin.
"""

from __future__ import annotations

import re

from tests.cohesion.support import REPO_ROOT

RUNNERS = (
    *sorted((REPO_ROOT / "scripts").glob("*.sh")),
    *sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")),
    *sorted((REPO_ROOT / "deployment" / "e2e").glob("*.sh")),
)
NO_WARNINGS = re.compile(r"-p\s*no:warnings\b")


def test_r24fu_no_runner_unregisters_the_filterwarnings_marker() -> None:
    assert RUNNERS, "no runner scripts found"
    offenders = [str(path.relative_to(REPO_ROOT)) for path in RUNNERS if NO_WARNINGS.search(path.read_text("utf-8"))]
    assert offenders == [], f"use --disable-warnings, not -p no:warnings: {offenders}"


def test_r24fu_the_marker_that_needs_the_plugin_is_in_use() -> None:
    """The guard above matters while a suite marks a test with ``filterwarnings``."""
    messaging = REPO_ROOT / "backend" / "python" / "tests" / "unit" / "services" / "messaging"
    assert any("mark.filterwarnings" in path.read_text("utf-8") for path in messaging.glob("test_*worker_loop.py"))
