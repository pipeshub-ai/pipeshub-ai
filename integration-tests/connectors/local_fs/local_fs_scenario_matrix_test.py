# pyright: ignore-file

"""Local FS in the shared scenario matrix (``connectors/scenario_matrix.py``): not runnable yet.

Local FS no longer reads a folder the backend can see. ``run_sync`` pulls file
events from the owner's desktop app over the Node relay and Socket.IO, and
enabling the connector needs a claimed desktop device (see
``backend/python/app/connectors/sources/local_fs/README.md``). The integration
stack has no desktop client, which is also why the suite's own fixtures skip.
Every scenario is listed as unsupported so the matrix report shows the gap and
its reason; wiring it needs a desktop stand-in that answers the pull relay.
"""

from __future__ import annotations

import pytest

from connectors.scenario_matrix import Action, ConnectorScenarioMatrix

NO_DESKTOP_CLIENT = (
    "Local FS syncs only through the PipesHub desktop app, which the integration stack "
    "does not run: run_sync pulls file events from the owner device over Socket.IO and "
    "enabling needs a claimed device (sources/local_fs/README.md)"
)


@pytest.fixture(scope="module")
def scenario_adapter():
    pytest.skip(NO_DESKTOP_CLIENT)


@pytest.mark.integration
@pytest.mark.local_fs
class TestLocalFsScenarioMatrix(ConnectorScenarioMatrix):
    SOURCE = "Local FS"
    UNSUPPORTED = {action.value: NO_DESKTOP_CLIENT for action in Action}
