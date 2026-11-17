"""Fakes for tests of the paid-result cache: an in-memory store with the storage routes' rules, a counting model,
and a binding of the record in flight."""

from __future__ import annotations

import hashlib
from collections import Counter
from contextlib import contextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from app.services.model_identity.call_context import ModelCallContext, bind_call_context
from app.services.model_identity.principal import service_principal
from app.services.model_identity.workload import Workload
from app.services.paid_cache.store import ListedFile

if TYPE_CHECKING:
    from collections.abc import Iterator

Path = tuple[str, str, str]  # (org, record, path inside the record's folder)


class MemoryResultStore:
    """What the storage service's paid-result routes do, in a dict.

    Files live under (org, record, path): another org's request can never name them. ``put`` keeps the first
    writer's bytes. ``clock`` is the "storage write time" the listing reports.
    """

    def __init__(self) -> None:
        self.files: dict[Path, tuple[bytes, int]] = {}
        self.now_ms = 1_700_000_000_000
        self.ops: Counter[str] = Counter()
        #: Set to make every call of this kind raise, like a storage outage: "get", "put", "list", "delete", "purge".
        self.failing: set[str] = set()
        #: Every (org, record) any call named, for isolation checks.
        self.touched: set[tuple[str, str]] = set()

    def _call(self, op: str, org: str, record: str | None = None) -> None:
        self.ops[op] += 1
        if record is not None:
            self.touched.add((org, record))
        if op in self.failing:
            raise ConnectionError(f"storage {op} unavailable")

    async def get(self, org_id: str, record_id: str, path: str) -> bytes | None:
        self._call("get", org_id, record_id)
        entry = self.files.get((org_id, record_id, path))
        return None if entry is None else entry[0]

    async def put(self, org_id: str, record_id: str, path: str, data: bytes) -> bool:
        self._call("put", org_id, record_id)
        if (org_id, record_id, path) in self.files:
            return False
        self.files[(org_id, record_id, path)] = (data, self.now_ms)
        return True

    async def replace(self, org_id: str, record_id: str, path: str, data: bytes) -> None:
        self._call("replace", org_id, record_id)
        self.files[(org_id, record_id, path)] = (data, self.now_ms)

    async def overwrite(self, org_id: str, record_id: str, path: str, data: bytes) -> None:
        self._call("overwrite", org_id, record_id)
        self.files[(org_id, record_id, path)] = (data, self.now_ms)

    async def list_files(self, org_id: str, record_id: str, prefix: str = "") -> list[ListedFile]:
        self._call("list", org_id, record_id)
        return sorted(
            (ListedFile(p, len(d), t) for (o, r, p), (d, t) in self.files.items()
             if o == org_id and r == record_id and p.startswith(prefix)),
            key=lambda f: f.path,
        )

    async def delete_files(self, org_id: str, record_id: str, paths: list[str]) -> None:
        self._call("delete", org_id, record_id)
        for p in paths:
            self.files.pop((org_id, record_id, p), None)

    async def purge_record(self, org_id: str, record_id: str) -> None:
        self._call("purge", org_id, record_id)
        for k in [k for k in self.files if k[0] == org_id and k[1] == record_id]:
            del self.files[k]

    async def purge_org(self, org_id: str) -> None:
        self._call("purge", org_id)
        for k in [k for k in self.files if k[0] == org_id]:
            del self.files[k]

    def paths(self, org: str, record: str) -> list[str]:
        return sorted(p for (o, r, p) in self.files if o == org and r == record)

    def results(self, org: str, record: str) -> list[str]:
        return [p for p in self.paths(org, record) if not p.startswith("manifests/")]


class CountingModel:
    """A model that answers deterministically and counts every request, as the fake provider of the T-dup tests.

    ``requests`` counts per (org, record, site, content digest, model): the key a duplicate paid call is
    measured by. The org and record are read from the ambient call context, as a real gateway request would.
    """

    def __init__(self, model_name: str = "fake-model", *, reply: Any = None, metadata: dict[str, Any] | None = None) -> None:  # noqa: ANN401 - a JSON value, model response or caller callable of any shape
        self.model_name = model_name
        self.reply = reply
        self.metadata = metadata or {}
        self.requests: Counter[tuple[str | None, str | None, str, str, str]] = Counter()
        #: Raised by the request with this 1-based ordinal (counting all requests), once.
        self.fail_request: dict[int, Exception] = {}
        self.total = 0

    @staticmethod
    def digest_of(messages: Any) -> str:  # noqa: ANN401 - a JSON value, model response or caller callable of any shape
        return hashlib.sha256(repr(messages).encode()).hexdigest()[:16]

    async def ainvoke(self, messages: Any, **_kwargs: Any) -> Any:  # noqa: ANN401 - a JSON value, model response or caller callable of any shape
        from app.services.model_identity.call_context import peek_call_context

        self.total += 1
        if (exc := self.fail_request.pop(self.total, None)) is not None:
            raise exc
        ctx = peek_call_context()
        org = ctx.principal.org_id if ctx else None
        record = ctx.record_id if ctx else None
        digest = self.digest_of(messages)
        self.requests[(org, record, "call", digest, self.model_name)] += 1
        content = self.reply(messages) if callable(self.reply) else (self.reply if self.reply is not None else f"text-{digest}")
        return SimpleNamespace(content=content, response_metadata=dict(self.metadata), additional_kwargs={})

    @property
    def duplicates(self) -> int:
        """Requests beyond the first for any (org, record, site, content digest, model)."""
        return sum(n - 1 for n in self.requests.values() if n > 1)


@contextmanager
def record_in_flight(org_id: str = "org-1", record_id: str = "rec-1") -> Iterator[None]:
    """Binds the model-call context the record's message handler binds."""
    ctx = ModelCallContext(
        principal=service_principal("indexing", org_id), workload=Workload.INDEXING, record_id=record_id,
    )
    with bind_call_context(ctx):
        yield
