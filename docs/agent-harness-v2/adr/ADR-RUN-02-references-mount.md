# ADR-RUN-02: Reference checkouts on their own mount

- Extends: [ADR-RUN-01](ADR-RUN-01-run-outcome-architecture.md) D2 (storage classes: a reference is stored as an
  address and a version, never its content).
- Related: [ADR-OWD-06](ADR-OWD-06-pipeshub-contract-and-ph.md) (the `/pipeshub` contract and `ph`),
  [ADR-OWD-03](ADR-OWD-03-isolation-and-tenancy.md) (sandbox mounts and the broker).
- Follow-up: WP98-FU-1 (option 3).

## Context

WP-98 made a checkout `ph git clone` made a reference: recorded by address and commit, never saved. It still lived
inside the synced workspace folder, so every save streamed it in the sandbox's archive (about 1.3 s per save at
20,977 files in a large open-source checkout) and the archive reader's 50,000-entry bound counted it, refusing a save once a checkout
passed it. Docker's archive API has no exclude, and the reader only recognises a reference after it has indexed every
header. Counting only non-reference entries would keep the streaming and give the bound two meanings; a broker-side
exclude would need a new wire parameter on both sides and still leave the daemon reading the whole folder.

## Decision

- **D1. The references mount.** An engine sandbox gets `/pipeshub/references` beside its workspace when the
  deployment can fetch repositories (`EngineControllerConfig.references_mount`, from `references_mount` in
  `backend/python/harness_core/repos/fetch.py`). It follows the workspace's strategy
  (`references_mount_for` in `backend/python/harness_core/engines/controller.py`): a scratch mount of its own size
  beside a scratch workspace, so a warm sandbox still holds the shape; the session's sibling persistent folder
  (`<workspace source>-references`) beside a persistent workspace, on disk, measured by the broker's quota guard as
  every writable persistent mount is, and erased with the session. No sandbox, broker or wire type changes.
- **D2. The producer links, the registry classifies.** `ph git clone` checks out under the mount, one folder per
  workspace path, and leaves a link at the folder the engine asked for, so its commands and paths stay the same. The
  storage-class registry gains a `LinkRule` (`GIT_CLONE_LINK` in
  `backend/python/harness_core/workspace/sync/storage_classes.py`): a link whose target is a folder directly under
  the mount is a reference, read from the checkout's own git config (the same `pipeshub.*` keys as WP-98's mark).
  Such a link is never saved; it is recorded only when the worker vouched for the repository, and kept in the
  sandbox on a refresh and at a turn's start whether or not its checkout could be read.
- **D3. The archive never holds a checkout.** The save reads one link entry per reference and one small file per
  link (`head -c` in the sandbox). The 50,000-entry bound keeps its one meaning: a tar-bomb guard.
- **D4. Older checkouts move.** At a turn's start the controller runs `ph git adopt` for each reference the
  revision records (`adopt_references`); `ph` moves a checkout an older `ph` made inside the workspace onto the mount
  and links it, and leaves anything else alone. One that cannot move stays as before: kept, never saved.
- **D5. Re-materialisation stays on demand** (WP-98): a later turn's brief names each reference and `ph git clone`
  restores it from the deployment's cache at its commit, replacing a link whose checkout went with an old sandbox.

## As built

- DEC-B (`harness/v2-decb`): the mount and its strategy (`references_mount`, `references_mount_for`, set by the
  worker and the broker's warm-pool shapes); `GIT_CLONE_LINK`, `StorageMap.linked`, `TarWorkspaceSync._linked`;
  `ph` 2.3.0's `git clone` on the mount and `git adopt`; the controller's `adopt_references`. Tests:
  `backend/python/tests/unit/harness_core/workspace/sync/test_wp98_fu1_references_mount.py` (a 25,000-file
  reference neither streamed nor counted under a 1,000-entry bound, both strategies, an unreadable checkout, a foreign
  link, the warm shape) and `backend/python/tests/unit/harness_engines/pipeshub/test_wp98_fu1_ph_references.py`.

## Deviations

- The note asked for a disk-backed mount in every case. Beside a scratch workspace the mount is scratch too: a
  per-sandbox disk volume would need a size the local volume driver cannot enforce (only the quota guard's `du`
  measures disk), and a persistent source would disable the warm pool for every engine turn. A scratch workspace's
  checkout was in a scratch mount before this ADR too, now with its own size instead of the workspace's 512 MB.
- A link is not followed by every tool (`find` without `-L`, a recursive search from the workspace root): `ph`'s clone
  line says so. The Docker lane's clone, link, save and refresh case and gVisor are open (STATUS WP98-FU-1).

## Owner amendments

None.

## Status

**Accepted** (2026-11-10).
