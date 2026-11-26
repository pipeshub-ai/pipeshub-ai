"""Let a slow file's independent tests spread over the xdist workers under `--dist loadfile`."""

import pytest

# `--dist loadfile` sends a whole file to one worker. These files' tests share no state, and together they take
# longer than the rest of their unit-test.yml shard on one worker, so each of their tests is a work unit of its own.
PER_TEST_FILES = frozenset({
    # ~150 schemas, 10-15 min on one CI worker: the harness-contracts shard's floor, past its job's 30-minute limit.
    "tests/unit/contracts_harness/test_c4_generative.py",
})


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_make_scheduler(config: pytest.Config, log: object) -> object | None:
    if config.getvalue("dist") != "loadfile":
        return None
    from xdist.scheduler import LoadFileScheduling

    class LoadFileExceptPerTestFiles(LoadFileScheduling):
        def _split_scope(self, nodeid: str) -> str:
            scope = super()._split_scope(nodeid)
            return nodeid if scope in PER_TEST_FILES else scope

    return LoadFileExceptPerTestFiles(config, log)
