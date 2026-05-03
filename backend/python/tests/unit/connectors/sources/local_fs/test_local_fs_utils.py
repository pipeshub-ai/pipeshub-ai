"""Tests for local_fs utility helpers."""

import sys
import types
from pathlib import Path

import pytest

# Same import shims as ``test_connector.py`` — required before importing ``connector``.
if "app.containers.connector" not in sys.modules:
    _stub_container = types.ModuleType("app.containers.connector")

    class _ConnectorAppContainer:
        pass

    _stub_container.ConnectorAppContainer = _ConnectorAppContainer
    sys.modules["app.containers.connector"] = _stub_container

if "redis" not in sys.modules:
    _redis_exc = types.ModuleType("redis.exceptions")
    _redis_exc.ConnectionError = type("RedisConnectionError", (Exception,), {})
    _redis_exc.TimeoutError = type("RedisTimeoutError", (Exception,), {})
    sys.modules["redis.exceptions"] = _redis_exc

    _redis_backoff = types.ModuleType("redis.backoff")

    class _ExponentialBackoff:
        pass

    _redis_backoff.ExponentialBackoff = _ExponentialBackoff
    sys.modules["redis.backoff"] = _redis_backoff

    _redis_retry = types.ModuleType("redis.asyncio.retry")

    class _Retry:
        pass

    _redis_retry.Retry = _Retry
    sys.modules["redis.asyncio.retry"] = _redis_retry

    _redis_asyncio = types.ModuleType("redis.asyncio")
    _redis_asyncio.Redis = type("Redis", (), {})
    sys.modules["redis.asyncio"] = _redis_asyncio

    _redis = types.ModuleType("redis")
    _redis.asyncio = _redis_asyncio
    sys.modules["redis"] = _redis

if "etcd3" not in sys.modules:
    _etcd3 = types.ModuleType("etcd3")
    _etcd3.client = type("client", (), {})
    sys.modules["etcd3"] = _etcd3

from app.connectors.core.registry.filters import (  # noqa: E402
    DatetimeOperator,
    Filter,
    FilterCollection,
    FilterType,
    SyncFilterKey,
)
from app.connectors.sources.local_fs.connector import (  # noqa: E402
    _local_fs_passes_date_filters as local_fs_passes_date_filters,
    _parse_batch_size_from_sync as parse_batch_size_from_sync,
    _read_sync_settings_from_config as read_sync_settings_from_config,
    _stat_created_epoch_ms as stat_created_epoch_ms,
    _validate_host_path as validate_host_path,
)
from app.connectors.sources.local_fs.utils import parse_sync_bool  # noqa: E402


class TestParseSyncBool:
    @pytest.mark.parametrize(
        "raw, default, expected",
        [
            (True, False, True),
            (False, True, False),
            ("true", False, True),
            ("TRUE", False, True),
            ("  yes  ", False, True),
            ("1", False, True),
            ("on", False, True),
            ("false", True, False),
            ("0", True, False),
            ("no", True, False),
            ("off", True, False),
        ],
    )
    def test_truthy_and_falsey_strings(self, raw, default, expected):
        assert parse_sync_bool(raw, default) is expected

    def test_unrecognized_string_is_false(self):
        """Strings that are not explicit truthy tokens are treated as false."""
        assert parse_sync_bool("maybe", True) is False
        assert parse_sync_bool("maybe", False) is False

    def test_non_bool_non_str_uses_default(self):
        assert parse_sync_bool(None, True) is True
        assert parse_sync_bool(42, False) is False
        assert parse_sync_bool([], True) is True


def _make_stat(
    *,
    mtime_s: float,
    ctime_s: float,
    birthtime_s: float | None = None,
    size: int = 100,
) -> types.SimpleNamespace:
    ns: dict[str, float | int] = {
        "st_mtime": mtime_s,
        "st_ctime": ctime_s,
        "st_size": size,
    }
    if birthtime_s is not None:
        ns["st_birthtime"] = birthtime_s
    return types.SimpleNamespace(**ns)


def test_stat_created_epoch_ms_prefers_birthtime():
    st = _make_stat(mtime_s=0, ctime_s=0, birthtime_s=2.5)
    assert stat_created_epoch_ms(st) == 2500


def test_stat_created_epoch_ms_falls_back_to_ctime():
    st = _make_stat(mtime_s=0, ctime_s=1.25)
    assert stat_created_epoch_ms(st) == 1250


@pytest.mark.parametrize(
    "sync_cfg, expected",
    [
        ({}, 50),
        ({"batchSize": "10"}, 10),
        ({"batch_size": 3}, 3),
        ({"batchSize": "", "batch_size": "7"}, 7),
        ({"customValues": {"batchSize": "8"}}, 8),
        ({"values": {"batch_size": "9"}}, 9),
        ({"batchSize": "0"}, 1),
        ({"batchSize": "not-int"}, 50),
    ],
)
def test_parse_batch_size_from_sync(sync_cfg, expected):
    assert parse_batch_size_from_sync(sync_cfg) == expected


def test_read_sync_settings_accepts_custom_values_shape():
    root, include, batch_size = read_sync_settings_from_config(
        {
            "sync": {
                "customValues": {
                    "sync_root_path": "/Users/me/Documents",
                    "include_subfolders": "false",
                    "batchSize": "17",
                }
            }
        }
    )

    assert root == "/Users/me/Documents"
    assert include is False
    assert batch_size == 17


def test_read_sync_settings_flat_values_take_priority():
    root, include, batch_size = read_sync_settings_from_config(
        {
            "sync": {
                "sync_root_path": "/server/mount",
                "include_subfolders": True,
                "batchSize": "3",
                "customValues": {
                    "sync_root_path": "/desktop/path",
                    "include_subfolders": "false",
                    "batchSize": "99",
                },
            }
        }
    )

    assert root == "/server/mount"
    assert include is True
    assert batch_size == 3


def test_validate_host_path_empty_ok():
    ok, detail = validate_host_path("   ")
    assert ok is True
    assert detail == ""


def test_validate_host_path_readable_dir(tmp_path: Path):
    d = tmp_path / "sync"
    d.mkdir()
    ok, detail = validate_host_path(str(d))
    assert ok is True
    assert Path(detail).resolve() == d.resolve()


def test_validate_host_path_missing(tmp_path: Path):
    missing = tmp_path / "nope"
    ok, detail = validate_host_path(str(missing))
    assert ok is False
    assert "does not exist" in detail


def test_local_fs_passes_date_filters_no_filters():
    st = _make_stat(mtime_s=1000, ctime_s=1000)
    empty = FilterCollection(filters=[])
    assert local_fs_passes_date_filters(st, empty) is True


def _dt_between_filter(key: str, start_ms: int, end_ms: int) -> Filter:
    return Filter(
        key=key,
        type=FilterType.DATETIME,
        operator=DatetimeOperator.IS_BETWEEN,
        value={"start": start_ms, "end": end_ms},
    )


def test_local_fs_passes_modified_window():
    st = _make_stat(mtime_s=3.0, ctime_s=1.0)
    flt = _dt_between_filter(SyncFilterKey.MODIFIED.value, 2000, 4000)
    coll = FilterCollection(filters=[flt])
    assert local_fs_passes_date_filters(st, coll) is True


def test_local_fs_fails_modified_before_window():
    st = _make_stat(mtime_s=1.0, ctime_s=1.0)
    flt = _dt_between_filter(SyncFilterKey.MODIFIED.value, 2000, 4000)
    coll = FilterCollection(filters=[flt])
    assert local_fs_passes_date_filters(st, coll) is False


def test_local_fs_passes_created_window():
    st = _make_stat(mtime_s=10.0, ctime_s=5.0, birthtime_s=3.0)
    flt = _dt_between_filter(SyncFilterKey.CREATED.value, 2000, 4000)
    coll = FilterCollection(filters=[flt])
    assert local_fs_passes_date_filters(st, coll) is True


def test_local_fs_fails_created_outside_window():
    st = _make_stat(mtime_s=10.0, ctime_s=5.0, birthtime_s=1.0)
    flt = _dt_between_filter(SyncFilterKey.CREATED.value, 2000, 4000)
    coll = FilterCollection(filters=[flt])
    assert local_fs_passes_date_filters(st, coll) is False
