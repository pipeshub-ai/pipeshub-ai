from unittest.mock import patch

from app.utils import memory_release


def test_trims_the_heap() -> None:
    with patch.object(memory_release.ctypes, "CDLL") as cdll:
        memory_release.release_freed_memory()
    cdll.assert_called_once_with("libc.so.6")
    cdll.return_value.malloc_trim.assert_called_once_with(0)


def test_is_a_no_op_without_glibc() -> None:
    with patch.object(memory_release.ctypes, "CDLL", side_effect=OSError("no libc.so.6")):
        memory_release.release_freed_memory()
