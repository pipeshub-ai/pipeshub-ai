# Pipeshub CLI

Sync a local directory to Pipeshub with the personal **Folder Sync** connector. Log in, run **setup** to link a connector and folder, then **run** to queue a sync on the backend. The Python connector service must be running and able to read `sync_root_path`.

Transport details:
- `login` / `verify` use REST OAuth endpoints.
- Operational commands (`setup`, `run`/`sync`, `indexing*`) use Socket.IO RPC on namespace `/cli-rpc`.

**Filters** (dates, extensions, indexing toggles, subfolders beyond defaults, etc.) are configured in the **web app**, not in this CLI.

## Requirements

- Node.js 20+
- **Native build for `keytar`** (OS credential storage): Xcode Command Line Tools on macOS; on Linux, `libsecret` development headers (e.g. Debian/Ubuntu: `libsecret-1-dev`); Windows usually builds with `windows-build-tools` / VS Build Tools. If the native addon cannot load, the CLI falls back to the Fernet-encrypted `auth.enc` file in your config directory.

## Install

From the repo:

```bash
cd backend/nodejs/apps/pipeshub-cli
npm install
npm run build
npm link   # optional: global `pipeshub` on PATH
```

Or run without linking:

```bash
node dist/cli.js --help
```

The root command only adds **`-h` / `--help`** and **`-V` / `--version`** (Commander defaults). Subcommands use **positional arguments** where noted—no other flags.

## Quick start

1. **Log in** with your OAuth2 client ID and client secret (from your Pipeshub app):

   ```bash
   pipeshub login
   ```

   - If **`PIPESHUB_BACKEND_URL`** is not set, you are prompted for the backend base URL (default suggestion: `http://localhost:3000`).
   - The **client secret** prompt hides input; paste and press **Enter**.

   Credentials and tokens are stored in the **OS keychain** when available; otherwise in **`auth.enc`** under your config directory (see [Environment](#environment)).

2. **Verify the token** (optional):

   ```bash
   pipeshub verify
   ```

   Prints `OK.` when the token is active.

3. **Setup** — interactively pick or create a **Folder Sync** instance and set the folder path. The CLI does not mark any row as a “default”; choose the instance you want linked on this machine. The last option when several exist is **create a new** connector. With a single connector you can confirm or create new instead. Rename, disable, or delete connectors in the **web app**.

   ```bash
   pipeshub setup
   ```

   Optional: pass the folder path as the first argument:

   ```bash
   pipeshub setup /absolute/path/to/folder
   ```

   If the connector already has a sync folder on the server (or **`daemon.json`** matches this connector), that path is **pre-filled**; press **Enter** to keep it or type another path.

   This writes **`daemon.json`** (sync root + connector id) beside `auth.enc`, updates sync path and **`include_subfolders: true`** through WebSocket RPC. The **manual indexing** question appears only when the connector is **inactive** (the API rejects filter changes while it is running). If the connector is **active**, setup still saves the path and tells you to change indexing in the app after you turn the connector off. If the path save fails, set the path in the app (or turn the connector off and run setup again).

4. **Queue a sync** (full sync; confirms before proceeding):

   ```bash
   pipeshub run
   # alias:
   pipeshub sync
   ```

   Optional: use a different folder for this run only:

   ```bash
   pipeshub run /other/path
   ```

   The CLI refreshes **`include_subfolders`** from the server config (or falls back to `daemon.json`). It does not upload files from your laptop—the connector process must read `sync_root_path`.

### Folder Sync: web app vs CLI

- **Registry type:** **`Folder Sync`** (same string the API uses when creating an instance).
- **Web:** Personal → **Folder Sync** — set path, subfolders, batch size, sync filters, and indexing toggles. Save while the connector is **inactive** when the app requires it.
- **CLI:** **setup** (path, optional manual indexing) + **run** (queue sync). **indexing** lists connectors, lets you pick one, then KB actions. Use the app for other filter details when needed.

### OAuth scopes

- **setup:** **CONNECTOR_READ**, **CONNECTOR_WRITE** (list/create connectors, update sync config).
- **run:** **CONNECTOR_SYNC**, **KB_WRITE** (toggle sync when inactive; resync when active).
- **indexing** subcommands: **CONNECTOR_READ** (list connectors and read sync config for the picker), **KB_READ**, **KB_WRITE** as needed for list/stats/reindex/queue.

If you see **403**, add scopes on your OAuth client or use the web app.

## Indexing

Every **indexing** command first prints **all personal Folder Sync connectors** (name, id, active, sync root, subfolders). Nothing is highlighted as a default—you pick **which connector (1–N)** for that run only. After **setup**, **`pipeshub run`** still reads **`daemon.json`** for its connector until you run setup again.

| Subcommand | What it does |
|------------|----------------|
| `pipeshub indexing` | Pick connector → KB summary → interactive pick to index a pending file. |
| `pipeshub indexing status` | Same as default `pipeshub indexing`. |
| `pipeshub indexing list` | Pick connector → first page of KB records (up to 50 rows). |
| `pipeshub indexing reindex [recordId]` | With **record id**: queue that record only (no connector list). Without id: pick connector → summary → interactive pick. |
| `pipeshub indexing queue-manual` | Pick connector → confirm → queue indexing for all **AUTO_INDEX_OFF** records on it. |

## Environment

Optional `.env` in the project directory or next to the CLI package (see `.env.example`):

| Variable | Purpose |
|----------|---------|
| `PIPESHUB_BACKEND_URL` | Backend base URL. If unset at **login**, you are prompted. |
| `PIPESHUB_WS_URL` | Optional Socket.IO base URL override (default derived from `PIPESHUB_BACKEND_URL`). |
| `PIPESHUB_CONFIG_DIR` | Override directory for `auth.enc`, `daemon.json`, and related files (no CLI flag). |

Client ID and secret are **not** read from the environment; enter them at **`pipeshub login`**.

## Auth and tokens

- **OAuth2 client credentials**: collected at **login**; used with the refresh token when the access token expires and for **verify** introspection.
- **Storage**: access + refresh tokens, **client_id**, **client_secret** — prefer **keychain** (`com.pipeshub.cli` / `oauth-tokens`); else **`auth.enc`** (Fernet + machine/user-bound key).
- **Refresh**: JWT past `exp` triggers refresh from secure storage. If refresh fails, run **login** again.

## Commands (summary)

| Command | Description |
|---------|-------------|
| `pipeshub login` | Prompt for API URL (if needed), client ID, client secret; store tokens. |
| `pipeshub logout` | Remove stored credentials. |
| `pipeshub verify` | Print `OK.` when the access token is valid. |
| `pipeshub setup [root]` | Interactive Folder Sync link + folder path; optional path argument. |
| `pipeshub run` / `sync [root]` | Confirm, push sync path, queue **full** sync; optional path override. |
| `pipeshub indexing …` | KB summary, list, reindex, queue-manual (see [Indexing](#indexing)). |

## Development

```bash
npm run build
npm run typecheck
```
