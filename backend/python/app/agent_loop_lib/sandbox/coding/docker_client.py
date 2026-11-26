"""Legacy import path; this module lives in ``app.harness.sandbox.docker_client``."""

import sys

from app.harness.sandbox import docker_client as _moved
from app.harness.sandbox.docker_client import *  # noqa: F403

sys.modules[__name__] = _moved
