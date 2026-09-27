"""The shared ``test`` logger must not stay silenced between tests.

``test`` is the parent of every ``test.*`` logger the unit tests hand to the
code under test. Left at CRITICAL, by the ``logger`` fixture or by a module
that sets it at import, it silences all of them for the rest of the session:
caplog then records nothing, and an assertion that nothing was logged passes
whatever the code logs. These run in file order, so the second sees what the
first, and every module collected before it, left behind.
"""

import logging


def test_the_logger_fixture_is_silent_while_it_is_in_use(logger) -> None:
    assert logger.name == "test"
    assert not logger.isEnabledFor(logging.ERROR)


def test_a_later_test_sees_what_a_test_logger_logs(caplog) -> None:
    child = logging.getLogger("test.logger_isolation")
    with caplog.at_level(logging.WARNING):
        child.warning("the code under test warned")
    assert logging.getLogger("test").level == logging.NOTSET
    assert [r.getMessage() for r in caplog.records] == ["the code under test warned"]
