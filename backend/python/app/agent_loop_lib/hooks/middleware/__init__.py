"""Legacy import path; this package lives in ``harness_core.kernel.hooks.middleware``.

Moved submodules keep their old paths as aliases; submodules that did not
move still live in this directory.
"""

from harness_core.kernel.hooks import middleware as _moved
from harness_core.kernel.hooks.middleware import *  # noqa: F403

globals().update(
    {k: v for k, v in vars(_moved).items() if not k.startswith("__") or k == "__all__"}
)


def __getattr__(name: str) -> object:
    # The update above copies the moved package as it was at import; a submodule imported later by its new path
    # becomes an attribute of the moved package only, so it is looked up there.
    return getattr(_moved, name)
