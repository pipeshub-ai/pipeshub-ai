"""Run datamodel-code-generator with a directory walk in path order.

datamodel-code-generator 0.83.0 reads a directory input as ``sorted(path.rglob("*"), key=lambda p: p.name)``: two
schemas with one file name in different groups (``common.json`` in core and control-plane) keep the order the
file system lists them in, which differs between machines, and the order decides the models the second file gets
and their names. Sorting every walk by full path makes the output the same on every file system.
"""

from __future__ import annotations

import pathlib
import runpy

_rglob = pathlib.Path.rglob


def _sorted_rglob(self: pathlib.Path, *args: object, **kwargs: object) -> list[pathlib.Path]:
    return sorted(_rglob(self, *args, **kwargs))


pathlib.Path.rglob = _sorted_rglob  # type: ignore[method-assign]

if __name__ == "__main__":
    runpy.run_module("datamodel_code_generator", run_name="__main__", alter_sys=True)
