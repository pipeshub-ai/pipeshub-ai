# Local FS integration tests on a headless desktop

These tests run the real PipesHub stack against a real folder, watched by PipesHub's own desktop code
running headless. Marker: `local_fs_sim`.

## How the suite works

**No remote API.** Local FS indexes a folder on the user's computer. The desktop app watches it and answers
the connector's pulls through Node's socket relay (`/socket.io-rest-proxy`, namespace `/rest-proxy`). So
there is nothing to simulate on the far side: the tests make a folder, and run the desktop.

**The desktop.** `desktop_agent.ts` from connectors-simulator (`harness/local_fs/node/`) loads this
checkout's `frontend/electron/local-sync` and wires it as `electron/main.ts` does. It signs in to the
stack's socket with the test owner's access token and registers a device id. Its control API on a local
port starts and stops watchers, arms socket faults (a pull never answered, answered with an error code,
the app quitting mid-run) and reports every pull it served.

**People.** Each test makes two fresh non-admin accounts: the owner, who creates the personal Local FS
connector, and another member. The owner enables sync with the desktop's device id, as the desktop app
does on first enable; that claims the device as the connector's owner.

**The check.** After each sync the folder is the oracle. Every file and folder in scope (the desktop's
ignore list, symlinks not followed) must have one live record, under the right folder, carrying the
file's sha256; nothing else may; the owner opens every file record
(`GET /api/v1/knowledgeBase/record/:id`) and the other member opens none. The check polls for up to 90 s,
since graph writes land a little after the run's end is recorded.

**Files.** `local_fs_sim_world.py` (desktop process, connector set-up, runs, the check, the fixture),
`local_fs_sim_sync_test.py`, `local_fs_sim_deletion_test.py`, `local_fs_sim_failure_test.py`.

## Running it

1. Install Node dependencies in this checkout: `npm ci` in `backend/nodejs/apps` and in `frontend`
   (the desktop code needs `chokidar` and `socket.io-client`; ts-node comes from the Node backend).
2. Point `LOCAL_FS_DESKTOP_AGENT` at a connectors-simulator checkout's
   `harness/local_fs/node/desktop_agent.ts`.
3. The stack must reach the socket from the test host: `PIPESHUB_BASE_URL` is the URL the desktop
   connects to as well.
4. `pytest -m local_fs_sim connectors/local_fs`. Folders go under `integration-tests/local_fs_root`.
   `TC-LFSSIM-SYNC-007` (3,000 files) and `TC-LFSSIM-ERR-002` (two runs of three 65 s timeouts) are slow.

## Status

Written during the Local FS audit (2026-10-26) and **not run**: no stack built from this checkout was
available. The same cases run in the audit harness against the real connector, relay and desktop code;
see the audit report for those results. Expect to adjust API details (response envelopes of
`/api/v1/connectors/`, `/api/v1/notifications`) on the first run.
