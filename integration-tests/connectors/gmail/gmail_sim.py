"""The Gmail simulator's control API, for building and changing the mailboxes a suite syncs.

One job: let a test create a Workspace and its mail, change it the way people do (deliver, archive, trash,
delete, suspend a user, ...) and read what a correct sync must contain. Calls the simulator only, never Google.
"""

from __future__ import annotations

from typing import Any

import requests
from cryptography.hazmat.primitives import serialization

from connectors.gmail.gmail_env import GMAIL_READONLY, GMAIL_SIM_URL, PersonalEnv, WorkspaceEnv


class GmailSim:
    def __init__(self, base_url: str = GMAIL_SIM_URL, timeout: float = 60) -> None:
        self.base_url = base_url
        self.timeout = timeout

    def _call(self, method: str, path: str, body: Any = None) -> Any:
        resp = requests.request(method, f"{self.base_url}{path}", json=body, timeout=self.timeout)
        if resp.status_code >= 400:
            raise RuntimeError(f"Gmail simulator {method} {path}: {resp.status_code} {resp.text[:400]}")
        return resp.json()

    def reachable(self) -> bool:
        try:
            return requests.get(f"{self.base_url}/_sim/health", timeout=5).ok
        except requests.RequestException:
            return False

    def load(self, scenario: dict[str, Any]) -> None:
        self._call("DELETE", "/_sim/faults")
        self._call("DELETE", "/_sim/gmail/behaviours")
        self._call("PUT", "/_sim/gmail/scenario", scenario)

    def ops(self, *ops: dict[str, Any]) -> list[dict[str, Any]]:
        return self._call("POST", "/_sim/gmail/ops", list(ops))["results"]

    def expected(self) -> dict[str, Any]:
        return self._call("GET", "/_sim/gmail/expected")

    def message(self, mailbox: str, key: str) -> dict[str, Any]:
        for msg in self.expected()["messages"]:
            if msg["mailbox"].lower() == mailbox.lower() and msg["key"] == key:
                return msg
        raise KeyError(f"no message {key!r} in {mailbox}")

    def messages_of(self, mailbox: str) -> list[dict[str, Any]]:
        return [m for m in self.expected()["messages"] if m["mailbox"].lower() == mailbox.lower()]

    def change_app(self, **values: Any) -> None:
        self._call("POST", "/_sim/gmail/app", values)

    def revoke_refresh_token(self) -> None:
        self._call("POST", "/_sim/gmail/refresh/revoke")

    def attachment_bytes(self, user_id: str, message_id: str, part: str) -> bytes:
        resp = requests.get(f"{self.base_url}/_sim/gmail/content/{user_id}/{message_id}/{part}", timeout=self.timeout)
        resp.raise_for_status()
        return resp.content

    def unmodeled(self) -> list[dict[str, Any]]:
        return self._call("GET", "/_sim/gmail/unmodeled")


def workspace_app(env: WorkspaceEnv) -> dict[str, Any]:
    """The service account as the simulator must know it: its address, and the public half of its key, so the
    connector's signed JWT is checked as Google checks it."""
    info = env.service_account
    key = serialization.load_pem_private_key(info["private_key"].encode(), password=None)
    public = key.public_key().public_bytes(serialization.Encoding.PEM,
                                           serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return {"client_email": info["client_email"], "public_key_pem": public}


def personal_oauth(env: PersonalEnv, user_key: str) -> dict[str, Any]:
    """The OAuth client and refresh token the simulator accepts: the Drive personal suite's own."""
    return {"user": user_key, "client_id": env.client_id, "client_secret": env.client_secret,
            "refresh_token": env.refresh_token, "scopes": [GMAIL_READONLY]}
