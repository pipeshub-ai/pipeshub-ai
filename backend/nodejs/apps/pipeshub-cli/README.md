# Pipeshub CLI

Sync a local directory to Pipeshub using the personal **Folder Sync** connector.

Log in, run `setup` to link a connector and folder, then `run` to queue a sync on the backend. The Python connector service must be running and able to read the configured `sync_root_path`.

> **Note:** Filters (dates, extensions, indexing toggles, subfolder settings beyond defaults, etc.) are configured in the **web app**, not in this CLI.

## Requirements

- **Node.js** 20+
- **Native build tools** for `keytar` (OS credential storage):
  - **macOS:** Xcode Command Line Tools
  - **Linux:** `libsecret` development headers (e.g., `libsecret-1-dev` on Debian/Ubuntu)
  - **Windows:** `windows-build-tools` or VS Build Tools

If the native addon cannot load, the CLI falls back to a Fernet-encrypted `auth.enc` file in your config directory.

## Installation

From the repo root:

```bash
cd backend/nodejs/apps/pipeshub-cli
npm install
npm run build
npm link   # optional: makes `pipeshub` available globally on PATH
```

Or run directly without linking:

```bash
node dist/cli.js --help
```

## Quick Start

### 1. Log in

Authenticate with your OAuth2 client ID and client secret (from your Pipeshub app):

```bash
pipeshub login
```

- If `PIPESHUB_BACKEND_URL` is not set, you will be prompted for the backend base URL (default: `http://localhost:3000`).
- The client secret prompt hides input — paste and press **Enter**.
- Credentials and tokens are stored in the **OS keychain** when available, otherwise in `auth.enc` under your config directory (see [Environment](#environment)).

### 2. Verify the token (optional)

```bash
pipeshub verify
```

Prints `OK.` when the token is active.

### 3. Setup

Interactively pick or create a **Folder Sync** connector instance and set the sync folder path:

```bash
pipeshub setup
```

You can also pass the folder path directly:

```bash
pipeshub setup /absolute/path/to/folder
```

**How it works:**

- If multiple connectors exist, choose the one to link on this machine. The last option is always **create a new connector**.
- If the connector already has a sync folder on the server (or `daemon.json` matches), that path is pre-filled — press **Enter** to keep it or type a new one.
- Writes `daemon.json` (sync root + connector ID) beside `auth.enc` and updates the sync path with `include_subfolders: true` via WebSocket RPC.
- The **manual indexing** prompt only appears when the connector is **inactive** (the API rejects filter changes while running). If active, setup saves the path and directs you to change indexing in the web app after disabling the connector.

### 4. Queue a sync

Run a full sync (confirms before proceeding):

```bash
pipeshub run
# alias:
pipeshub sync
```

Use a different folder for this run only:

```bash
pipeshub run /other/path
```

The CLI refreshes `include_subfolders` from the server config (or falls back to `daemon.json`). It does **not** upload files — the connector process must be able to read `sync_root_path` directly.

## Commands

| Command | Description |
| --- | --- |
| `pipeshub login` | Prompt for API URL (if needed), client ID, and client secret; store tokens. |
| `pipeshub logout` | Remove stored credentials. |
| `pipeshub verify` | Print `OK.` when the access token is valid. |
| `pipeshub setup [path]` | Interactive Folder Sync setup — link a connector and set folder path. |
| `pipeshub run [path]` / `sync [path]` | Confirm and queue a **full** sync; optional path override. |
| `pipeshub indexing [subcommand]` | Knowledge base operations (see [Indexing](#indexing)). |

## Indexing

Every indexing command first lists **all personal Folder Sync connectors** (name, ID, active status, sync root, subfolders). You pick which connector to operate on — nothing is highlighted as a default.

| Subcommand | Description |
| --- | --- |
| `pipeshub indexing` | Pick connector, view KB summary, interactively select a pending file to index. |
| `pipeshub indexing status` | Same as `pipeshub indexing`. |
| `pipeshub indexing list` | Pick connector, show first page of KB records (up to 50 rows). |
| `pipeshub indexing reindex [recordId]` | With record ID: queue that record directly. Without: pick connector and select interactively. |
| `pipeshub indexing queue-manual` | Pick connector, confirm, then queue indexing for all `AUTO_INDEX_OFF` records. |

## Environment

Set these in a `.env` file in the project directory or next to the CLI package (see `.env.example`):

| Variable | Description |
| --- | --- |
| `PIPESHUB_BACKEND_URL` | Backend base URL. If unset at login, you will be prompted. |
| `PIPESHUB_WS_URL` | Socket.IO base URL override (defaults to value derived from `PIPESHUB_BACKEND_URL`). |
| `PIPESHUB_CONFIG_DIR` | Override directory for `auth.enc`, `daemon.json`, and related files. |

> Client ID and secret are **not** read from the environment — enter them at `pipeshub login`.

## Authentication and Tokens

- **OAuth2 client credentials** are collected at login and used with the refresh token when the access token expires.
- **Storage:** Access token, refresh token, client ID, and client secret are stored in the **OS keychain** (`com.pipeshub.cli` / `oauth-tokens`). Falls back to `auth.enc` (Fernet-encrypted, machine/user-bound key).
- **Refresh:** When the JWT passes its `exp` time, the CLI automatically refreshes from secure storage. If refresh fails, run `pipeshub login` again.

## OAuth Scopes

| Command | Required Scopes |
| --- | --- |
| `setup` | `CONNECTOR_READ`, `CONNECTOR_WRITE` |
| `run` / `sync` | `CONNECTOR_SYNC`, `KB_WRITE` |
| `indexing` | `CONNECTOR_READ`, `KB_READ`, `KB_WRITE` (as needed per subcommand) |

If you see **403**, add the required scopes to your OAuth client or use the web app instead.

## Transport

- `login` / `verify` use REST OAuth endpoints.
- Operational commands (`setup`, `run`/`sync`, `indexing`) use Socket.IO RPC on namespace `/cli-rpc`.

## Web App vs CLI

| Capability | Web App | CLI |
| --- | --- | --- |
| Set sync path | Yes | Yes (`setup`) |
| Subfolder toggle | Yes | Yes (auto-enabled) |
| Batch size, sync filters | Yes | No |
| Indexing toggles | Yes | Partial (`setup` when inactive) |
| Queue sync | Yes | Yes (`run`) |
| KB operations | Yes | Yes (`indexing`) |

## Development

```bash
npm run build
npm run typecheck
```
