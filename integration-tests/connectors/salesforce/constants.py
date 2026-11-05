"""Settings of the Salesforce suites (see DESIGN.md). Every value comes from the environment.

Simulator mode (connectors/salesforce_sim.env): SALESFORCE_SIM_URL is where the test process
reaches the simulator, SALESFORCE_CONNECTOR_BASE_URL where the connector does (compose alias).

Live mode (read only): SALESFORCE_TEST_INSTANCE_URL, SALESFORCE_TEST_CLIENT_ID,
SALESFORCE_TEST_CLIENT_SECRET and SALESFORCE_TEST_REFRESH_TOKEN (a refresh token issued to
the connected app by the web server flow). Optional SALESFORCE_TEST_LOGIN_URL for a sandbox.
"""

import os

SALESFORCE_SIM_URL = os.getenv("SALESFORCE_SIM_URL", "").rstrip("/")
SALESFORCE_CONNECTOR_BASE_URL = os.getenv("SALESFORCE_CONNECTOR_BASE_URL", "").rstrip("/")

LIVE_VARS = [
    "SALESFORCE_TEST_INSTANCE_URL",
    "SALESFORCE_TEST_CLIENT_ID",
    "SALESFORCE_TEST_CLIENT_SECRET",
    "SALESFORCE_TEST_REFRESH_TOKEN",
]
SALESFORCE_TEST_LOGIN_URL = os.getenv("SALESFORCE_TEST_LOGIN_URL", "https://login.salesforce.com").rstrip("/")
API_VERSION = os.getenv("SALESFORCE_TEST_API_VERSION", "59.0")
SYNC_TIMEOUT_SEC = int(os.getenv("SALESFORCE_TEST_SYNC_TIMEOUT_SEC", "600"))

# PipesHub users the simulator scenario gives a Salesforce account (scenario.py).
SIM_USERS = ("maya", "raj", "olga", "gone")
