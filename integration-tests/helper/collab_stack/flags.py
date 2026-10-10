"""The collaborative-chats feature flag, toggled the way an admin does it.

The API reads platform flags through a cache with a 10 s TTL (``PlatformFeatureFlags``). A write does
not bust it, so ``set`` polls a probe request whose answer depends on the flag until the new value
is what the API enforces.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from bson import ObjectId

from helper.collab_stack.identity import stable_oid

if TYPE_CHECKING:
    from helper.collab_stack.stack import CollabStack

COLLAB_FLAG = "ENABLE_COLLABORATIVE_CHATS"
MENTIONS_FLAG = "ENABLE_CHAT_MENTIONS"
AGENT_BUILDER_FLAG = "ENABLE_CHAT_AGENT_BUILDER"
SETTINGS_PATH = "/api/v1/configurationManager/platform/settings"
PROBE_TIMEOUT_S = 30


class FeatureFlags:
    def __init__(self, stack: CollabStack) -> None:
        self.stack = stack
        self._probe_id: ObjectId | None = None

    def current(self) -> dict[str, bool]:
        r = self.stack.api.get(SETTINGS_PATH, self.stack.roster.admin)  # type: ignore[union-attr]
        assert r.status_code == 200, r.text
        return r.json()["featureFlags"]

    def set(self, enabled: bool, key: str = COLLAB_FLAG) -> None:
        """Store the flag and block until the running API enforces it."""
        api, admin = self.stack.api, self.stack.roster.admin  # type: ignore[union-attr]
        got = api.get(SETTINGS_PATH, admin)
        assert got.status_code == 200, got.text
        settings = got.json()
        settings["featureFlags"][key] = enabled
        put = api.post(
            SETTINGS_PATH,
            admin,
            json_body={"fileUploadMaxSizeBytes": settings["fileUploadMaxSizeBytes"], "featureFlags": settings["featureFlags"]},
        )
        assert put.status_code == 200, put.text
        if key == COLLAB_FLAG:
            self._wait_enforced(enabled)

    @contextmanager
    def value(self, enabled: bool):  # noqa: ANN201
        previous = self.current().get(COLLAB_FLAG, False)
        if previous != enabled:
            self.set(enabled)
        try:
            yield
        finally:
            if previous != enabled:
                self.set(previous)

    def _ensure_probe(self) -> ObjectId:
        """A chat the read recipient may open but not rename: 403 with the flag on, 404 with it off."""
        if self._probe_id is not None and self.stack.db["chatSessions"].count_documents({"_id": self._probe_id}):
            return self._probe_id
        roster = self.stack.roster
        now = datetime.now(timezone.utc)
        self._probe_id = stable_oid("flag-probe")
        self.stack.db["chatSessions"].replace_one(
            {"_id": self._probe_id},
            {
                "_id": self._probe_id,
                "sessionType": "chat",
                "nextSeq": 0,
                "userId": roster.owner.oid,  # type: ignore[union-attr]
                "orgId": roster.owner.org_oid,  # type: ignore[union-attr]
                "initiator": roster.owner.oid,  # type: ignore[union-attr]
                "title": "flag probe",
                "isShared": True,
                "sharedWith": [{"userId": roster.read_recipient.oid, "accessLevel": "read"}],  # type: ignore[union-attr]
                "isDeleted": False,
                "isArchived": False,
                "lastActivityAt": int(time.time() * 1000),
                "aclVersion": 0,
                "rev": 0,
                "createdAt": now,
                "updatedAt": now,
            },
            upsert=True,
        )
        return self._probe_id

    def _probe(self) -> int:
        probe_id = self._ensure_probe()
        r = self.stack.api.patch(  # type: ignore[union-attr]
            f"/api/v1/conversations/{probe_id}/title",
            self.stack.roster.read_recipient,  # type: ignore[union-attr]
            json_body={"title": "probe"},
        )
        return r.status_code

    def _wait_enforced(self, enabled: bool) -> None:
        expected = 403 if enabled else 404
        deadline = time.monotonic() + PROBE_TIMEOUT_S
        seen = None
        while time.monotonic() < deadline:
            seen = self._probe()
            if seen == expected:
                return
            time.sleep(0.5)
        raise TimeoutError(f"API still answers the flag probe with {seen}, expected {expected} (flag={enabled})")
