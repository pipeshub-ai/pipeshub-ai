"""In-memory fakes of the graph and the storage service for the stored-record retention sweep, shared by its
unit tests and the PostgreSQL membership tests."""

from __future__ import annotations

from typing import Any

from app.modules.artifacts.stored_retention import RETIRE_GRACE_MS

NOW = 10_000_000_000
GR = RETIRE_GRACE_MS
ORG = "org-1"


class FakeGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.cas: list[tuple[dict, dict]] = []

    def add(self, vrid: str, **fields: Any) -> dict[str, Any]:  # noqa: ANN401
        node = {
            "id": vrid, "orgId": ORG, "documentId": "d-mapped", "switchDoc": "d-mapped", "switchSeq": 5,
            "updatedAt": 1, "switchUpdatedAt": 1, "lastSwitchAt": NOW - 10 * GR, "retired": [], **fields,
        }
        self.nodes[vrid] = node
        return node

    async def get_switch_pointers(self, after_key: str | None, limit: int) -> list[dict[str, Any]]:
        keys = sorted(k for k in self.nodes if after_key is None or k > after_key)[:limit]
        return [{
            "virtualRecordId": k, "orgId": self.nodes[k]["orgId"], "documentId": self.nodes[k].get("documentId"),
            "switchSeq": self.nodes[k].get("switchSeq"), "lastSwitchAt": self.nodes[k].get("lastSwitchAt"),
            "retired": list(self.nodes[k].get("retired") or []),
        } for k in keys]

    async def get_document(self, key: str, collection: str, **_: Any) -> dict[str, Any] | None:  # noqa: ANN401
        node = self.nodes.get(key)
        return None if node is None else {**node, "retired": list(node.get("retired") or [])}

    async def update_node_fields_if_match(self, key: str, collection: str, updates: dict, expected: dict, *_: Any) -> bool:  # noqa: ANN401
        node = self.nodes.get(key)
        self.cas.append((updates, expected))
        if node is None or any(node.get(k) != v for k, v in expected.items()):
            return False
        node.update(updates)
        return True


class FakeBlob:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.purged: list[str] = []
        self.retire_outcome = "marked"
        self.marks: list[str] = []
        self.listing: list[dict[str, Any]] = []
        self.listed_with: list[int | None] = []

    async def _get_document_row(self, org_id: str, document_id: str) -> dict[str, Any] | None:
        row = self.rows.get(document_id)
        return None if row is None else dict(row)

    async def _retire_document(self, org_id: str, document_id: str) -> str:
        self.marks.append(document_id)
        if self.retire_outcome == "marked":
            self.rows[document_id]["retiredAt"] = NOW
        return self.retire_outcome

    async def purge_document(self, org_id: str, document_id: str) -> int:
        self.purged.append(document_id)
        self.rows.pop(document_id, None)
        return 1

    async def list_record_documents(self, org_id: str, vrid: str, *, older_than_seconds: int | None = None) -> list[dict[str, Any]]:
        self.listed_with.append(older_than_seconds)
        return list(self.listing)


def retired_entry(doc: str, age: int) -> dict[str, Any]:
    return {"doc": doc, "rev": "r", "sidecar": None, "at": NOW - age}
