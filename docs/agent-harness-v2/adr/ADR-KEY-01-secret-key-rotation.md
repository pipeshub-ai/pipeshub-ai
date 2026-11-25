# ADR-KEY-01: `SECRET_KEY` is rotated through a two-key ring and one resumable command

- Package: KEYRING (STATUS WP87-FU-4).
- Design: the follow-up decisions of 2026-11-10, "WP87-FU-4". Owner approval
  2026-11-10 ("yes, go ahead with the key ring rotation"), including the change to the shared platform cipher.
- Related: ADR-OWD-12 (per-org data keys under a KEK), ADR-OWD-01 (sealed credentials in sandbox routes).

## Context

`SECRET_KEY` roots four things:

1. **The platform cipher.** SHA-256 of the secret is the AES-256-GCM key of every configuration secret in the KV
   store (Redis, etcd or PostgreSQL), in Node (`EncryptionService.getInstance`) and in Python
   (`EncryptedKeyValueStore`, `ConfigurationService`). The same cipher encrypts each OAuth app's
   `clientSecretEncrypted` in the document store and connectors' sync-point delta links in the graph.
2. **The KEK.** HKDF of the hashed secret wraps every org data key; each wrap stores `kekId`, a fingerprint.
3. **The egress proxy's durable state.** `StateSealer` (bindings, grants, stops, volume records, the TLS authority)
   and the credentials sealed into sandbox routes (`CredentialSealer`), both keyed by HMAC of the raw secret.
4. **Run-key digests.** HMAC of each run's idempotency key under HKDF of the hashed secret.

Until now the runbooks said "not rotatable: treat a leak as a full compromise". This ADR takes option 1 of the
design: a key ring with an online move.

## Decision

**D1. The ring is two variables: `SECRET_KEY` writes, one other key is only read.**

| Variable | Meaning |
| --- | --- |
| `SECRET_KEY` | The current key. Every writer seals, wraps, encrypts and digests under it alone. |
| `SECRET_KEY_NEXT` | The key about to become current. Readers accept it; nothing is written under it. |
| `SECRET_KEY_PREVIOUS` | The key being retired. Readers accept it; nothing is written under it. |

The other key is named by its role, not tagged by an id inside each ciphertext, so the stored formats do not change:
an old build reads everything a new build writes as long as it holds the key. Each reader tries the current key
first, then the other one; AES-GCM authenticates, so a wrong key fails, it never decrypts to garbage. A KEK wrap is
the exception: it already names its key (`kekId`), so the ring opens it with that member only.

Both languages read the ring with the same rules and the same messages
(`libs/encryptor/secret-key-ring.ts`, `app/utils/encryption/secret_key_ring.py`). Fail closed:

- `SECRET_KEY` unset: the service does not start (as before).
- `SECRET_KEY_NEXT` and `SECRET_KEY_PREVIOUS` both set: refused ("never both").
- The other key equal to `SECRET_KEY`: refused ("a rotation needs a new key"). The comparison is by SHA-256 digest
  with a constant-time compare.

The platform cipher's process-wide instance (`EncryptionService.getInstance` in both languages) takes the ring when
it is given the deployment's own key; a cipher made for another key (the credential vault's data keys,
`EncryptionService.withKey`) reads under that key alone.

**D2. Every reader accepts the ring.**

| Data | Reader with the ring |
| --- | --- |
| Configuration store, OAuth client secrets, sync points | `EncryptionService` (Node and Python) tries both keys |
| Org data keys | `RingKeyEncryptionKey` unwraps with the member `kekId` names; a wrap under neither fails closed (`unwrap_failed`) |
| Egress state and sealed route credentials | `StateSealer` and `CredentialSealer` take `accepted=` secrets (`AeadRing`) |
| Run-key digests | `RunKeyDigest.matches` computes the digest under each key and compares each with `timingSafeEqual`; every key is tried, so the time taken does not say which matched |

**D3. A key switch is two restarts, so a mixed fleet never meets data it cannot open.**

1. Set `SECRET_KEY_NEXT=<new>` next to the old `SECRET_KEY` everywhere, restart one service at a time. Writers
   still use the old key; every restarted service can already read the new one.
2. Set `SECRET_KEY=<new>` and `SECRET_KEY_PREVIOUS=<old>`, remove `SECRET_KEY_NEXT`, restart one at a time. During
   this restart some services write under the new key and some under the old, and every service reads both: those
   still on step 1 hold the new key as `SECRET_KEY_NEXT`, those on step 2 hold the old one as `SECRET_KEY_PREVIOUS`.
3. Run `rotate-secret-key rotate` (D4), then `rotate-secret-key retire` (D6).
4. Remove `SECRET_KEY_PREVIOUS`, restart.

A build from before this change reads only `SECRET_KEY`: it must not run during steps 1 to 4. A single-host Compose
install may join steps 1 and 2 (one `docker compose up -d`), accepting that for the seconds between two containers'
restarts a value one wrote under the new key fails to open in the other; nothing is lost, the read is repeated or
the request fails and is retried.

**D4. One command moves everything, in a fixed order, and keeps no checkpoint.**
`node dist/modules/agent-harness/ops/rotate-secret-key-cli.js dry-run|rotate|verify|retire`, run where the Node
service runs, with its environment. `rotate`:

1. Refuses unless `SECRET_KEY_PREVIOUS` is set and `SECRET_KEY_NEXT` is not.
2. Takes the rotation lock (D5).
3. Counts every step (a dry run). Anything that opens under neither key refuses the run (D7).
4. **Data keys.** For each org holding a key (`DataKeyStore.orgIds`), each wrap not under the current KEK is
   unwrapped with the member its `kekId` names, wrapped under the current KEK and written with
   `replaceWrap(orgId, kid, expectedKekId, next)`, a compare-and-set on `kekId`. The data key's bytes, its seal
   count and its version do not change. The plaintext key is zeroed after use.
5. **Egress state.** `python -m app.harness.sandbox.egress_rekey --apply`: each record sealed under the previous
   key is sealed again under the current one with `EgressStateStore.replace`, a compare-and-set that keeps the
   record's expiry (Redis `KEEPTTL` in one script).
6. **Configuration store.** Every key of the KV store, through `KeyValueStoreService`: a value that is one
   ciphertext (as Node stores it, or JSON-quoted as Python does), or a plaintext JSON document whose fields hold
   ciphertexts, is re-encrypted where the previous key opened it and written with `compareAndSet` on the value
   read (up to five attempts against concurrent writers). A decrypted value is not searched again: what it holds
   is under other keys (the credential vault's data keys).
7. **OAuth client secrets.** For each org, each app's `clientSecretEncrypted` is re-encrypted and written with
   `replaceClientSecret(orgId, id, expected, next)`, a compare-and-set.
8. Counts every step again and reports.

Nothing records progress. Each step finds what is left from the data itself (a wrap's `kekId`, the key a
ciphertext opens under) and each write is a compare-and-set on what was read. So:

- **Crash and resume.** A run stopped anywhere (between steps or inside one) leaves every item either moved or
  untouched; running `rotate` again moves the rest. A write that reached the store but not the operator is found
  already moved.
- **Order.** Data keys come first because they guard the most (every org's journal); configuration last but one
  because a stale cached value there is the cheapest to read again. Any order is correct; this one shortens the
  window in which the most valuable data depends on the old key.

**D5. Two operators: a lease, with the compare-and-sets as the guarantee.** `rotate` and `retire` take the harness
`LeaderLease` `secret-key-rotation` (Mongo or PostgreSQL, store time, 60 s, renewed every 20 s) and release it at the
end; a second run is refused while it is held ("another run of the rotation holds the lock"). A run that loses the
lease (a renewal finds another holder) stops at its next item (`RotationLockLost`). Correctness does not rest on
the lease: two runs that overlap anyway (one paused past its lease) both apply the same transformation through
compare-and-sets, so one wins each item and the other's write fails harmlessly. `dry-run` and `verify` take no lease.

**D6. Retire is refused while anything needs the previous key.** `retire` (under the lease) counts every step and
exits 0 only when nothing is under the previous key, no running sandbox route holds a credential sealed under it
(`draining`, below) and nothing is unreadable (D7). It then prints the next step: remove `SECRET_KEY_PREVIOUS` and
restart. With `SECRET_KEY_PREVIOUS` already removed, data still under the old key reads as unreadable, so retire is
refused then too.

**D7. A wrong or missing previous key fails closed.** An item that opens under neither key is counted
`unreadable`. `rotate` and `retire` refuse while there are any, saying to check `SECRET_KEY_PREVIOUS`, unless the
operator passes `--accept-unreadable N` with exactly the count reported (values from another deployment that were
never readable here, such as Slack settings saved under an older key). The count must match, so a later, larger
problem is never accepted by an old acknowledgement.

**D8. What drains instead of moving.**

- **Credentials sealed in a running sandbox's routes** are the broker's: it re-registers a binding as it holds it
  after a proxy restart, so rewriting the proxy's copy would be overwritten. The egress step counts them as
  `draining`; they end with their sandboxes (a run's deadline, one hour by default). The proxy opens them with the
  ring meanwhile.
- **Run-key digests** are not moved (they cannot be: the idempotency key is not stored). A run admitted under the
  old key matches through `matches` during the grace window; admission deduplicates by its receipt, so a digest
  that no longer matches after retirement costs nothing but a client run id lookup on an ended turn.
- **Connector sync points** (delta links in the graph) are read under the ring and moved by the command
  (WP87-FU-4b, amending the first design, which let them drain at each sync and so left `retire` blind to a
  connector that had not synced). The step is Python, `python -m app.connectors.core.base.sync_point.rekey`, run like
  the egress step: it walks every org's sync points through `IGraphDBProvider.list_sync_points` (a keyset page in
  `syncPointKey` order, on Neo4j, ArangoDB and PostgreSQL alike), counts each sealed field (`<field>_encrypted`) by
  the key that opens it, and reseals the stale ones with `update_sync_point_if` on their stored values, so a
  connector's own write meanwhile wins and is counted for the final check. `retire` refuses while one is left.

**D9. Journal data does not move.** It is sealed under the org data keys; only their wraps change. A backup taken
before the rotation needs the previous key to restore: keep the old key with the backup, as the runbook says.

**D10. The grace window** is from step 2 of D3 to step 4: long enough for the longest-running sandbox to end
(`draining` 0), every connector to sync once, and every service to have been restarted with the ring. `retire`
checks what it can see; the operator owns the rest.

**D11. Compare-and-set keeps a key's expiry on every KV backend.** A rewrap of a lease or a sign-in state must not
make it permanent. PostgreSQL's compare-and-set already kept `expires_at`; Redis's now replaces with `KEEPTTL`
(Redis 6 and later; Compose ships 7.4) and etcd's with `ignoreLease`, so all three agree.

## As built

The ports and files changed, and what follows from them:


- The runbook's "`SECRET_KEY` is not rotatable" row is replaced by the procedure; a leak is answered by a rotation.
- A deployment runs with two secrets in its environment during the grace window; both are as sensitive as the one.
- `KeyEncryptionKey.unwrap` takes the stored `kekId`; a managed KMS KEK ignores it (one KMS key) and keeps its own
  rotation behind the same port.
- `RunKeyDigest` gains `matches`; `childRunDocument` takes only `digest`.
- `DataKeyStore` gains `orgIds` and `replaceWrap`, `OAuthAppRepository` gains `listClientSecrets` and
  `replaceClientSecret`, `EgressStateStore` gains `replace`; each has a contract case on every store.

**Tests.**

| Ids | File | What |
| --- | --- | --- |
| KR-1..KR-17 | `backend/nodejs/apps/tests/modules/agent-harness/secret-key-rotation.test.ts` | the ring's rules; readers meeting previous-key data (cipher, KEK, digests); a mixed fleet; dry-run, rotate, verify, retire; a crash at each step and inside each, then a resume; a second operator refused by the lock; two overlapping runs; a lost lock; a wrong or missing previous key; draining; concurrent writers |
| KC-1..KC-9 | `.../rotate-secret-key-cli.test.ts` | arguments, exit codes, output without key material, the Python step's adapter |
| KI-1..KI-3 | `.../secret-key-rotation.mongo.itest.ts` | the CLI's production backend on the replica set and Redis, the lock in Mongo, a key with an expiry |
| KV-1, KV-2 | `.../secret-key-rotation-kv.redis.itest.ts` | the configuration step on real Redis and etcd: expiry kept, a concurrent write wins |
| DK-10..DK-12 | `support/data-key-store-contract.ts` | `replaceWrap`, `orgIds` on the in-memory, Mongo and PostgreSQL stores |
| (contract) | `tests/stores/contract/oauth-app.contract.ts` | `listClientSecrets`, `replaceClientSecret` on Mongo and PostgreSQL |
| ES-5 | `harness_core/testing/sandbox/egress_contracts.py` | `EgressStateStore.replace` on the in-memory store, fakeredis and real Redis |
| SP-1..SP-6 | `backend/python/tests/unit/connectors/core/test_sync_point_rekey.py` | the sync-point step: dry-run, apply, the new key alone reading every link; a connector's racing write; a wrong key; only stale fields; paging over every org; output |
| RB-1, RB-2 | `backend/python/tests/integration/graph_db/test_sync_point_rekey_real_backends.py`, `postgres/test_records.py` | `list_sync_points` and the step on real Neo4j, ArangoDB and PostgreSQL |
| KC-10 | `.../rotate-secret-key-cli.test.ts` | the sync point step's module, and `retire` refused while one is under the previous key |
| KP-1..KP-12 | `backend/python/tests/unit/harness/test_keyring_rotation.py` | the ring in Python, the platform cipher, the egress sealers, the egress step (crash and resume, a racing proxy, a wrong key, output) |
| KH-1..KH-3 | `deployment/helm/tests/test_secret_key_ring.py` | Compose and Helm pass the ring to every service that reads `SECRET_KEY`; Helm refuses both keys at once |

## Deviations

- The design said "unwrap by `kekId`" with one KEK port; the ring is its own type (`KeyEncryptionKeyRing`) so a
  step that rewraps can see which members exist without the single-key adapters changing.
- The design listed three stores; reading every use of `SECRET_KEY` found two more. OAuth client secrets are moved
  by the command (they authenticate every `client_credentials` request, so leaving them would break apps at
  retirement). Sync points were left to drain, then moved by the command too (WP87-FU-4b, D8):
  `IGraphDBProvider` gained `list_sync_points`, and the Python steps share `app/utils/encryption/rotation_step.py`
  and Node's `PythonRotationStep`.

## Owner amendments

None. The design includes the change to the shared platform cipher.

## Status

**Accepted** 2026-11-10: option 1 of WP87-FU-4, built by KEYRING. Rotating a live install's key stays the operator's
step.

Amended 2026-11-13 by [ADR-KEY-02](ADR-KEY-02-per-component-keys.md) (SEC-D-03, SEC-D-05):

- The platform cipher's key is HKDF of the secret; the old SHA-256 key is still read.
- `rotate` with no previous key moves what the old key sealed.
- A key a writer uses must be random.
- The egress proxy and the sandbox broker no longer hold the ring: Node gives the proxy the egress keys derived from it.
