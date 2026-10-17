"""Which registered connectors the coverage gate counts.

Run: python3 -m unittest discover -s scripts -p 'test_*.py'
"""

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import connector_coverage as cov  # noqa: E402

PLACEHOLDERS = {"airtable", "calendar", "docs", "forms", "meet", "slides", "zendesk"}


class TestRegisteredConnectorsInThisRepo(unittest.TestCase):
    def test_placeholders_without_sync_are_not_counted(self) -> None:
        self.assertFalse(PLACEHOLDERS & set(cov.registered_connectors()))

    def test_real_connectors_sharing_a_placeholder_name_are_counted(self) -> None:
        # registry/connector.py also defines LinearConnector and SlackConnector
        # placeholders; the factory registers the real ones from sources/.
        registry = cov.registered_connectors()
        for key in ("linear", "slack", "slackworkspace"):
            self.assertIn(key, registry)


class TestRegisteredConnectorsFollowTheImport(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)

    def write(self, rel: str, body: str) -> Path:
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body), encoding="utf-8")
        return path

    def test_only_the_imported_placeholder_is_skipped(self) -> None:
        self.write("backend/python/app/placeholders.py", """
            @ConnectorBuilder("Echo")\\
                .with_sync_support(False)
            class EchoConnector:
                pass

            @ConnectorBuilder("Real")\\
                .with_sync_support(False)
            class RealConnector:
                pass
            """)
        self.write("backend/python/app/real.py", """
            @ConnectorBuilder("Real")\\
                .with_sync_support(True)
            class RealConnector:
                pass
            """)
        factory = self.write("backend/python/app/factory.py", """
            from app.placeholders import (
                EchoConnector,
            )
            from app.real import RealConnector

            class ConnectorFactory:
                _connector_registry = {
                    'real': RealConnector,
                }

                _beta_connector_definitions = {
                    'echo': EchoConnector,
                }
            """)
        with mock.patch.object(cov, "REPO", self.repo), mock.patch.object(cov, "FACTORY", factory):
            self.assertEqual(cov.registered_connectors(), {"real": "RealConnector"})


if __name__ == "__main__":
    unittest.main()
