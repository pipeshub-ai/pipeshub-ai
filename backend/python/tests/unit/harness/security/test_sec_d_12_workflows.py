"""SEC-D-12 in the security gate: the workflow-privilege lint over this repository and its seeded bad cases.

The cases live beside the lint in scripts/test_workflow_lint.py (unittest, which main.yml runs with the tools lock);
this module collects the same classes so the gate's `security` stage holds them too.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[6] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
_spec = importlib.util.spec_from_file_location("test_workflow_lint", _SCRIPTS / "test_workflow_lint.py")
assert _spec is not None and _spec.loader is not None
_cases = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cases)

TestFixture = _cases.TestFixture
TestPermissions = _cases.TestPermissions
TestPersistedCredentials = _cases.TestPersistedCredentials
TestExpressions = _cases.TestExpressions
TestAgent = _cases.TestAgent
TestTriggersAndOwners = _cases.TestTriggersAndOwners
TestThisRepository = _cases.TestThisRepository
