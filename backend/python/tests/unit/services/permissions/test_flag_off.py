"""With the filter mode off (the default) the viewer-scope service changes nothing: no cache is created, no Redis is
opened, and the permission events the writers call are no-ops."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from app.services.cache import invalidation_hooks as hooks
from app.services.permissions.mode import ENV_ACL_FILTER_MODE
from app.services.permissions.wiring import register_viewer_scope_version


@pytest.mark.parametrize("raw", [None, "", "off", "legacy", "scoped"])
async def test_a_service_started_with_the_mode_off_registers_nothing(monkeypatch: pytest.MonkeyPatch, raw: str | None) -> None:
    if raw is None:
        monkeypatch.delenv(ENV_ACL_FILTER_MODE, raising=False)
    else:
        monkeypatch.setenv(ENV_ACL_FILTER_MODE, raw)
    config = MagicMock()
    hooks.reset_viewer_scope_version()

    assert await register_viewer_scope_version(config, logging.getLogger("t")) is None

    config.assert_not_called()      # no configuration read, so no Redis connection either
    assert config.mock_calls == []
    assert hooks._scope_cache["cache"] is None


async def test_the_permission_events_are_no_ops_until_a_cache_is_registered() -> None:
    hooks.reset_viewer_scope_version()
    await hooks.notify_principals_changed("org-1")
    await hooks.notify_kb_records_changed("kb-1", "org-1")
    await hooks.notify_connector_permissions_changed("conn-1", "org-1")
    await hooks.notify_connector_sync_completed("conn-1", "org-1")


async def test_the_scope_organisation_never_reaches_the_accessible_records_invalidator() -> None:
    """The existing invalidator gets exactly what it got before: the ambient organisation only picks whose viewer-scope
    version moves."""
    from unittest.mock import AsyncMock

    invalidator = AsyncMock()
    hooks._state["invalidator"] = invalidator
    try:
        await hooks.notify_kb_records_changed("kb-1", scope_org="org-ambient")
        invalidator.on_kb_records_changed.assert_awaited_once_with("kb-1", None)
    finally:
        hooks.reset_accessible_records_invalidator()


def test_importing_the_service_opens_no_redis() -> None:
    """A fresh interpreter imports the service and the version hooks with the Redis provider factory patched to fail."""
    import subprocess
    import sys

    code = (
        "from unittest import mock\n"
        "from app.services.redis import connection_provider_factory as f\n"
        "boom = mock.MagicMock(side_effect=AssertionError('redis opened'))\n"
        "f.get_redis_provider = boom\n"
        "import app.services.permissions, app.services.cache.invalidation_hooks\n"
        "assert not boom.called\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr[-400:]
