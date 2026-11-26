# Rotating SECRET_KEY

`SECRET_KEY` protects stored configuration (AI model settings, storage and mail credentials, connector credentials and similar). Changing it without re-encrypting what is stored makes those values unreadable. Use the rotation tool; never just change the variable.

Two procedures exist. "Rotating SECRET_KEY" in `docs/agent-harness-v2/guides/runbooks.md` moves everything the key protects, including the agent harness's per-organization data keys, OAuth client secrets and connector sync points, with the services running: prefer it, and use only it when the agent harness has been used (its stored conversations cannot be read once the old key is gone and their data keys were not moved). The offline tool below rewrites the key-value store only.

Rotating with this tool is a maintenance event, not a routine toggle. It needs downtime, a backup, and follow-up work for data the tool cannot reach (see "Not rotated by the tool").

All services, in all regions, share one key and one store. They must switch together.

## What the tool rotates

Every value in the key-value store (Redis, etcd or Postgres) that is either encrypted as a whole, or is plain JSON holding encrypted strings at any depth (for example the `s3` and `azureBlob` fields of the storage setting). Only the encrypted strings change; the rest of a JSON value is kept byte for byte. Plain values are left alone. Keys that hold a non-string value (a different Redis type) are skipped and listed. Keys with an expiry (locks, short-lived caches) are not rewritten, because a rewrite would drop the expiry; they are listed as `expiring`, still use the old key, and are recreated by the services when they expire or are next written.

## Not rotated by the tool

- **OAuth app client secrets** (database field `clientSecretEncrypted`). After rotation, the stored secret of every OAuth app no longer decrypts, so token exchange for that app fails until its secret is regenerated. Regenerate each app's secret after the services are back and give the new secret to the app's owner.
- **Encrypted connector sync fields**, for example Outlook delta links stored in the sync point records. After rotation they cannot be decrypted (the connector logs a warning and the field stays unusable). Recovery: expect the affected connector to lose its incremental position; clear the affected sync points or trigger a full re-sync of the connector, and check the connector logs for decrypt warnings.
- **Agent harness data keys and egress proxy state.** Use the runbook procedure instead.
- Anything else you encrypt yourself with this key outside the key-value store.

## Before you start

1. Choose the lowest global traffic window and announce a maintenance period.
2. Generate the new key (at least 16 characters, ideally 32 or more random characters) and store it in your secret manager. The tool refuses placeholders, short keys and repetitive keys (fewer than 8 different characters).
3. Stop everything that can write configuration, in every region: the Node API, the query, connectors, indexing, docling and embedding services (all Python and Node services), and any other process using the store. A process left running on the old key would fail to read rotated values or write new ones under the old key.
4. Only then take a verified backup of the store (Redis snapshot, etcd snapshot or Postgres dump) and confirm it restores. Backing up first would miss writes made before the services stopped. Keep the old `SECRET_KEY` until the new one is confirmed.

## Steps

1. Dry run from any container or host that can reach the store, using the built image and the same store settings as the API (`KV_STORE_TYPE`, `REDIS_*` or the etcd/Postgres variables):

   ```bash
   OLD_SECRET_KEY='...' NEW_SECRET_KEY='...' \
     node /app/backend/dist/libs/keyValueStore/rotation/rotateSecretKeyCli.js
   ```

   It prints counts and key names only. It exits non-zero if nothing was found (check the store settings and `REDIS_KV_PREFIX`), if the store cannot be read, or if any key is `failed`.
2. Clear every `failed` key before applying. A failed key holds an encrypted value that neither the old nor the new key can read. If it was written under an earlier key and you still have that key, do two runs: first `OLD_SECRET_KEY=<earlier> NEW_SECRET_KEY=<current> ... --apply` (values already on the current key count as `alreadyRotated`, the earlier-key values move to the current key), then the normal run from the current key to the new key. The first run is refused if the current key is a placeholder or shorter than 16 characters; in that case, and when no earlier key exists, the value is lost: restore it from the backup if the backup used the right key, or delete the key and re-enter the setting in the product afterwards. The tool never writes while any key has failed, unless you pass `--skip-unreadable`: use it only when you have checked every failed key and none is a live secret under another key (for example a plain value that only looks encrypted, or a stale value you will delete). Those keys are then left exactly as they are, including any readable encrypted field in the same value: it stays on the old key and cannot be read after you switch `SECRET_KEY`.
3. Apply:

   ```bash
   OLD_SECRET_KEY='...' NEW_SECRET_KEY='...' \
     node /app/backend/dist/libs/keyValueStore/rotation/rotateSecretKeyCli.js --apply
   ```

   It prints `APPLIED` only when every write went through and exits 0. Keys listed as `expiring` do not change that: they are left on the old key by design and the services recreate them. It is safe to re-run after an interruption: values already on the new key are counted as `alreadyRotated`. Keys listed as `conflicts` were not rewritten (the value changed, or the store did not apply the write) and `writeErrors` hit a store error; run it again until both are empty.
4. Set `SECRET_KEY` to the new value in every service's environment in every region, then start all services together. Node and Python services must share the same value.
5. Verify: sign in, open the AI model settings and confirm models load, check storage settings, run a chat. Then do the follow-up for data outside the store (above).

## If it goes wrong

Stop all services, restore the backup, and start them with the old `SECRET_KEY`.

Secrets are read from the environment only, so they do not appear in the process list. Clear them from your shell history and environment afterwards.
