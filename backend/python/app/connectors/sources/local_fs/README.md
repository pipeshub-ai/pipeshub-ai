# Local FS (Electron desktop, server-driven sync)

Local FS indexes files from a folder on the **user's machine**, watched by the **Electron desktop app**. The server never crawls `sync_root_path` itself — it cannot see the user's disk.

Unlike the earlier design, the desktop no longer pushes batches. `run_sync` is server-driven like every other connector and **pulls** pages of file-event *metadata* from the desktop. No file bytes cross the sync path; content is fetched on demand in `stream_record`.

## 1. Sync path

```
resync / scheduler → Kafka → run_sync()
                                ↓  HTTP, scoped JWT (desktop:command)
                       Node desktop route
                                ↓  Socket.IO
                             Electron
```

1. `run_sync` reads its sync point. **No `last_sync_time` ⇒ `FULL`**, otherwise `INCREMENTAL` resuming from the stored `cursor`. `event_service` deletes sync points before a user-requested full sync, which is what makes the Full Sync button work here without a parameter. The sync point holds progress only; the owner device lives on the app document, so a full sync never changes who owns the folder.
2. For each page it POSTs to the Node relay:

   ```text
   POST /api/v1/desktop/internal/local-fs/file-events/pull
   { connectorId, deviceId, runId, batchIndex, mode, cursor, maxEvents, timeoutMs }
   ```

   `deviceId` is the connector's owner device (`ownerDeviceId` on the app document). Node relays the pull only to that device's socket and returns its answer: `{ connectorId, runId, batchIndex, deviceId, cursor, hasMore, events[], rootPath }`. `connectorId` and `deviceId` are echoed so a run can still refuse a reply from anyone but the owner.
3. `_apply_file_event_batch` upserts graph `FileRecord`s via `DataSourceEntitiesProcessor.on_new_records` and retires records for `DELETED` and superseded `RENAMED`/`MOVED` paths. The new record always lands **before** the old row is removed, so a mid-batch failure cannot lose data.
4. The `cursor` is persisted after every applied page, so a crash costs at most one page of re-work. `last_sync_time` is written only when the desktop reports `hasMore: false` and every page applied cleanly.
5. A `FULL` run tracks the external ids it observed and prunes anything absent **after** the run completes — a run that dies midway prunes nothing and the previous snapshot stays live.

Events carry `type`, `path`, `oldPath`, `timestamp`, `size`, `isDirectory`, `sha256`, `mimeType`. Since no bytes reach the server, `sha256` is the only change-detection signal.

### Contract the desktop must honour

- `cursor` is **opaque to the server** and denotes the position *after* the returned events. It must be a sequence/position, not a timestamp — a wall-clock cursor loses or duplicates events landing on the boundary millisecond. The desktop encodes the mode into the token (`{"v":1,"mode":"INCREMENTAL","afterBatchId":…}` / `{"v":1,"mode":"FULL","afterPath":…}`, base64'd) because a disk-walk position and a journal position are not interchangeable.
- A repeat of `(runId, batchIndex)` must return the identical previous page from an idempotency cache without advancing. This is what makes server-side retry safe — `_pull_with_retry` re-sends the same indices deliberately.
- A page is **not** acked when served. The next pull arriving with a cursor past it is the ack; committing on send loses data whenever a run dies mid-flight.
- A newer `runId` supersedes an in-flight one; an older one gets `STALE_RUN`.
- The **final** `FULL` page returns an *incremental* cursor. The server persists it and its next run is `INCREMENTAL`; a `FULL` token there would fail the mode check on every run and pin the connector to a full sync forever.
- Ack within `timeoutMs`. If enumeration is slow, answer `{ events: [], hasMore: true, cursor: <unchanged> }` as a keepalive rather than blocking. A run with nothing to send must still answer one page with `hasMore: false`.

### Ownership

Two machines signed into one account both see the connector, because `sync_root_path` lives server-side. Only the owner device syncs it:

- **Owner on the app document** — `ownerDeviceId` / `ownerDeviceName` are flat fields on the connector (app) document. They are claimed the first time sync is enabled from the desktop app: `POST /api/v1/connectors/{id}/toggle` carries `deviceId` / `deviceName`, and Python writes them with the `isActive` update. Enabling with no owner and no `deviceId` is refused with `409 DESKTOP_UNCLAIMED`; enabling from any other device is refused with `409 DESKTOP_OWNED_BY_OTHER_DEVICE`.
- **Routing by device** — every pull and content fetch carries `deviceId = ownerDeviceId`, and Node sends it to that device's socket only. Another machine for the same user never receives it, whether or not the owner is online; an offline owner is `DESKTOP_OFFLINE`. `_request_file_event_batch` still rejects a page stamped with any other device as `DEVICE_MISMATCH`, as a backstop against a relay bug.

  The desktop's `deviceId` is derived from the OS machine id, so reinstalling the app keeps it. A connector with no owner (`run_sync` or content fetch) fails with the non-retryable `DESKTOP_UNCLAIMED` and the user is notified. Moving a folder to another machine is not supported yet: delete the connector and create a new one there.

If the configured folder is moved, renamed, or deleted, the desktop answers `ROOT_MISSING` (non-retryable). The connector does not invent a new path and does not prune indexed records — relative paths stay valid once the user points the connector at the new location. `run_sync` notifies and aborts, as it does for `DEVICE_MISMATCH`. A missing folder must not surface as `DESKTOP_OFFLINE`: that code means no socket. Nothing else is remapped to it either — retries exhausted against a reachable desktop raise `LocalFsDesktopUnreachableError` carrying the code that actually failed (`DESKTOP_TIMEOUT`, `NODE_UNREACHABLE`, `INTERNAL`), which notifies and fails the run instead of skipping it silently.

## 2. Content and indexing

`stream_record` serves two record shapes:

- records created by the retired push flow keep a `storage://<documentId>` path and stream from object storage;
- everything since carries a relative path and is fetched from the desktop.

The desktop acks `localfs:content:fetch` with the file's size and mime type, then pushes the bytes as ordered `localfs:content:chunk` frames (256KB, under socket.io's 1MB `maxHttpBufferSize`); Node buffers them by `requestId` and returns `application/octet-stream`. Content has its own budget constants (`LOCAL_FS_CONTENT_*`) rather than reusing the pull's — those are sized for a page of metadata and a large file blows straight through them.

When the desktop is unreachable the fetch surfaces as **503**, which the indexing consumer classifies as TRANSIENT and retries — rather than a 4xx, which would burn the record at `FAILED`. A file the desktop can no longer read is reported non-retryable and becomes a **404**, which is terminal so the consumer stops.

The desktop re-validates that the server-supplied `relPath` resolves inside the configured sync root before reading. The server owns that string, so treating it as trusted would be a path-traversal hole.

> Upgrading from the phase where `LOCAL_FS_DESKTOP_CONTENT_AVAILABLE` was `False`: existing rows were created at `indexing_status = AUTO_INDEX_OFF` and stay there. Re-drive them once with a reindex filtered on `AUTO_INDEX_OFF` (`POST /api/v1/connectors/{id}/reindex` with that status filter, which routes to `reindex_records`).

## 3. Web flow

1. Create or open a **personal** Local FS connector instance in the web app.
2. Set the **local folder path** (resolved on the machine running the desktop app, not the server) and sync options, then save.
3. **Activate** the connector from the desktop app on the machine that has the folder; that machine becomes the owner device.
4. Press Sync, or wait for the scheduled tick — both reach `run_sync` through the normal resync/crawl paths, from the browser as well as the desktop app. Only setup (picking a folder) and the first activation need Electron.

If the desktop is offline, Node refuses Sync and toggle-on up front with `409 DESKTOP_OFFLINE` (checked against the owner device's socket before the job is queued) and skips a scheduled tick quietly. A pull that still finds no desktop mid-run (service boot, Kafka-driven runs, a desktop that dropped after the check) is skipped with a warning, not failed: no sync point is written and no records are pruned, and the connector returns to IDLE. A desktop that *is* connected but cannot serve the run — timeouts, a 502 from the relay, an unreachable Node endpoint — is a failure, not a skip: it notifies and `run_sync` raises, so the run is recorded as failed rather than logged as finished. The web UI shows a "Desktop offline" badge from live presence, which Node stamps as `desktopOnline` on Local FS rows of the instance GET responses; nothing about the outcome is persisted.

## 4. Auth

The pull and content routes are service-to-service. Python mints a scoped JWT with the `desktop:command` scope (`generate_jwt`), and Node guards the routes with `scopedTokenValidator(TokenScopes.DESKTOP_COMMAND)`. `orgId`/`userId` come from the token, never the request body — a body-supplied user id would be a cross-tenant targeting primitive.

The desktop side authenticates separately: the renderer hands its **access token** to the Electron main process over IPC at login and again on every refresh, and main presents it on the socket handshake. Main holds it in memory only and cannot mint one itself — its `deviceId` is derived from the OS machine id, not stored, and is what the connector's `ownerDeviceId` names. Local FS therefore syncs while the app is running, not as a background daemon: a machine with no app open reports `DESKTOP_OFFLINE` and its scheduled ticks are skipped, not failed.

## 5. Known limits

1. **Single Node replica only.** Socket registration is process-local — there is no socket.io Redis adapter on the `/rest-proxy` namespace. A desktop connected to one Node replica is invisible to a pull that lands on another, which then answers `DESKTOP_OFFLINE` for a machine that is plainly connected. A multi-replica Node deployment needs that adapter before Local FS works there.

2. **A `FULL` run cannot resume; it restarts.** Only an `INCREMENTAL` run resumes from the stored `cursor`, and a run is `INCREMENTAL` only once a `FULL` one has finished and written `last_sync_time`. So while the first sync is still running, any interruption — crash, desktop drop, `RUN_TOO_LONG`, `TOO_MANY_BATCHES` — sends the next run back to the start of the folder. Nothing is lost (records already written stay, and pruning only happens after a run completes), but a large first sync that keeps being interrupted never gets past its opening pages. The same applies to any later `FULL` run, such as one started by the Full Sync button.
