"""Remember pytest's status for the xdist force-exit in conftest.

pytest_unconfigure calls os._exit, which drops the status unless
pytest_sessionfinish stored it first. A configure failure never reaches
this hook, so pytest returns that status itself.
"""


def pytest_sessionfinish(session, exitstatus: int) -> None:
    session.config._exitcode = int(exitstatus)
