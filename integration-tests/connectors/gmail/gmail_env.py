"""The Gmail suites' settings: the Google Drive suites' credentials, and where the Gmail simulator listens.

The Gmail suites reuse the Drive suites' variables on purpose (DESIGN.md, section 3): one set of Google
credentials in CI, and the Gmail connectors authenticate the same way the Drive connectors do.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

GMAIL_SIM_URL = os.getenv("GMAIL_SIM_URL", "http://localhost:8106").rstrip("/")

ENV_SA_JSON = "GOOGLE_DRIVE_WORKSPACE_SERVICE_ACCOUNT_JSON"
ENV_ADMIN_EMAIL = "GOOGLE_DRIVE_WORKSPACE_ADMIN_EMAIL"
ENV_TEST_USER = "GOOGLE_DRIVE_WORKSPACE_TEST_USER_EMAIL"
ENV_SECOND_USER = "GOOGLE_DRIVE_WORKSPACE_SECOND_USER_EMAIL"

ENV_CLIENT_ID = "GOOGLE_DRIVE_CLIENT_ID"
ENV_CLIENT_SECRET = "GOOGLE_DRIVE_CLIENT_SECRET"
ENV_REFRESH_TOKEN = "GOOGLE_DRIVE_REFRESH_TOKEN"
ENV_PERSONAL_USER = "GOOGLE_DRIVE_TEST_USER_EMAIL"

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


def _required(names: list[str]) -> dict[str, str]:
    values = {name: os.getenv(name, "").strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ValueError(f"missing {', '.join(missing)}")
    return values


def workspace_env() -> WorkspaceEnv:
    v = _required([ENV_SA_JSON, ENV_ADMIN_EMAIL, ENV_TEST_USER, ENV_SECOND_USER])
    return WorkspaceEnv(v[ENV_SA_JSON], v[ENV_ADMIN_EMAIL].lower(), v[ENV_TEST_USER].lower(),
                        v[ENV_SECOND_USER].lower())


def personal_env() -> PersonalEnv:
    from helper.oauth_token_helper import _DEFAULT_REFRESH_TOKENS_FILE  # type: ignore[import-not-found]
    from dotenv import load_dotenv

    if _DEFAULT_REFRESH_TOKENS_FILE.exists():
        load_dotenv(_DEFAULT_REFRESH_TOKENS_FILE, override=False)
    v = _required([ENV_CLIENT_ID, ENV_CLIENT_SECRET, ENV_REFRESH_TOKEN, ENV_PERSONAL_USER])
    return PersonalEnv(v[ENV_CLIENT_ID], v[ENV_CLIENT_SECRET], v[ENV_REFRESH_TOKEN], v[ENV_PERSONAL_USER].lower())
