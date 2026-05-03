"""Tests for Local FS connector helpers (non-exported functions used by :class:`LocalFsConnector`)."""

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
    LOCAL_FS_STORAGE_PATH_PREFIX,
    LocalFsConnector,
    _local_fs_passes_date_filters as local_fs_passes_date_filters,
    _parse_batch_size_from_sync as parse_batch_size_from_sync,
    _read_sync_settings_from_config as read_sync_settings_from_config,
    _stat_created_epoch_ms as stat_created_epoch_ms,
    _sync_value_from_config as sync_value_from_config,
    _validate_host_path as validate_host_path,
)
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


def test_local_fs_fails_modified_after_window():
    """Modified time after the upper bound must be filtered out (the diff path
    not exercised by the existing 'before window' test)."""
    st = _make_stat(mtime_s=10.0, ctime_s=1.0)
    flt = _dt_between_filter(SyncFilterKey.MODIFIED.value, 2000, 4000)
    coll = FilterCollection(filters=[flt])
    assert local_fs_passes_date_filters(st, coll) is False


def test_local_fs_passes_when_filter_present_but_empty():
    """An empty datetime filter (no bounds set) is a no-op, not a hard reject."""
    st = _make_stat(mtime_s=10.0, ctime_s=10.0)
    empty = Filter(
        key=SyncFilterKey.MODIFIED.value,
        type=FilterType.DATETIME,
        operator=DatetimeOperator.IS_BETWEEN,
        value={},
    )
    coll = FilterCollection(filters=[empty])
    assert local_fs_passes_date_filters(st, coll) is True


def test_local_fs_combines_modified_and_created_filters():
    """Both filters must hold simultaneously; a passing modified is not enough
    to override a failing created window."""
    st = _make_stat(mtime_s=3.0, ctime_s=5.0, birthtime_s=10.0)  # created at 10000ms
    flt_mod = _dt_between_filter(SyncFilterKey.MODIFIED.value, 2000, 4000)
    flt_cre = _dt_between_filter(SyncFilterKey.CREATED.value, 2000, 4000)
    coll = FilterCollection(filters=[flt_mod, flt_cre])
    assert local_fs_passes_date_filters(st, coll) is False


def test_stat_created_epoch_ms_birthtime_zero_falls_back_to_ctime():
    """Some FAT/ext4 mounts surface birthtime=0 — must NOT be treated as 1970."""
    # The current implementation uses `if birth is not None` rather than truthy
    # check; this test pins down that behavior so a future "if not birth" rewrite
    # would surface here.
    st = _make_stat(mtime_s=0, ctime_s=5.0, birthtime_s=0.0)
    assert stat_created_epoch_ms(st) == 0


# --- _sync_value_from_config -------------------------------------------------

def test_sync_value_flat_takes_priority():
    cfg = {"key": "flat", "values": {"key": "nested-values"}}
    assert sync_value_from_config(cfg, "key") == "flat"


def test_sync_value_falls_through_empty_string():
    """Empty string at the flat level should defer to the nested value, not lock in ''."""
    cfg = {"key": "", "customValues": {"key": "from-custom"}}
    assert sync_value_from_config(cfg, "key") == "from-custom"


def test_sync_value_returns_default_when_missing():
    assert sync_value_from_config({}, "missing", default="d") == "d"
    assert sync_value_from_config({"other": 1}, "missing", default=42) == 42


def test_sync_value_returns_default_for_non_dict_input():
    assert sync_value_from_config("not-a-dict", "k", default=7) == 7  # type: ignore[arg-type]
    assert sync_value_from_config(None, "k", default=None) is None  # type: ignore[arg-type]


def test_sync_value_values_takes_priority_over_custom_values():
    """When both nested keys exist, ``values`` wins over ``customValues``
    (matches the iteration order in the implementation)."""
    cfg = {
        "values": {"key": "from-values"},
        "customValues": {"key": "from-custom"},
    }
    assert sync_value_from_config(cfg, "key") == "from-values"


# --- _parse_batch_size_from_sync edge cases ---------------------------------

def test_parse_batch_size_negative_floored_to_one():
    assert parse_batch_size_from_sync({"batchSize": "-5"}) == 1


def test_parse_batch_size_whitespace_trimmed():
    assert parse_batch_size_from_sync({"batchSize": "  17 "}) == 17


def test_parse_batch_size_falls_back_for_non_dict_sync_cfg():
    # Anything non-dict ⇒ default 50 (must not raise).
    assert parse_batch_size_from_sync(None) == 50  # type: ignore[arg-type]
    assert parse_batch_size_from_sync("oops") == 50  # type: ignore[arg-type]


# --- _read_sync_settings_from_config edge cases -----------------------------

def test_read_sync_settings_none_config_returns_defaults():
    root, include, batch_size = read_sync_settings_from_config(None)
    assert root == ""
    assert include is True  # default
    assert batch_size == 50


def test_read_sync_settings_empty_config_returns_defaults():
    root, include, batch_size = read_sync_settings_from_config({})
    assert root == ""
    assert include is True
    assert batch_size == 50


def test_read_sync_settings_missing_sync_key_returns_defaults():
    root, include, batch_size = read_sync_settings_from_config({"other": "stuff"})
    assert root == ""
    assert include is True
    assert batch_size == 50


def test_read_sync_settings_strips_whitespace_from_root():
    root, _, _ = read_sync_settings_from_config(
        {"sync": {"sync_root_path": "  /some/path  "}}
    )
    assert root == "/some/path"


# --- _validate_host_path edge cases -----------------------------------------

def test_validate_host_path_not_a_directory(tmp_path: Path):
    f = tmp_path / "regular.txt"
    f.write_text("x", encoding="utf-8")
    ok, detail = validate_host_path(str(f))
    assert ok is False
    assert "not a directory" in detail


def test_validate_host_path_resolves_user_expansion(tmp_path: Path, monkeypatch):
    """``~`` must be expanded before the existence check."""
    monkeypatch.setenv("HOME", str(tmp_path))
    ok, detail = validate_host_path("~")
    assert ok is True
    assert Path(detail).resolve() == tmp_path.resolve()


# --- LocalFsConnector static helpers ---------------------------------------

class TestStorageDocumentIdFromPath:
    def test_returns_id_after_prefix(self):
        path = f"{LOCAL_FS_STORAGE_PATH_PREFIX}doc-abc123"
        assert (
            LocalFsConnector._storage_document_id_from_path(path) == "doc-abc123"
        )

    def test_strips_whitespace_after_prefix(self):
        path = f"{LOCAL_FS_STORAGE_PATH_PREFIX}  doc-xyz  "
        assert (
            LocalFsConnector._storage_document_id_from_path(path) == "doc-xyz"
        )

    def test_returns_none_for_empty_or_missing_prefix(self):
        assert LocalFsConnector._storage_document_id_from_path(None) is None
        assert LocalFsConnector._storage_document_id_from_path("") is None
        assert LocalFsConnector._storage_document_id_from_path("/abs/path") is None
        assert (
            LocalFsConnector._storage_document_id_from_path("storagex://other")
            is None
        )

    def test_returns_none_for_prefix_only(self):
        assert (
            LocalFsConnector._storage_document_id_from_path(
                LOCAL_FS_STORAGE_PATH_PREFIX
            )
            is None
        )

    def test_returns_none_for_prefix_plus_whitespace(self):
        assert (
            LocalFsConnector._storage_document_id_from_path(
                f"{LOCAL_FS_STORAGE_PATH_PREFIX}    "
            )
            is None
        )


class TestStorageSafeDocumentName:
    def test_strips_extension_and_path(self):
        assert (
            LocalFsConnector._storage_safe_document_name("a/b/notes.txt")
            == "notes"
        )

    def test_handles_windows_separator(self):
        assert (
            LocalFsConnector._storage_safe_document_name("a\\b\\notes.txt")
            == "notes"
        )

    def test_returns_file_for_empty(self):
        assert LocalFsConnector._storage_safe_document_name("") == "file"
        assert LocalFsConnector._storage_safe_document_name("/") == "file"

    def test_truncates_to_180_chars(self):
        long = "x" * 500 + ".txt"
        out = LocalFsConnector._storage_safe_document_name(long)
        assert len(out) == 180
        assert out == "x" * 180

    def test_no_extension_keeps_full_name(self):
        assert LocalFsConnector._storage_safe_document_name("README") == "README"


class TestStorageUploadFilename:
    def test_keeps_original_name_when_extension_present(self):
        assert (
            LocalFsConnector._storage_upload_filename("a/b/c.txt", "text/plain")
            == "c.txt"
        )

    def test_appends_bin_when_no_extension(self):
        assert (
            LocalFsConnector._storage_upload_filename("README", "text/plain")
            == "README.bin"
        )

    def test_appends_bin_for_octet_stream_unguessable(self):
        # ``foo.unknownext`` has no mimetype guess ⇒ bin fallback when caller
        # also gave us ``application/octet-stream``.
        assert (
            LocalFsConnector._storage_upload_filename(
                "foo.unknownext", "application/octet-stream"
            )
            == "foo.bin"
        )

    def test_replaces_path_separators(self):
        assert (
            LocalFsConnector._storage_upload_filename("a/b.txt", "text/plain")
            == "b.txt"
        )

    def test_handles_windows_separator(self):
        assert (
            LocalFsConnector._storage_upload_filename("a\\b.txt", "text/plain")
            == "b.txt"
        )


class TestNormalizeUploadedRelPath:
    def test_strips_and_normalizes_separators(self):
        assert (
            LocalFsConnector._normalize_uploaded_rel_path("  a\\b\\c.txt  ")
            == "a/b/c.txt"
        )

    def test_rejects_empty(self):
        with pytest.raises(Exception) as ei:
            LocalFsConnector._normalize_uploaded_rel_path("")
        assert ei.value.status_code == 400  # type: ignore[attr-defined]

    def test_rejects_absolute(self):
        with pytest.raises(Exception) as ei:
            LocalFsConnector._normalize_uploaded_rel_path("/abs/path")
        assert ei.value.status_code == 400  # type: ignore[attr-defined]

    def test_rejects_dot_segments(self):
        for bad in ("a/./b", "a/../b", "..", ".", "a//b"):
            with pytest.raises(Exception) as ei:
                LocalFsConnector._normalize_uploaded_rel_path(bad)
            assert ei.value.status_code == 400, bad  # type: ignore[attr-defined]

    def test_accepts_simple_relative(self):
        assert LocalFsConnector._normalize_uploaded_rel_path("a.txt") == "a.txt"
        assert (
            LocalFsConnector._normalize_uploaded_rel_path("nested/dir/file.txt")
            == "nested/dir/file.txt"
        )


class TestDecodeStorageBufferPayloadCorners:
    def test_empty_buffer_envelope(self):
        body = LocalFsConnector._decode_storage_buffer_payload(
            {"type": "Buffer", "data": []}
        )
        assert body == b""

    def test_raw_bytes_passthrough(self):
        assert (
            LocalFsConnector._decode_storage_buffer_payload(b"raw") == b"raw"
        )
        assert (
            LocalFsConnector._decode_storage_buffer_payload(bytearray(b"ba"))
            == b"ba"
        )

    def test_data_list_without_buffer_type(self):
        # Some legacy callers drop ``"type": "Buffer"`` and just send {"data":[...]}.
        assert (
            LocalFsConnector._decode_storage_buffer_payload({"data": [120, 121]})
            == b"xy"
        )

    def test_data_inner_bytes(self):
        assert (
            LocalFsConnector._decode_storage_buffer_payload({"data": b"raw"})
            == b"raw"
        )


class TestExtractStorageDocumentIdCorners:
    def test_handles_circular_reference_without_recursing(self):
        """Self-referential payload would loop forever without seen-set tracking."""
        d: dict = {"data": None}
        d["data"] = d  # cycle
        with pytest.raises(Exception) as ei:
            LocalFsConnector._extract_storage_document_id(d)
        assert ei.value.status_code == 502  # type: ignore[attr-defined]

    def test_walks_result_wrapper(self):
        assert (
            LocalFsConnector._extract_storage_document_id(
                {"result": {"document": {"_id": "deep"}}}
            )
            == "deep"
        )

    def test_oid_alternative_lowercase(self):
        # ``oid`` (no $) is an accepted alternative for the Mongo extended form.
        assert (
            LocalFsConnector._extract_storage_document_id({"_id": {"oid": "alt"}})
            == "alt"
        )

    def test_empty_string_id_treated_as_missing(self):
        with pytest.raises(Exception) as ei:
            LocalFsConnector._extract_storage_document_id({"_id": ""})
        assert ei.value.status_code == 502  # type: ignore[attr-defined]
