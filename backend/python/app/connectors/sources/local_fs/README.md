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

1. `run_sync` reads its sync point. **No `last_sync_time` ⇒ `FULL`**, otherwise `INCREMENTAL` resuming from the stored `cursor`. `event_service` deletes sync points before a user-requested full sync, which is what makes the Full Sync button work here without a parameter.
2. For each page it POSTs to the Node relay:

   ```text
   POST /api/v1/desktop/internal/local-fs/file-events/pull
   { connectorId, runId, batchIndex, mode, cursor, maxEvents, timeoutMs }
   ```

   Node resolves the desktop socket that **registered this connector** and returns its answer: `{ connectorId, runId, batchIndex, deviceId, cursor, hasMore, events[], rootPath }`. `connectorId` and `deviceId` are echoed so a run can detect a reply from a machine other than the one that owns the folder.
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

Two machines signed into one account both see the connector, because `sync_root_path` lives server-side. Two mechanisms keep only one of them syncing:

- **Registration** — `desktop:register` is first-claim-wins for the life of the socket; a second machine is told `ALREADY_REGISTERED` and must not serve that connector.
- **`device_id` on the sync point** — registration alone is not enough, because when the owner disconnects the claim frees, another machine takes it, and the next `FULL` run prunes everything the first one synced. The first device to answer is pinned onto the sync point, and `_request_file_event_batch` rejects a page from any other with `RESPONSE_MISMATCH`. Moving to a new machine is therefore an explicit act: clear the sync point, which forces a full re-seed.

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
3. **Activate** the connector.
4. Press Sync, or wait for the scheduled tick — both reach `run_sync` through the normal resync/crawl paths, from the browser as well as the desktop app. Only *setup* (picking a folder) needs Electron.

If the desktop is offline the run is skipped with a warning, not failed: no sync point is written and no records are pruned. Because resync is published to Kafka and `run_sync` pulls asynchronously, that outcome shows up as the connector returning to IDLE rather than as an error on the Sync request itself.

## 4. Auth

The pull and content routes are service-to-service. Python mints a scoped JWT with the `desktop:command` scope (`generate_jwt`), and Node guards the routes with `scopedTokenValidator(TokenScopes.DESKTOP_COMMAND)`. `orgId`/`userId` come from the token, never the request body — a body-supplied user id would be a cross-tenant targeting primitive.

The desktop side authenticates separately: the renderer hands its **refresh token** to the Electron main process at login, which encrypts it with `safeStorage` and mints its own access tokens for the socket handshake. That is what lets sync run with the window closed. Where `safeStorage.isEncryptionAvailable()` is false (some Linux desktops, no keyring) the token is kept in memory for the session rather than written to disk in the clear, and sync stops when the app closes.

## 5. Known limits

Socket registration is **process-local** — there is no socket.io Redis adapter on the `/rest-proxy` namespace. A desktop connected to one Node replica is invisible to a pull that lands on another, which answers `DESKTOP_OFFLINE` for a plainly-connected machine. A multi-replica Node deployment needs that adapter before Local FS works there.
