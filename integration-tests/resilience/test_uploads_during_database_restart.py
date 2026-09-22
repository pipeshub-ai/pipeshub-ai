"""Uploads made while MongoDB is down either land whole or fail cleanly.

MongoDB holds each upload's storage document, so it is on the path of every
upload. This test stops MongoDB, confirms it is really down, keeps uploading
to a fresh knowledge base for a while, then starts it again and checks each
upload's outcome against what the product holds:

  * an upload reported as saved is listed once and reaches COMPLETED;
  * an upload reported as failed left nothing behind in the knowledge base,
    and uploading it again succeeds;
  * no upload hangs past the client's timeout;
  * uploads made once MongoDB is back succeed.

Only uploads made while MongoDB was confirmed down count as meeting the
outage; the test fails if none did.

    RESILIENCE_MONGO_DOWN_SEC  how long MongoDB stays down (default 20)
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid

import pytest
import requests

from helper.clients.kb_client import KBClient
from helper.compose_control import ComposeStack
from helper.indexing_progress import document, wait_until_finished
from helper.stored_names import stored_name
from pipeshub_client import PipeshubClientError

logger = logging.getLogger("resilience")

pytestmark = [pytest.mark.resilience, pytest.mark.asyncio(loop_scope="session")]

DOWN_FOR = float(os.getenv("RESILIENCE_MONGO_DOWN_SEC", "20"))
AFTER_RESTART_UPLOADS = 2


def _upload(kb_client: KBClient, kb_id: str, name: str) -> tuple[str, str | None, str]:
    """("saved", record id, "") or ("failed", None, why). A hang raises."""
    token = name.split("-", 1)[1].split(".", 1)[0]
    try:
        result = kb_client.upload_file(kb_id, name, document(token), mimetype="text/markdown")
    except (PipeshubClientError, requests.HTTPError) as exc:
        return "failed", None, str(exc)
    if result["summary"]["failed"] or not result["records"]:
        return "failed", None, str(result.get("failed"))
    return "saved", result["records"][0]["recordId"], ""


class _Outage(threading.Thread):
    """Stops MongoDB, holds it down, starts it again; ``down`` is set only while it is confirmed stopped."""

    def __init__(self, compose: ComposeStack) -> None:
        super().__init__(name="mongodb-outage", daemon=True)
        self.compose = compose
        self.down = threading.Event()
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            self.compose.stop("mongodb")
            self.down.set()
            time.sleep(DOWN_FOR)
        except BaseException as exc:  # noqa: BLE001 - reported on the main thread
            self.error = exc
        finally:
            self.down.clear()
            try:
                self.compose.start("mongodb")
                self.compose.wait_ready("mongodb")
            except BaseException as exc:  # noqa: BLE001 - reported on the main thread
                self.error = self.error or exc


async def test_uploads_while_the_database_is_down_land_whole_or_fail_cleanly(
    compose: ComposeStack,
    kb_client: KBClient,
) -> None:
    kb_id = kb_client.create_kb(f"resilience-mongo-{uuid.uuid4().hex[:8]}")["id"]
    outage = _Outage(compose)
    try:
        outcomes: dict[str, tuple[str, str | None, str]] = {}
        during: list[str] = []
        outage.start()
        assert outage.down.wait(timeout=180), f"MongoDB was never confirmed down: {outage.error}"
        while outage.down.is_set():
            name = f"upload-{uuid.uuid4().hex[:12]}.md"
            started_while_down = outage.down.is_set()
            try:
                outcomes[name] = _upload(kb_client, kb_id, name)
            except requests.Timeout as exc:
                pytest.fail(f"an upload while MongoDB was down hung until the client gave up: {exc}")
            if started_while_down:
                during.append(name)
            time.sleep(0.5)
        outage.join(timeout=300)
        assert not outage.is_alive(), "MongoDB did not come back within 300s"
        assert outage.error is None, f"the MongoDB outage did not run cleanly: {outage.error}"
        assert during, "no upload was made while MongoDB was confirmed down; nothing was tested"

        after = [f"upload-{uuid.uuid4().hex[:12]}.md" for _ in range(AFTER_RESTART_UPLOADS)]
        for name in after:
            outcomes[name] = _upload(kb_client, kb_id, name)

        failed = {n: why for n, (state, _, why) in outcomes.items() if state == "failed"}
        saved = {n: rid for n, (state, rid, _) in outcomes.items() if state == "saved"}
        logger.info("%d upload(s) while MongoDB was down; %d failed: %s", len(during), len(failed), failed)
        assert not set(after) & set(failed), (
            f"uploads made after MongoDB was back still failed: { {n: failed[n] for n in after if n in failed} }"
        )

        final = await wait_until_finished(kb_client, list(saved.values()))
        stuck = {n: final[rid] for n, rid in saved.items() if final[rid] != "COMPLETED"}
        assert not stuck, f"uploads reported as saved never finished indexing: {stuck}"

        # A record is stored under the file name without its extension, so
        # comparing against the uploaded names would fail on every upload that
        # was saved and indexed perfectly well.
        listed = [item["name"] for item in kb_client.list_records(kb_id).get("items") or []]
        expected = sorted(stored_name(name) for name in saved)
        assert sorted(listed) == expected, (
            f"the knowledge base lists {sorted(listed)}; expected exactly the {len(saved)} saved uploads "
            f"({expected}) and none of the {len(failed)} that failed ({sorted(failed)})"
        )

        retried = {}
        for name in failed:
            state, record_id, why = _upload(kb_client, kb_id, name)
            assert state == "saved", f"uploading {name} again after MongoDB was back failed: {why}"
            retried[name] = record_id
        final = await wait_until_finished(kb_client, list(retried.values()))
        stuck = {n: final[rid] for n, rid in retried.items() if final[rid] != "COMPLETED"}
        assert not stuck, f"uploads retried after the restart never finished indexing: {stuck}"
    finally:
        # MongoDB must be running again before anything else touches the stack.
        if outage.ident is not None:
            outage.join(timeout=300)
        try:
            kb_client.delete_kb(kb_id)
        except Exception as exc:  # noqa: BLE001 - teardown must not mask the result
            logger.warning("Could not delete knowledge base %s: %s", kb_id, exc)
