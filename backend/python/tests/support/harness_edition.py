"""The default harness edition, pinned: for shared tests of a port's default, whichever edition the build binds.

``app.edition_services.harness_edition`` is the one place every reader asks (no module binds it by name), so pinning
it there gives each port its default: ``pytestmark = pytest.mark.usefixtures("default_harness_edition")`` after
importing the fixture.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.edition import NO_HARNESS_EDITION

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def default_harness_edition(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("app.edition_services.harness_edition", lambda: NO_HARNESS_EDITION)
    yield
