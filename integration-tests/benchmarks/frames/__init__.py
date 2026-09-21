"""FRAMES benchmark harness for PipesHub — see README.md.

Importing the package puts `integration-tests/` and `integration-tests/helper/`
on `sys.path`, exactly as the root conftest does, because the reused helpers
import each other under both module roots.
"""

from benchmarks.frames.paths import ensure_helper_importable

HARNESS_VERSION = "1.0.0"

ensure_helper_importable()
