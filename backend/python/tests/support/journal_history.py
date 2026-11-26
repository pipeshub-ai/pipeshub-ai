"""JRN-22: every in-memory journal a test wrote is a valid history when the test ends.

Imported into the harness packages' conftests as an autouse fixture. A test that writes an invalid
history on purpose (an injected event from a newer release, a hand-made gap) says why with
``@pytest.mark.journal_history_unchecked("reason")``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.journal.memory import InMemoryJournal
from harness_core.testing.history import assert_journal_history, journal_events

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def journal_history_check(request: pytest.FixtureRequest) -> Iterator[None]:
    if "journal_history_unchecked" in request.keywords:
        yield
        return
    journals: list[InMemoryJournal] = []
    created = InMemoryJournal.__init__

    def tracked(self: InMemoryJournal, *args: object, **kwargs: object) -> None:
        created(self, *args, **kwargs)  # type: ignore[arg-type]
        journals.append(self)

    # Its own MonkeyPatch, not the test's: the test's would be undone last (see the harness conftest).
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(InMemoryJournal, "__init__", tracked)
        yield
    for journal in journals:
        assert_journal_history(journal_events(journal))
