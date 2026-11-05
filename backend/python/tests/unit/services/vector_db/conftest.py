from collections.abc import Iterator

import pytest

from tests.support.bound_org import bind_test_org


@pytest.fixture
def bound_org() -> Iterator[str]:
    yield from bind_test_org()
