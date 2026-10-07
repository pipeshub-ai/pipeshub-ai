"""Entry point and wire format of a parse worker process.

``parse_pool`` starts it as ``python -m app.modules.parsers.parse_worker
<request fd> <reply fd>``. It has a main module of its own on purpose: a
``multiprocessing`` worker re-imports its parent's main module, which for the
indexing service costs 1.3 GB and fifteen to twenty seconds before the first byte is
parsed. This one imports only what the functions it is sent need, so keep this
file's own imports to the standard library and modules as light as it.

Each message is an 8-byte length and then a pickle, so a request that fails to
unpickle (a module that will not import) is answered instead of leaving the
stream out of step.
"""
from __future__ import annotations

import os
import pickle
import signal
import struct
import sys
import traceback
from typing import BinaryIO

from app.utils.process_hardening import mark_process_non_dumpable

_LENGTH = struct.Struct("!Q")


def write_frame(stream: BinaryIO, obj: object) -> None:
    payload = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    stream.write(_LENGTH.pack(len(payload)))
    stream.write(payload)
    stream.flush()


def _read_exactly(stream: BinaryIO, size: int) -> bytearray:
    data = bytearray(size)
    view = memoryview(data)
    filled = 0
    while filled < size:
        count = stream.readinto(view[filled:])
        if not count:
            raise EOFError("the other end of the parse pipe closed")
        filled += count
    return data


def read_frame(stream: BinaryIO) -> bytearray:
    """The next message, still pickled. ``EOFError`` when the other end is gone."""
    (size,) = _LENGTH.unpack(_read_exactly(stream, _LENGTH.size))
    return _read_exactly(stream, size)


def _portable(exc: BaseException) -> BaseException:
    """*exc*, or a stand-in when it would not survive the trip to the parent."""
    try:
        pickle.loads(pickle.dumps(exc))
    except Exception:
        return RuntimeError(f"{type(exc).__name__}: {exc}")
    return exc


def _answer(request: bytearray) -> tuple[bool, object, str]:
    try:
        fn, args = pickle.loads(request)
        return True, fn(*args), ""
    except Exception as exc:  # re-raised in the parent
        return False, _portable(exc), traceback.format_exc()


def main(request_fd: int, reply_fd: int) -> None:
    # This process was handed the service's environment, secrets included, and
    # exec reset the non-dumpable mark the service set on itself. Without it a
    # same-uid process (a tool the service runs) can read /proc/<pid>/environ.
    mark_process_non_dumpable()
    # Ctrl-C in a terminal reaches the whole process group; the parent decides
    # when a worker stops, and a worker whose parent is gone sees its pipe close.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    requests = os.fdopen(request_fd, "rb")
    replies = os.fdopen(reply_fd, "wb")
    write_frame(replies, os.getpid())
    while True:
        try:
            request = read_frame(requests)
        except EOFError:
            return
        reply = _answer(request)
        try:
            write_frame(replies, reply)
        except BrokenPipeError:
            return
        except Exception as exc:  # a result that will not pickle
            failure = RuntimeError(f"The parse result could not be sent back: {exc}")
            write_frame(replies, (False, failure, traceback.format_exc()))


if __name__ == "__main__":
    main(int(sys.argv[1]), int(sys.argv[2]))
