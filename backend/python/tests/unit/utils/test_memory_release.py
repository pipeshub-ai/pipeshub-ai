from unittest.mock import patch

import pytest

from app.utils import memory_release


def test_trims_the_heap() -> None:
    with patch.object(memory_release.ctypes, "CDLL") as cdll:
        memory_release.release_freed_memory()
    cdll.assert_called_once_with("libc.so.6")
    cdll.return_value.malloc_trim.assert_called_once_with(0)


def test_is_a_no_op_without_glibc() -> None:
    with patch.object(memory_release.ctypes, "CDLL", side_effect=OSError("no libc.so.6")):
        memory_release.release_freed_memory()


def test_caps_the_malloc_arenas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MALLOC_ARENA_MAX", raising=False)
    with patch.object(memory_release.ctypes, "CDLL") as cdll:
        memory_release.limit_malloc_arenas(2)
    cdll.return_value.mallopt.assert_called_once_with(-8, 2)


def test_leaves_the_operators_arena_setting_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MALLOC_ARENA_MAX", "4")
    with patch.object(memory_release.ctypes, "CDLL") as cdll:
        memory_release.limit_malloc_arenas(2)
    cdll.assert_not_called()


def test_arena_cap_is_a_no_op_without_glibc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MALLOC_ARENA_MAX", raising=False)
    with patch.object(memory_release.ctypes, "CDLL", side_effect=OSError("no libc.so.6")):
        memory_release.limit_malloc_arenas(2)
