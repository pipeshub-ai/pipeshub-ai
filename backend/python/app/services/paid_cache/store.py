"""The storage service's ``/internal/paid-results`` routes, as the cache uses them.

The service signs one URL per file; the bytes go straight to S3 or Azure (or, on local storage, to a
storage-service route). A signed URL is a bearer credential: nothing here logs one or puts one in an error.

A storage service without the routes (404/405, e.g. mid-upgrade) turns the cache off for ``OFF_SECONDS``
with one warning; indexing is unaffected.
"""

import asyncio
import logging
import threading
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import aiohttp
from yarl import URL

from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import Routes
from app.modules.artifacts.models import SignedFileUrl
from app.modules.artifacts.store import ArtifactRoutesUnavailable, NodeArtifactStore
from app.modules.transformers.blob_storage import (
    _storage_status_error,
    get_shared_session,
)
from app.telemetry.backend import METRICS_BACKEND

if TYPE_CHECKING:
    from app.modules.transformers.blob_storage import BlobStorage

#: The storage service's cap on files in one presign, batch delete or listing page.
MAX_FILES_PER_CALL = 1000
OFF_SECONDS = 600.0

#: S3 answers a lost conditional PUT with 412, or with 409 while another write of the key is in flight.
#: S3 answers a conditional write that raced a delete or another write of the key with 409 and this code; Azure's 409
#: for an existing blob is a plain lost race.
_S3_CONDITIONAL_CONFLICT = b"ConditionalRequestConflict"
_CONFLICT_ATTEMPTS = 3
_CONFLICT_PAUSE_S = 0.05
_LOST_RACE = frozenset({HttpStatusCode.PRECONDITION_FAILED.value, HttpStatusCode.CONFLICT.value})
#: A signed GET of a missing key is 403 when the signer may not list the bucket.
_ABSENT = frozenset({HttpStatusCode.NOT_FOUND.value, HttpStatusCode.FORBIDDEN.value})


#: The most ids one batch-purge request names (the storage service refuses more).
PURGE_BATCH_MAX = 100
#: Folder purges in flight at once when a batch goes one id at a time (the storage service without the batch route).
PURGE_FALLBACK_CONCURRENCY = 8
#: One batch of 100 folders is bounded in total by this, so a stuck storage call hands its ids to the retry path.
PURGE_BATCH_TIMEOUT_S = 60.0
#: A storage service seen without the batch route is asked again after this long, doubling each time it is still
#: missing, up to the cap. Short on purpose: the route arrives with a storage upgrade, and the per-id loop is only slower.
BATCH_REPROBE_BASE_S = 30.0
BATCH_REPROBE_MAX_S = 300.0

PURGE_REQUESTS = METRICS_BACKEND.counter(
    "pipeshub_paid_purge_storage_requests_total",
    "Storage-service requests made to purge paid-result folders, by route (batch carries up to 100 records, record one)",
    ["route"],
)
PURGE_RECORDS = METRICS_BACKEND.counter(
    "pipeshub_paid_purge_records_total",
    "Paid-result folders purged, by the route that purged them and the outcome",
    ["route", "outcome"],
)


class PaidResultStoreError(Exception):
    """A storage reply this client cannot use. Never carries a signed URL."""


@dataclass(frozen=True, slots=True)
class ListedFile:
    path: str
    size: int
    last_modified_ms: int


class _Availability:
    """Process-wide: once the storage service is seen without the routes, stop asking for a while."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._off_until = 0.0

    @property
    def available(self) -> bool:
        return self._clock() >= self._off_until

    def mark_unavailable(self) -> bool:
        """True when this call turned the cache off (the caller warns once)."""
        with self._lock:
            was_available = self._clock() >= self._off_until
            self._off_until = self._clock() + OFF_SECONDS
            return was_available

    def reset(self) -> None:
        with self._lock:
            self._off_until = 0.0


PAID_RESULT_ROUTES = _Availability()


class _BatchRoute:
    """Process-wide memo that the storage service has no batch-purge route. Unlike ``_Availability`` it never switches
    the cache off: it only sends purges through the per-record route, and asks again after a growing pause."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._retry_at = 0.0
        self._misses = 0

    def usable(self) -> bool:
        return self._clock() >= self._retry_at

    def missing(self) -> bool:
        """Records a miss. True when this is the first since the route last answered (the caller warns once)."""
        with self._lock:
            first = self._misses == 0
            self._misses += 1
            # The exponent is capped first: 2 ** 1024 is no float, and a route missing for that long must still fall back.
            pause = min(BATCH_REPROBE_BASE_S * 2 ** min(self._misses - 1, 16), BATCH_REPROBE_MAX_S)
            self._retry_at = self._clock() + pause
            return first

    def present(self) -> None:
        with self._lock:
            self._retry_at = 0.0
            self._misses = 0

    def reset(self) -> None:
        self.present()


PURGE_BATCH_ROUTE = _BatchRoute()


class NodePaidResultStore:
    def __init__(self, blob_storage: "BlobStorage", logger: logging.Logger | None = None) -> None:
        self._blob_storage = blob_storage
        self.logger = logger or logging.getLogger(__name__)
        self._cutoff_route_warned = False
        # Reuses the artifact client's reply handling and its URL transfer helpers.
        self._artifacts = NodeArtifactStore(blob_storage, self.logger)

    # -- routes ---------------------------------------------------------------------------------

    async def _call(
        self, org_id: str, method: str, route: str, *, body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None, idempotent: bool = True, latch: bool = True,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        headers, endpoint, _ = await self._blob_storage._get_auth_and_config(org_id)
        timeout = None if timeout_s is None else aiohttp.ClientTimeout(total=timeout_s)

        async def attempt() -> dict[str, Any]:
            async with get_shared_session().request(
                method, f"{endpoint}{route}", json=body, params=params, headers=headers,
                **({} if timeout is None else {"timeout": timeout}),
            ) as response:
                return await self._artifacts._json_reply(response, route, missing_route=True)

        try:
            return await self._blob_storage._with_storage_retry(
                f"paid-result request {method} {route}", attempt, idempotent=idempotent,
            )
        except ArtifactRoutesUnavailable as exc:
            # ``latch`` False: a route that is newer than the others says nothing about whether the cache exists.
            if latch and PAID_RESULT_ROUTES.mark_unavailable():
                self.logger.warning(
                    "Storage has no paid-result routes (HTTP %d); the paid-result cache is off for %d s",
                    exc.status, int(OFF_SECONDS),
                )
            raise

    async def presign(
        self, org_id: str, record_id: str, files: Sequence[tuple[str, str]],
    ) -> dict[tuple[str, str], SignedFileUrl]:
        """Signed URLs for ``(path, mode)`` files, ``mode`` one of ``put``, ``get``, ``replace`` (a manifest),
        ``refresh`` (a result object, overwritten)."""
        signed: dict[tuple[str, str], SignedFileUrl] = {}
        for start in range(0, len(files), MAX_FILES_PER_CALL):
            chunk = files[start:start + MAX_FILES_PER_CALL]
            body = await self._call(
                org_id, "POST", Routes.STORAGE_PAID_RESULTS_PRESIGN.value,
                body={"recordId": record_id, "files": [{"path": p, "mode": m} for p, m in chunk]},
            )
            answered = body.get("files")
            if not isinstance(answered, list):
                raise PaidResultStoreError("presign answered no files")
            for entry in answered:
                if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not entry.get("url"):
                    raise PaidResultStoreError("storage answered a malformed paid-result URL entry")
                mode = entry.get("mode")
                headers = entry.get("headers") or {}
                if mode not in ("put", "get", "replace", "refresh") or not isinstance(headers, dict):
                    raise PaidResultStoreError("storage answered a malformed paid-result URL entry")
                signed[(entry["path"], str(mode))] = SignedFileUrl(
                    url=str(entry["url"]),
                    method="GET" if mode == "get" else "PUT",
                    headers={str(k): str(v) for k, v in headers.items()},
                )
            if {(p, m) for p, m in chunk} - signed.keys():
                raise PaidResultStoreError("presign left a file unsigned")
        return signed

    async def list_files(self, org_id: str, record_id: str, prefix: str = "") -> list[ListedFile]:
        """Every file of the record under ``prefix`` (a kind folder or the manifests), all pages."""
        files: list[ListedFile] = []
        after: str | None = None
        route = Routes.STORAGE_PAID_RESULTS_RECORD.value.format(recordId=record_id)
        while True:
            params = {k: v for k, v in (("prefix", prefix), ("after", after)) if v}
            body = await self._call(org_id, "GET", route, params=params)
            objects = body.get("objects")
            if not isinstance(objects, list):
                raise PaidResultStoreError("listing answered no objects")
            files.extend(
                ListedFile(o["path"], int(o.get("size") or 0), int(o.get("lastModifiedMs") or 0))
                for o in objects
                if isinstance(o, dict) and isinstance(o.get("path"), str)
            )
            nxt = body.get("next")
            if not isinstance(nxt, str) or not nxt:
                return files
            after = nxt

    async def delete_files(self, org_id: str, record_id: str, paths: Sequence[str]) -> None:
        route = Routes.STORAGE_PAID_RESULTS_DELETE.value.format(recordId=record_id)
        for start in range(0, len(paths), MAX_FILES_PER_CALL):
            await self._call(
                org_id, "POST", route, body={"paths": list(paths[start:start + MAX_FILES_PER_CALL])},
            )

    async def purge_record(self, org_id: str, record_id: str, *, before_ms: int | None = None) -> None:
        """Removes the record's folder. Nothing stored is a success (the route answers 200).

        ``before_ms``: only objects last written before that instant go, so a DELETE that lands late (its caller lost
        its lease) leaves what a newer purge's run stored since. A storage service without the routes never held a
        result, so it is a success too.
        """
        try:
            await self._call(
                org_id, "DELETE", Routes.STORAGE_PAID_RESULTS_RECORD.value.format(recordId=record_id),
                params={"before": str(int(before_ms))} if before_ms is not None else None,
            )
        except ArtifactRoutesUnavailable:
            return

    async def purge_records(self, org_id: str, record_ids: Sequence[str]) -> dict[str, BaseException | None]:
        """Removes the folders of many records, through the batch route where the storage service has one (up to
        ``PURGE_BATCH_MAX`` ids per request) and one id at a time where it has not.

        One entry per id: ``None`` for a purge that is done (nothing stored is done too), else the error, so a caller
        repeats only the failures. Never raises for a failure of one request: its ids get the error. A 404/405 on the
        batch route means an older storage service: that chunk goes through the per-record route (the cache stays on),
        the miss is remembered for a short, growing pause, and the route is asked again after it. Any other failure is
        a retryable one for the chunk's ids, and says nothing about the route.
        """
        ids = list(dict.fromkeys(record_ids))
        outcomes: dict[str, BaseException | None] = {}
        for start in range(0, len(ids), PURGE_BATCH_MAX):
            chunk = ids[start:start + PURGE_BATCH_MAX]
            if PURGE_BATCH_ROUTE.usable():
                try:
                    outcomes.update(await self._purge_chunk(org_id, chunk))
                    continue
                except ArtifactRoutesUnavailable as exc:
                    if PURGE_BATCH_ROUTE.missing():
                        self.logger.warning(
                            "Storage has no paid-result batch purge route (HTTP %d): purging one record at a time, "
                            "and asking again after a pause; update the storage service", exc.status,
                        )
                except Exception as exc:
                    outcomes.update(dict.fromkeys(chunk, exc))
                    PURGE_RECORDS.inc("batch", "failed", value=float(len(chunk)))
                    continue
            outcomes.update(await self._purge_one_by_one(org_id, chunk))
        return outcomes

    async def _purge_chunk(self, org_id: str, chunk: list[str]) -> dict[str, BaseException | None]:
        PURGE_REQUESTS.inc("batch")          # counted when sent: a probe the storage service answers 404 is a request too
        # The deadline is for the whole call, retries included (the per-request timeout alone is reset by each retry).
        body = await asyncio.wait_for(
            self._call(
                org_id, "POST", Routes.STORAGE_PAID_RESULTS_PURGE_BATCH.value, body={"recordIds": chunk},
                latch=False, timeout_s=PURGE_BATCH_TIMEOUT_S,
            ),
            timeout=PURGE_BATCH_TIMEOUT_S,
        )
        PURGE_BATCH_ROUTE.present()
        answered: dict[str, bool] = {}
        results = body.get("results")
        if isinstance(results, list):
            for entry in results:
                if isinstance(entry, dict) and isinstance(entry.get("recordId"), str):
                    answered[entry["recordId"]] = entry.get("purged") is True
        outcomes: dict[str, BaseException | None] = {}
        for rid in chunk:
            # An id the reply leaves out, or does not confirm, is not purged: it is a failure to repeat.
            outcomes[rid] = None if answered.get(rid) else PaidResultStoreError("batch purge did not confirm the record")
        done = sum(1 for e in outcomes.values() if e is None)
        PURGE_RECORDS.inc("batch", "purged", value=float(done))
        PURGE_RECORDS.inc("batch", "failed", value=float(len(chunk) - done))
        return outcomes

    async def _purge_one_by_one(self, org_id: str, chunk: list[str]) -> dict[str, BaseException | None]:
        gate = asyncio.Semaphore(PURGE_FALLBACK_CONCURRENCY)
        outcomes: dict[str, BaseException | None] = {}

        async def one(rid: str) -> None:
            async with gate:
                PURGE_REQUESTS.inc("record")
                try:
                    await self.purge_record(org_id, rid)
                    outcomes[rid] = None
                    PURGE_RECORDS.inc("record", "purged")
                except Exception as exc:
                    outcomes[rid] = exc
                    PURGE_RECORDS.inc("record", "failed")

        await asyncio.gather(*(one(rid) for rid in chunk))
        return outcomes

    async def purge_cutoff(self, org_id: str, record_id: str) -> int | None:
        """The object store's own current time (ms), as the cutoff of a purge: no caller's clock decides what goes.

        ``None`` when the storage service has no such route (one of an earlier release): that says nothing about the
        cache, so the routes are not switched off, and the caller purges without a cutoff.
        """
        try:
            body = await self._call(
                org_id, "POST", Routes.STORAGE_PAID_RESULTS_PURGE_CUTOFF.value.format(recordId=record_id), latch=False,
            )
        except ArtifactRoutesUnavailable as exc:
            if not self._cutoff_route_warned:
                self._cutoff_route_warned = True
                self.logger.warning(
                    "Storage has no purge-cutoff route (HTTP %d): a refresh purge goes without a cutoff there, or is "
                    "handed back while paid-result writes are on; update the storage service", exc.status,
                )
            return None
        return int(body["cutoff"])

    async def purge_org(self, org_id: str) -> None:
        try:
            await self._call(org_id, "DELETE", Routes.STORAGE_PAID_RESULTS_ORG.value)
        except ArtifactRoutesUnavailable:
            return

    # -- one file: presign, then transfer ---------------------------------------------------------

    async def get(self, org_id: str, record_id: str, path: str) -> bytes | None:
        signed = await self.presign(org_id, record_id, [(path, "get")])
        return await self.get_if_present(signed[(path, "get")])

    async def put(self, org_id: str, record_id: str, path: str, data: bytes) -> bool:
        """Store once. False when the file is already there: the first writer's bytes are kept."""
        signed = await self.presign(org_id, record_id, [(path, "put")])
        return await self.put_to(signed[(path, "put")], data)

    async def replace(self, org_id: str, record_id: str, path: str, data: bytes) -> None:
        """Store over what is there (a writer's own manifest; the service signs it for manifest paths only)."""
        signed = await self.presign(org_id, record_id, [(path, "replace")])
        await self.put_to(signed[(path, "replace")], data)

    async def overwrite(self, org_id: str, record_id: str, path: str, data: bytes) -> None:
        """Store over one result object (a refresh run's, or one found unreadable); signed for that key only."""
        signed = await self.presign(org_id, record_id, [(path, "refresh")])
        await self.put_to(signed[(path, "refresh")], data)

    # -- bytes ----------------------------------------------------------------------------------

    async def put_to(self, entry: SignedFileUrl, data: bytes) -> bool:
        """Upload with exactly the signed headers. False when the file already exists (first writer won)."""
        if entry.method != "PUT":
            raise ValueError("not an upload URL")
        headers = dict(entry.headers)

        async def attempt() -> bool:
            for conflict in range(_CONFLICT_ATTEMPTS):
                try:
                    async with get_shared_session().put(
                        # encoded=True: re-quoting the query would break the signature.
                        URL(entry.url, encoded=True), data=data, headers=headers, skip_auto_headers={"Content-Type"},
                    ) as response:
                        body = await response.read()
                        status = response.status
                except aiohttp.InvalidURL:
                    # The exception text carries the whole signed URL.
                    raise PaidResultStoreError("paid-result upload URL is malformed") from None
                if status == HttpStatusCode.CONFLICT.value and _S3_CONDITIONAL_CONFLICT in body:
                    # S3: a concurrent delete or write of the key is in progress; the object is not the first
                    # writer's. The request is retried, and after the last attempt reads as a lost race.
                    if conflict + 1 < _CONFLICT_ATTEMPTS:
                        await asyncio.sleep(_CONFLICT_PAUSE_S * (conflict + 1))
                        continue
                    return False
                if status in _LOST_RACE:
                    return False
                if not 200 <= status < 300:
                    raise _storage_status_error(status, f"paid-result upload failed (HTTP {status})")
                return True
            return False

        return bool(await self._blob_storage._with_storage_retry("paid-result upload", attempt))

    async def get_if_present(self, entry: SignedFileUrl) -> bytes | None:
        if entry.method != "GET":
            raise ValueError("not a download URL")

        async def attempt() -> bytes | None:
            try:
                async with get_shared_session().get(URL(entry.url, encoded=True), headers=dict(entry.headers)) as response:
                    if response.status in _ABSENT:
                        await response.read()
                        return None
                    if not 200 <= response.status < 300:
                        await response.read()
                        raise _storage_status_error(response.status, f"paid-result download failed (HTTP {response.status})")
                    return await response.read()
            except aiohttp.InvalidURL:
                raise PaidResultStoreError("paid-result download URL is malformed") from None

        result: Awaitable[Any] = self._blob_storage._with_storage_retry("paid-result download", attempt)
        return await result  # type: ignore[no-any-return]
