import sys

import pytest


@pytest.fixture(autouse=True)
def _fresh_connector_name_cache() -> None:
    """Connector names are cached per process; a test must not see another's."""
    chat_helpers = sys.modules.get("app.utils.chat_helpers")
    if chat_helpers is not None:
        chat_helpers._connector_name_cache.clear()
        chat_helpers._connector_name_misses.clear()
