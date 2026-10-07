"""A Host header must not be able to change the path the auth middlewares see.

Both services decide whether to skip authentication from ``request.url.path``.
Starlette used to build that URL by concatenating the Host header and the path,
so ``Host: evil.test/health?`` turned ``/api/v1/x`` into the query string of
``/health`` and the request was let through unauthenticated. The middlewares
themselves are tested in tests/unit/test_connectors_main.py and test_query_main.py.
"""

import pytest

from tests.support.host_header import POISONED_HOSTS, request_with_host


@pytest.mark.parametrize("host", POISONED_HOSTS)
def test_host_header_cannot_replace_the_request_path(host):
    assert request_with_host("/api/v1/x", host).url.path == "/api/v1/x"


def test_path_cannot_replace_the_request_host():
    assert request_with_host("@evil.test/x").url.hostname == "testserver"
