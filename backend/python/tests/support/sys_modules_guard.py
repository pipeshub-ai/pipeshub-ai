"""Make a ``patch.dict(sys.modules)`` that drops modules fail at its own ``with`` line.

On exit ``patch.dict`` restores its snapshot, which deletes every module first imported inside it. The next import of
such a module runs its body again: its metrics register twice (DuplicateTimeseries), and two copies of its classes and
ContextVars coexist, so a later, unrelated test breaks depending on which tests shared its worker.

``_patch_dict._unpatch_dict`` is CPython's restore step; reading it at import raises AttributeError if a Python
upgrade renames it. If a Python ever stopped calling it, tests/unit/test_sys_modules_guard.py goes red: don't skip it.
"""

import sys
from unittest import mock

_restore = mock._patch_dict._unpatch_dict


class ModulesDroppedError(AssertionError):
    pass


def _restore_and_check(patcher: mock._patch_dict) -> None:
    if patcher.in_dict is not sys.modules:
        _restore(patcher)
        return
    present = set(sys.modules)
    _restore(patcher)
    dropped = sorted(present - sys.modules.keys() - patcher.values.keys())
    if dropped:
        raise ModulesDroppedError(
            f"patch.dict(sys.modules) dropped {len(dropped)} module(s) first imported inside it: {', '.join(dropped[:10])}."
            " Import them before patching, or patch the attribute the code reads (patch('pkg.mod.name'))."
        )


def install() -> None:
    mock._patch_dict._unpatch_dict = _restore_and_check
