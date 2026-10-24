"""The Gmail suites' settings: the simulated Google tenant's users and OAuth client, and where the simulator listens.

Every value comes from ``connectors/gmail_sim.env`` (DESIGN.md, section 3), as the Confluence and Jira suites'
come from ``atlassian_sim.env``: none is a secret and none reaches Google. The one thing that must look like a
real credential, the Workspace service account's private key, is generated here once per test run; the
simulator is given only its public half and checks the connector's signed JWTs against it.
"""

from __future__ import annotations

import functools
import json
import os
import uuid
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

GMAIL_SIM_URL = os.getenv("GMAIL_SIM_URL", "http://localhost:8106").rstrip("/")

#: Set when the stack's Gmail connectors were pointed at the simulator; then an unreachable simulator is a
#: broken run, not a missing source.
ENV_API_BASE_URL = "GMAIL_GOOGLE_API_BASE_URL"

ENV_ADMIN_EMAIL = "GMAIL_SIM_WORKSPACE_ADMIN_EMAIL"
ENV_TEST_USER = "GMAIL_SIM_WORKSPACE_TEST_USER_EMAIL"
ENV_SECOND_USER = "GMAIL_SIM_WORKSPACE_SECOND_USER_EMAIL"

ENV_CLIENT_ID = "GMAIL_SIM_OAUTH_CLIENT_ID"
ENV_CLIENT_SECRET = "GMAIL_SIM_OAUTH_CLIENT_SECRET"
ENV_REFRESH_TOKEN = "GMAIL_SIM_REFRESH_TOKEN"
ENV_PERSONAL_USER = "GMAIL_SIM_PERSONAL_USER_EMAIL"

WORKSPACE_VARS = (ENV_ADMIN_EMAIL, ENV_TEST_USER, ENV_SECOND_USER)
PERSONAL_VARS = (ENV_CLIENT_ID, ENV_CLIENT_SECRET, ENV_REFRESH_TOKEN, ENV_PERSONAL_USER)

GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"


@dataclass(frozen=True)
class WorkspaceEnv:
    service_account_json: str
    admin_email: str
    test_user: str
    second_user: str

    @property
    def service_account(self) -> dict:
        return json.loads(self.service_account_json)

    @property
    def domain(self) -> str:
        return self.admin_email.split("@", 1)[1]


@dataclass(frozen=True)
class PersonalEnv:
    client_id: str
    client_secret: str
    refresh_token: str
    user: str


def simulator_expected() -> bool:
    """Whether this run pointed the Gmail connectors at the simulator (CI does, through gmail_sim.env)."""
    return bool(os.getenv(ENV_API_BASE_URL, "").strip())


def _required(names: tuple[str, ...]) -> dict[str, str]:
    values = {name: os.getenv(name, "").strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ValueError(f"missing {', '.join(missing)} (load connectors/gmail_sim.env)")
    return values


@functools.lru_cache(maxsize=1)
def _service_account_json() -> str:
    """A service-account key file shaped like Google's, with a key made for this run only."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    project = "connsim-gmail-it"
    return json.dumps({
        "type": "service_account",
        "project_id": project,
        "private_key_id": uuid.uuid4().hex,
        "private_key": pem,
        "client_email": f"pipeshub-gmail-it@{project}.iam.gserviceaccount.com",
        "client_id": str(uuid.uuid4().int)[:21],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "universe_domain": "googleapis.com",
    })


def workspace_env() -> WorkspaceEnv:
    v = _required(WORKSPACE_VARS)
    return WorkspaceEnv(_service_account_json(), v[ENV_ADMIN_EMAIL].lower(), v[ENV_TEST_USER].lower(),
                        v[ENV_SECOND_USER].lower())


def personal_env() -> PersonalEnv:
    v = _required(PERSONAL_VARS)
    return PersonalEnv(v[ENV_CLIENT_ID], v[ENV_CLIENT_SECRET], v[ENV_REFRESH_TOKEN], v[ENV_PERSONAL_USER].lower())
