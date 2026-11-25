"""SEC-SC-07..11 in the pytest gate: the supply-chain rules over the repository and their seeded bad cases.

The cases live beside the rules in scripts/test_supply_chain.py (stdlib unittest, which main.yml runs before
anything is installed); this module collects the same classes so `pytest tests/cohesion` runs them too.
"""

from __future__ import annotations

import importlib.util
import sys

from tests.cohesion.support import REPO_ROOT

_SCRIPTS = REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
_spec = importlib.util.spec_from_file_location("test_supply_chain", _SCRIPTS / "test_supply_chain.py")
assert _spec is not None and _spec.loader is not None
_cases = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cases)

TestSecSc07HashedPythonLocks = _cases.TestSecSc07HashedPythonLocks
TestSecSc07InstallsOutsideImagesAndCi = _cases.TestSecSc07InstallsOutsideImagesAndCi
TestSecSc08LockAgreesWithPyproject = _cases.TestSecSc08LockAgreesWithPyproject
TestSecSc08NoInstallScripts = _cases.TestSecSc08NoInstallScripts
TestSecSc09Denylist = _cases.TestSecSc09Denylist
TestSecSc10NodeInstalls = _cases.TestSecSc10NodeInstalls
TestSecSc11ImagePins = _cases.TestSecSc11ImagePins
TestSdkJobLocks = _cases.TestSdkJobLocks
TestThisRepository = _cases.TestThisRepository
TestSecD11FetchedCode = _cases.TestSecD11FetchedCode
TestSecD11ActionPins = _cases.TestSecD11ActionPins
TestSecD11ImageTagsAndHelm = _cases.TestSecD11ImageTagsAndHelm
