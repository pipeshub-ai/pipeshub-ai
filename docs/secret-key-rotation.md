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
2. Generate the new key with `openssl rand -base64 32` and store it in your secret manager. The tool refuses, before writing anything, a key the services refuse on the hardened profile (`PIPESHUB_HARDENED=true`), whichever profile you run: a placeholder, a key shorter than 32 characters, a key with leading or trailing whitespace, a low-entropy or patterned key, or readable text encoded as hex or base64. The error names the reason.
3. Stop everything that can write configuration, in every region: the Node API, the query, connectors, indexing, docling and embedding services (all Python and Node services), and any other process using the store. A process left running on the old key would fail to read rotated values or write new ones under the old key.
4. Only then take a verified backup of the store (Redis snapshot, etcd snapshot or Postgres dump) and confirm it restores. Backing up first would miss writes made before the services stopped. Keep the old `SECRET_KEY` until the new one is confirmed.

## Steps

1. Dry run from any container or host that can reach the store, using the built image and the same store settings as the API (`KV_STORE_TYPE`, `REDIS_*` or the etcd/Postgres variables):

   ```bash
   OLD_SECRET_KEY='...' NEW_SECRET_KEY='...' \
     node /app/backend/dist/libs/keyValueStore/rotation/rotateSecretKeyCli.js
   ```

   It prints counts and key names only. It exits non-zero if nothing was found (check the store settings and `REDIS_KV_PREFIX`), if the store cannot be read, or if any key is `failed`.
2. Clear every `failed` key before applying. A failed key holds an encrypted value that neither the old nor the new key can read. If it was written under an earlier key and you still have that key, do two runs: first `OLD_SECRET_KEY=<earlier> NEW_SECRET_KEY=<current> ... --apply` (values already on the current key count as `alreadyRotated`, the earlier-key values move to the current key), then the normal run from the current key to the new key. That first run writes under the current key, so it is refused when the current key fails the check in "Before you start". Then run in the reverse direction instead: first `OLD_SECRET_KEY=<current> NEW_SECRET_KEY=<earlier> ... --apply`, which moves every current-key value, and the current-key fields of a mixed value, to the earlier key and leaves the earlier-key fields as they are (a value entirely on the earlier key counts as `alreadyRotated`); then the normal run from the earlier key to the new key. Nothing is written under the current key. Only when you have no earlier key, or the earlier key also fails that check, is the value lost: restore it from the backup if the backup used the right key, or delete the key and re-enter the setting in the product afterward. The tool never writes while any key has failed, unless you pass `--skip-unreadable`: use it only when you have checked every failed key and none is a live secret under another key (for example a plain value that only looks encrypted, or a stale value you will delete). Those keys are then left exactly as they are, including any readable encrypted field in the same value: it stays on the old key and cannot be read after you switch `SECRET_KEY`.
3. Apply:

   ```bash
   OLD_SECRET_KEY='...' NEW_SECRET_KEY='...' \
     node /app/backend/dist/libs/keyValueStore/rotation/rotateSecretKeyCli.js --apply
   ```

   It prints `APPLIED` only when every write went through and exits 0. Keys listed as `expiring` do not change that: they are left on the old key by design and the services recreate them. It is safe to re-run after an interruption: values already on the new key are counted as `alreadyRotated`. Keys listed as `conflicts` were not rewritten (the value changed, or the store did not apply the write) and `writeErrors` hit a store error; run it again until both are empty.
4. Set `SECRET_KEY` to the new value in every service's environment in every region, then start all services together. Node and Python services must share the same value.
5. Verify: sign in, open the AI model settings and confirm models load, check storage settings, run a chat. Then do the follow-up for data outside the store (above).

## If it goes wrong

Stop all services, restore the backup, and start them with the old `SECRET_KEY`. You need two records from step 4 to find what was written under the new key, so take both before you start the services: the time they first start on the new key, and the id and stored secret (`clientSecretEncrypted`, column `client_secret_encrypted` on Postgres) of every OAuth app.

The backup covers the key-value store only. Records the tool never rotated (listed in "Not rotated by the tool") read again once the old key is back, except those written while the services ran on the new key, including every secret you regenerated and every sync point re-synced in the follow-up after the switch. Those stay on the new key and do not decrypt after the rollback:

- **Settings changed in the key-value store after the switch** are lost with the restore. Enter them again in the product.
- **OAuth app client secrets** (field `clientSecretEncrypted` in the `oauthApps` collection of the document store) of every app created, or whose secret was regenerated, after the switch: find them by comparing with the record from step 4. An app missing from it, or whose stored secret differs from it, has a secret written under the new key. Do not select by `updatedAt`: any edit of an app changes it, and an app whose secret was not regenerated still works after the rollback. Regenerate each one's secret and give the new secret to the app's owner. The built-in "Personal Access Tokens", "Service Tokens" and "PipesHub agent" apps never use their stored secret; they need nothing.
- **Connector sync points** whose sealed fields (`<field>_encrypted`, for example Outlook delta links) a connector wrote after the switch. The connector logs "Failed to decrypt" and loses its incremental position. Trigger a full re-sync of each such connector.

Backing up the document store and the graph database before the switch does not avoid this: what breaks was written after the switch, so restoring an older copy loses it too.

Secrets are read from the environment only, so they do not appear in the process list. Clear them from your shell history and environment afterward.
