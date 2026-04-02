# Local FS (server-side / backend-visible path)

Local FS ingests files from a **local directory on the host where the Python connector service runs**. The web app stores `sync_root_path` in connector config; **`run_sync`** walks that path and creates graph records, then publishes **`record-events`** (`newRecord`) for the indexing pipeline.

## 1. Runtime: path must be visible to the connector process

The path you configure is resolved by the **same OS namespace** as `connectors` / `connectors_main` (not necessarily your laptop browser).

| Deployment | What to do |
|------------|------------|
| **Python on host** | Use the real absolute path (e.g. `/Users/you/project/data`). |
| **Docker / Kubernetes** | **Mount** the host folder into the container and set the **in-container** path (e.g. `-v /host/data:/data` → use `/data`). The UI path must match what `os.path` sees inside the connector pod. |

**Verify from the connector environment**

```bash
# Example: container name from your compose stack
docker compose exec <connectors-service> ls -la /path/you/configured
```

If the path is missing or permission-denied, `init` and `test_connection_and_access` log warnings and sync will not index files.

## 2. Web flow (no CLI required)

1. Create or open a **personal** Local FS connector instance.
2. If your deployment requires saving **filters/sync** while the instance is **inactive**, turn sync off, set **Local folder path** and options, then save.
3. **Activate** the connector when ready.
4. Run a **manual sync** — Local FS uses **MANUAL** strategy; turning “Active” on does **not** crawl the folder by itself.
5. Confirm records appear in the connector / KB UI and indexing progresses.

## 3. Indexing pipeline (Kafka)

After `run_sync`, records are written via `DataSourceEntitiesProcessor.on_new_records`, which emits **`newRecord`** on the **`record-events`** topic.

**Operational checks**

- **Kafka**: brokers reachable from connector and indexing services; topic `record-events` present (see your Kafka/Redpanda config).
- **Indexing worker**: process that consumes `record-events` (e.g. `indexing_main` / indexing consumer) is running and healthy.
- **Logs**: connector logs show `Local FS: finished sync from ...`; indexing logs should show consumption of `newRecord` events for those record IDs.

If sync succeeds but nothing is indexed, verify the indexing consumer and Kafka connectivity before changing connector code.

## 4. WebSockets (notifications only)

The app’s **Socket.IO** service is used for **real-time notifications** (e.g. upload/processing progress) with a normal user JWT. It is **not** the transport for folder crawl or file bytes. Local FS does **not** require WebSocket for ingestion on a backend-visible path.

## 5. Optional CLI

[`pipeshub`](../../../../../nodejs/apps/pipeshub-cli/) (`backend/nodejs/apps/pipeshub-cli`) can:

1. **`pipeshub login`** — store OAuth tokens against `PIPESHUB_BACKEND_URL` (default Node gateway, e.g. `http://localhost:3000`).
2. **`pipeshub setup`** — pick or create a personal Local FS instance and **PUT** `sync_root_path` / `include_subfolders` (writes `daemon.json` locally).
3. **`pipeshub run`** (alias **`pipeshub sync`**) — **PUT** the same path again, then **enable** sync if the instance is inactive (`POST .../toggle`) or **queue a resync** if it is already active (`POST /api/v1/knowledgeBase/resync/connector`), matching the web app’s **Sync** / **Full sync** buttons.

The CLI does **not** stream files to the server; the **Python connector service** must run where that path exists and consume Kafka sync events. Grant the OAuth client **CONNECTOR_*** and **KB_WRITE** scopes as described in the CLI README.
