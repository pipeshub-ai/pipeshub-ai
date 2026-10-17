# pyright: ignore-file

"""A folder the Local FS connector syncs from, and the desktop upload API.

The integration compose stack mounts ``integration-tests/local_fs_root`` into
``pipeshub-ai`` at ``/srv/local-fs-it`` (read-only). The folder is committed to
the repository, so the checkout creates it owned by the user running the tests,
who can write to it; the connector, running as root in the container, reads it.
Each run works in its own subfolder, so runs never see each other's files.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
import unicodedata
from pathlib import Path
from typing import Any

from helper.run_folder import require_run_folder
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

INTEGRATION_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HOST_ROOT = INTEGRATION_ROOT / "local_fs_root"
DEFAULT_CONTAINER_ROOT = "/srv/local-fs-it"


def external_record_id(connector_id: str, rel_path: str) -> str:
    """The connector's record id for a path, as ``LocalFsConnector`` computes it."""
    normalized = unicodedata.normalize("NFC", rel_path.strip().replace("\\", "/"))
    return hashlib.sha256(f"{connector_id}:{normalized}".encode("utf-8")).hexdigest()


class LocalFsFolder:
    """The run's folder, seen from the test process and from the connector."""

    def __init__(self, host_root: Path, container_root: str) -> None:
        self.host_root = host_root
        self.container_root = container_root.rstrip("/")

    def check_available(self) -> None:
        if not self.host_root.is_dir():
            raise FileNotFoundError(f"{self.host_root} does not exist")
        probe = self.host_root / ".write-check"
        probe.write_text("ok")
        probe.unlink()

    def host_path(self, folder: str, rel_path: str = "") -> Path:
        return self.host_root / require_run_folder(folder) / rel_path

    def container_path(self, folder: str) -> str:
        return f"{self.container_root}/{require_run_folder(folder).rstrip('/')}"

    def write(self, folder: str, rel_path: str, text: str) -> None:
        path = self.host_path(folder, rel_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def delete(self, folder: str, rel_path: str) -> None:
        self.host_path(folder, rel_path).unlink()

    def clear_objects(self, _resource_name: str, folder: str) -> None:
        """Remove the run's folder (called by ``connector_lifecycle.destructor``)."""
        shutil.rmtree(self.host_path(folder), ignore_errors=True)


def upload_file_events(
    pipeshub_client: PipeshubClient,
    connector_id: str,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Send events the way the desktop app does: a manifest plus one part per file.

    Each event is ``{"type", "path", "content"?, "oldPath"?}``; events with
    content get a ``file_N`` part, its SHA-256 and size, as
    ``frontend/electron/local-sync/transport/file-event-dispatcher.ts`` builds them.
    """
    now = int(time.time() * 1000)
    manifest_events: list[dict[str, Any]] = []
    files: list[tuple[str, tuple[Any, ...]]] = []
    for event in events:
        entry: dict[str, Any] = {
            "type": event["type"],
            "path": event["path"],
            "timestamp": now,
            "isDirectory": False,
        }
        if event.get("oldPath"):
            entry["oldPath"] = event["oldPath"]
        if "content" in event:
            content = event["content"].encode()
            field = f"file_{len(files)}"
            files.append((field, (Path(event["path"]).name, content, "text/plain")))
            entry.update(
                contentField=field,
                sha256=hashlib.sha256(content).hexdigest(),
                size=len(content),
                mimeType="text/plain",
            )
        manifest_events.append(entry)

    manifest = {"batchId": f"it-batch-{now}", "events": manifest_events, "timestamp": now}
    # A part without a filename is a plain form field. Sending the manifest this
    # way keeps a delete-only batch multipart, as the desktop app sends it.
    parts: list[tuple[str, tuple[Any, ...]]] = [("manifest", (None, json.dumps(manifest)))]
    response = pipeshub_client.request(
        "POST",
        f"/api/v1/connectors/{connector_id}/file-events/upload",
        files=[*parts, *files],
    )
    assert response.status_code == 200, (
        f"file-events/upload returned {response.status_code}: {response.text[:500]}"
    )
    body = response.json()
    assert body.get("success") is True, f"file-events/upload did not succeed: {body}"
    return body
