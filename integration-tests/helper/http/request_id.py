"""Tag every outbound test request with ``x-request-id`` for cross-service tracing.

The Python twin of the frontend's ``lib/utils/request-id.ts`` + axios request
interceptor: a fresh id per request, so a failing test can be followed through the
Node and Python logs. The suite makes most of its calls through module-level
``requests.post(...)`` rather than a shared client, so injection hooks
``Session.prepare_request`` -- the one point every ``requests`` call passes through --
instead of touching ~200 call sites.

Sanitizing and the 64-char cap come from the server's own ``sanitize_root_id``, so the
id logged here is byte-for-byte the id the backend logs.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

import requests

from app.utils.request_context import (  # type: ignore[import-not-found]
    HEADER_REQUEST_ID,
    sanitize_root_id,
)

TEST_ID_PREFIX = "it-"

# 64 (server cap) - len("it-") - len("-") - 8 hex, so truncation never eats the
# random suffix that makes each id unique.
_MAX_SLUG_LEN = 52

_NO_TEST_SLUG = "session"

_current_slug: str = _NO_TEST_SLUG


def slug_for_nodeid(nodeid: str) -> str:
    """Stable per-test part of the id: ``Class.test_func`` (or just ``test_func``).

    Pure, so the HTML report can recompute the exact prefix a worker sent from the
    node id alone.

    ``file.py::TestKB::test_create[case-1]`` → ``TestKB.test_create.case-1``
    """
    # Under `--dist loadgroup` xdist appends `@<group>` to the node id.
    base = nodeid.split("@", 1)[0]
    segments = base.split("::")[1:] or [base.rsplit("/", 1)[-1]]
    # `test_sync[case-1]` -> `test_sync.case-1`; the sanitizer would drop the
    # brackets and run the params into the name.
    joined = ".".join(segments).replace("[", ".").replace("]", "")
    return (sanitize_root_id(joined) or _NO_TEST_SLUG)[:_MAX_SLUG_LEN]


def request_id_prefix(nodeid: str) -> str:
    """The shared prefix of every id a test sent -- the string to grep backend logs for.

    ``file.py::TestKB::test_create[case-1]`` → ``it-TestKB.test_create.case-1-``
    """
    return f"{TEST_ID_PREFIX}{slug_for_nodeid(nodeid)}-"


def set_current_test(nodeid: Optional[str]) -> None:
    """Bind the test whose name tags subsequent requests; ``None`` outside a test.

    A plain global rather than a ContextVar so ids still carry the test name on
    requests made from helper threads.

    ``file.py::TestKB::test_create`` → later ids start with ``it-TestKB.test_create-``;
    ``None`` → ``it-session-``.
    """
    global _current_slug
    _current_slug = slug_for_nodeid(nodeid) if nodeid else _NO_TEST_SLUG


def generate_request_id() -> str:
    """``it-<Class>.<test_func>-<8 hex>``; unique per call, like the frontend's nanoid.

    After ``set_current_test("file.py::TestKB::test_create")`` →
    ``it-TestKB.test_create-a1b2c3d4`` (last 8 hex chars are random).
    """
    request_id = f"{TEST_ID_PREFIX}{_current_slug}-{uuid.uuid4().hex[:8]}"
    # Built from an already-sanitized slug within budget, so this is the guarantee
    # that what we send is what the server logs, not a reshaping.
    return sanitize_root_id(request_id) or request_id


_hook_installed = False


def install_requests_hook() -> None:
    """Stamp ``x-request-id`` on every ``requests`` call made by the suite.

    After this, ``requests.get(...)`` sends
    ``x-request-id: it-TestKB.test_create-a1b2c3d4``. An already-set header is left alone.
    """
    global _hook_installed
    if _hook_installed:
        return

    original = requests.sessions.Session.prepare_request

    def prepare_request(
        self: requests.sessions.Session, request: requests.Request, *args: Any, **kwargs: Any
    ) -> requests.PreparedRequest:
        prepared = original(self, request, *args, **kwargs)
        # CaseInsensitiveDict: an explicitly passed header wins whatever its casing.
        prepared.headers.setdefault(HEADER_REQUEST_ID, generate_request_id())
        return prepared

    requests.sessions.Session.prepare_request = prepare_request  # type: ignore[method-assign]
    _hook_installed = True
