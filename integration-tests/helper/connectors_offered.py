"""Connector types a downstream build leaves out of its registry.

A build of the product may not offer every connector this repository has. Its
suite extension (``helper/suite_extensions.py``) names them in
``connectors_not_offered.py``: ``NOT_OFFERED`` maps the connector type, as the
registry names it, to ``(markers of its suites, why it is not offered)``.

Their suites are skipped when collected, and a test that creates one of them
elsewhere (a suite that uses it as its sample source) is skipped where it asks
for it, rather than failing on a connector type the stack does not have.
Without an extension every connector is offered.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from helper import suite_extensions


def not_offered(root: Path = suite_extensions.ROOT) -> dict[str, tuple[tuple[str, ...], str]]:
    """Connector type -> (markers of its suites, why it is not offered)."""
    return suite_extensions.add_items(
        {}, "NOT_OFFERED", suite_extensions.modules("connectors_not_offered", root)
    )


def skipped_markers(root: Path = suite_extensions.ROOT) -> dict[str, str]:
    """Marker -> why the suites carrying it are skipped."""
    return {
        marker: f"This build does not offer the {connector_type} connector: {why}"
        for connector_type, (markers, why) in not_offered(root).items()
        for marker in markers
    }


def skip_if_not_offered(connector_type: str, root: Path = suite_extensions.ROOT) -> None:
    entry = not_offered(root).get(connector_type)
    if entry is not None:
        pytest.skip(f"This build does not offer the {connector_type} connector: {entry[1]}")
