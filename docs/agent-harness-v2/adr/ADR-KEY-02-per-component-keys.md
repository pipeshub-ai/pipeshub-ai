# ADR-KEY-02: the sandbox plane holds its own keys, and SECRET_KEY must be random

- Fixes SEC-D-03 and SEC-D-05 (R38-IDENT, 2026-11-13).
- Amends: [ADR-KEY-01](ADR-KEY-01-secret-key-rotation.md) (the platform cipher's key derivation, and `rotate` with no
  previous key).
- Related: [ADR-CRED-01](ADR-CRED-01-machine-credential-roles.md).

## Context

- **SEC-D-03.** The egress proxy and the sandbox broker face sandbox code first, and both held `SECRET_KEY` (which
  decrypts every configuration secret, wraps every org's data keys and seals the egress state) and the default
  Redis login. One parsing bug in the proxy, or a read of `/proc/1/environ`, was every credential of every org.
- **SEC-D-05.** The key ring accepted any non-empty `SECRET_KEY`, including the placeholder that `env.template` and
  eight Compose files shipped. The platform cipher's AES key was `SHA-256(secret)`: no domain separation, and a short
  or guessable secret falls to an offline brute force of a leaked dump.

## Decision

**D1. A component never holds `SECRET_KEY`.** The proxy and the broker each have their own random key
(`EGRESS_PROXY_COMPONENT_KEY`, `SANDBOX_BROKER_COMPONENT_KEY`; in the component, `PIPESHUB_COMPONENT_KEY` or a file
named by `PIPESHUB_COMPONENT_KEY_FILE`). Node holds both. A component that finds `SECRET_KEY` in its environment,
or a weak key, refuses to start and says what to set.

**D2. Node seals for each component only what it needs.** Under a key HKDF derives from the component's key
(`<component>/config/v1`), Node writes `/services/agentHarness/components/<component>`:

- its own role credential (ADR-CRED-01);
- digests of the credentials it admits;
- for the proxy, the egress sealing keys. These are derived from each `SECRET_KEY` ring member exactly as the sealers
  derive them (HMAC-SHA256 under the sealer's label). The proxy opens what it and the worker sealed, and holds nothing
  that opens a configuration secret or an org's data key.

The component reads that document and `/services/endpoints`, and nothing else: its configuration service answers any
other key as absent without asking the store.

**D3. A Redis ACL user per component.** Its password is HKDF of the component's key (`<component>/redis/v1`). Its name
carries a fingerprint of the password, so two installs sharing one Redis never share a user. Node applies the user on
every publish (`ACL SETUSER … reset on #<sha256>`), so a Redis restart heals within five minutes. The user may:

- read its document and the endpoints;
- read, write and compare-and-set the proxy's `harness:egress:*` state (the proxy only);
- subscribe to the configuration-change channel.

Every other key and every other command is refused (`NOPERM`). The component gets no Redis password in its
environment. `contracts/secret-key/component-acl.json` holds both languages to the same rules and derivations.

**D4. `SECRET_KEY` must be random.** Both rings refuse, at start, a key that:

- starts or ends with whitespace;
- is a published placeholder;
- is shorter than 32 characters;
- has fewer than 3 bits of entropy per character;
- is periodic or sequential;
- is readable text encoded as hex or base64.

The message says how to make one and how to move off a weak one. `SECRET_KEY_NEXT` must pass too, since it is about
to be written under. `SECRET_KEY_PREVIOUS` may be weak: that is how an install moves off a weak key.
`contracts/secret-key/strength-cases.json` holds both languages to the same answers. Compose files refuse to render
without a key (`${SECRET_KEY:?…}`). `env.template` ships it empty. `install.sh` generates 256-bit keys, and on an
existing weak key it generates a new one and keeps the old as `SECRET_KEY_PREVIOUS`, so nothing becomes unreadable.

**D5. The platform cipher's key is HKDF, and the move needs no downtime.** The AES key is
HKDF-SHA256(secret, salt `pipeshub/platform-cipher`, info `aes-256-gcm/v2`). Readers try, for each ring member, its
HKDF key and then its old SHA-256 key; writers use the HKDF key. A caller that still derives the old key is moved to
the HKDF writer by the platform cipher itself. `rotate-secret-key rotate` with no previous key moves every value the
old key sealed (the same steps and compare-and-sets as ADR-KEY-01 D4), and `retire` then confirms none is left.

A multi-replica install that wants no window in which an old replica meets a value it cannot read sets
`SECRET_KEY_KDF=sha256` for the rolling upgrade (writers stay on the old key, every new replica reads both), then
removes it. This is ADR-KEY-01 D3's two-step, for the derivation. The KEK and the run-key digests already run their
own HKDF over the secret's SHA-256 and do not change.

**D6. The legacy Docker API proxy holds its own credential, never the root key (R38-IDENT, reconciling SEC-A-04).**
R38-EGRESS gave `docker-socket-proxy` `SECRET_KEY` so it could derive its caller credential by HMAC. That proxy holds
the Docker socket, so a bug in it would hand over both the host and every stored credential: the same fault D1
removes from the sandbox plane. The credential is now `DOCKER_PROXY_TOKEN`, which `install.sh` generates (and keeps
across `--reconfigure`) and Compose gives the app and the proxy alone, refusing to start without it. Neither side
derives it from the root key; both refuse a weak one with the SEC-D-05 rules (`app/utils/encryption/secret_strength.py`,
stdlib only, so the proxy still starts without the app's dependencies); and the proxy refuses to start when it is
given `SECRET_KEY`, `SECRET_KEY_PREVIOUS` or `SECRET_KEY_NEXT`. The compose check that holds the sandbox plane to D1
names every service that faces sandboxes or holds the socket, so the next one cannot slip past it.

**D7. A component's peers' addresses travel sealed (R38IDENT-FU-10, amendment 2026-11-13).** The plain
`/services/endpoints` document is written by Node and readable and writable by any holder of the store's default
login, so such a holder could point the proxy's model-gateway upstream at its own host. Node now seals into each
component's document (D2) the four sandbox-plane addresses (`sandboxBroker`, `egressProxy`, `modelGateway`,
`toolGateway`), taken from Node's own environment first and its stored values second, exactly as Node writes the
plain document. A component's configuration service answers `/services/endpoints` from its sealed document and never
reads the plain one; its Redis user no longer has the key at all. There is no fallback: a document without addresses
names no peer, never the ones a store writer chose. A changed address is republished with the document (on start,
on rotation and every five minutes). Node and the components ship in one release and move together: in a rolling
upgrade a new component finds no addresses in an old Node's document until Node rolls, and an old component loses
read of the plain document once the new Node applies its user, until it rolls. Either way sandbox egress pauses for
that window, and nothing is sent to a wrong host.

**D8. The root key may arrive as a file (R38IDENT-FU-8, amendment 2026-11-14).** Every `SECRET_KEY` ring member
may be named by a file instead: `SECRET_KEY_FILE`, `SECRET_KEY_PREVIOUS_FILE`, `SECRET_KEY_NEXT_FILE`. One resolver
per language reads them (`ring_secret` in `secret_key_ring.py`, `ringSecret` in `secret-key-ring.ts`), and every
reader of the ring goes through it, so the KEK, the run-key digests, the platform cipher, the egress sealers and the
rotation steps all see the same key.

- A named file wins over the variable. The variable still works, so nothing changes for an install that sets none.
- One trailing line break is dropped (a file written by `echo` or an editor). Any other whitespace is the key's own
  and is refused by D4, as it is in the variable.
- A file that is missing, unreadable or empty stops the start. The message names the variable and the path, never
  the content. An empty file is not read as "unset": a mount that went wrong must not look like a missing key.
- A file every account can read is used, with one warning naming the variable (not the path). It is not refused, because Kubernetes
  and Docker set a secret's mode themselves (a pod's `fsGroup` adds group read).
- D1 and D6 hold the file variables to the same rule as the values. The proxy, the broker and the legacy Docker API
  proxy refuse to start when given any of them. The Compose check refuses a sandbox-plane service that names one
  or mounts any Compose secret.

Delivery:

- **Helm**: the default (`secretKeyAsFile: true`). The app reads `secret-key` from the app Secret through a volume,
  mode 0400, at `/run/secrets/pipeshub/secret-key`, and the pod spec has no `SECRET_KEY` variable. The Secret, its
  key and an existing or external secret are unchanged, so an upgrade only restarts the pod onto the file. `false`
  goes back to the variable, for an image older than the chart.
- **Compose**: opt-in, `docker-compose.secret-key-file.yml` in `PIPESHUB_COMPOSE_OVERRIDES`. Compose copies the key
  from `.env` into `/run/secrets/pipeshub_secret_key`, owned by uid 1000, mode 0400, and empties the variable.
  `install.sh` is unchanged and keeps writing and rotating the key in `.env` (mode 0600). A Compose file secret (`file:`)
  is bind-mounted with its host owner and mode, which Compose cannot change, so the override uses an `environment:`
  secret, and says how to switch to a host file owned by uid 1000.
- `SECRET_KEY_PREVIOUS` and `SECRET_KEY_NEXT` stay variables in both: they exist only while a rotation runs (ADR-KEY-01),
  and an optional key cannot become a file that must exist. An operator may still name them by file.

**D9. With etcd as the KV store, each component has its own etcd role (R38IDENT-FU-9, amendment 2026-11-14).** The
same split as D3, on etcd's role-based auth. etcd already accepts only clients with a certificate its CA signed, one
per component, and names a client by the certificate's CN (ADR-OPS-03 D1), so no password is generated, stored or
backfilled: the certificate is the credential.

- Node applies it with the Redis users, on every publish (`EtcdComponentRoles`, `adapters/etcd/etcd-component-roles.ts`),
  only where `KV_STORE_TYPE=etcd`. Role `pipeshub-<component>` reads exactly its own document; an extra permission
  or role is taken back. User `<component>` (no password) holds that role and no other.
- Before turning auth on, Node gives the root role to `root` (no password, and no certificate is issued for that
  name) and to its own certificate's user (`pipeshub-ai`), which the Python services in the app container share, so
  the platform keeps full access. An older Node rolled back to keeps it too.
- The component's change subscription watches its own document's key, not every key: a watch of `/` is refused
  under its role (`subscribe_changes(..., keys=...)`, ConfigurationService on the plane).
- etcd reached without a client certificate (an operator's own etcd) cannot tell components apart: the publish logs
  `agent_harness.component_publish_failed` with the action to take, and auth is not turned on.
- The etcd healthchecks keep working: `etcdctl endpoint health` counts "permission denied" as healthy.
- The CNs are in `contracts/secret-key/component-acl.json` (`etcd`), which Node's roles and Python's `internal_tls`
  leaves are both tested against. Proven on a real etcd 3.5.17 (R44-IDENT).

## As built

- Python: `backend/python/app/utils/encryption/secret_key_ring.py` (strength, `platform_cipher_key`,
  `cipher_keys`) and `backend/python/app/utils/encryption/component_key.py` (identity, subkeys, Redis login, ACL
  rules). `backend/python/app/harness/sandbox/egress_state.py` reads the proxy's sealing keys from its document.
  `AeadRing.of_keys` and the sealers' `of_keys` take derived keys.
- Node: `backend/nodejs/apps/src/libs/encryptor/secret-key-ring.ts`,
  `backend/nodejs/apps/src/modules/agent-harness/domain/component-keys.ts`,
  `backend/nodejs/apps/src/modules/agent-harness/application/component-credential-publisher.ts`,
  `backend/nodejs/apps/src/modules/agent-harness/adapters/redis/redis-component-logins.ts`.
- Compose: the `x-sandbox-plane-env` anchor holds no `SECRET_KEY` or Redis login. Helm: the broker pod reads
  `sandbox-broker-component-key`, which the chart generates once and keeps, and gets no Redis password. The lane
  (`deployment/e2e/run.sh`) and `install.sh` generate both component keys.
- Tests: `backend/python/tests/unit/harness/security/test_sec_d_ops_supply_chain.py` (the SEC-D-03 and SEC-D-05
  proofs, green); `backend/python/tests/unit/harness/security/test_sec_d_component_keys.py` (every Compose file, the
  installer's keys and checks); `backend/python/tests/unit/utils/encryption/test_secret_key_strength.py`;
  `backend/python/tests/unit/harness/test_credential_roles.py`;
  `backend/nodejs/apps/tests/libs/encryptor/secret-key-strength.test.ts` (including the cipher move by `rotate`);
  `backend/nodejs/apps/tests/modules/agent-harness/component-credentials.test.ts`;
  `deployment/helm/tests/test_secret_key_ring.py`.

## Deviations

- The finding proposed a KDF to slow brute force. HKDF does not slow it; what stops it is refusing a weak key (D4).
  HKDF gives the cipher key its own domain. A KMS-wrapped KEK stays the long-term step (SEC-D-05 fix 4).
- `SECRET_KEY_FILE` for the root key: built as D8 (R38IDENT-FU-8, R44-IDENT). Tests:
  `tests/unit/utils/encryption/test_secret_key_file.py`, N `tests/libs/encryptor/secret-key-file.test.ts`, the Compose
  checks in `test_sec_d_component_keys.py`, Helm `deployment/helm/tests/test_secret_key_ring.py` KH-4. Compose keeps
  the variable by default: making the file the default means changing what `install.sh` writes for every install,
  which no finding asks for while `.env` is mode 0600.
- etcd as the KV store had no per-component role: built as D9 (R38IDENT-FU-9, R44-IDENT). Tests: N
  `tests/modules/agent-harness/etcd-component-roles.test.ts`; P `tests/unit/config/test_etcd3_store.py`
  (`TestComponentScopedSubscription`), `test_configuration_service.py` (`TestComponentSubscription`),
  `tests/unit/utils/test_internal_tls.py` (the contract's users are issued CNs).
- SEC-D-03 fix 3 proposed signing `/services/endpoints`. Built instead as D7 (R38IDENT-FU-10, R42-SMALL): the
  document Node already seals for each component carries the addresses it calls, so there is no second key and no
  verifier to keep in step.
- A managed Redis that refuses `ACL SETUSER` (some cloud offerings) needs its operator to create the two users
  (R38IDENT-FU-12, closed by R42-SMALL). On a refusal Node tries the component's own login on a connection of its
  own; when it works, the operator created the user and the publish succeeds. When it does not, the publish fails
  with `ComponentLoginMissing`, and `agent_harness.component_publish_failed` carries the step to take (`action`).
  `node dist/scripts/component-acl.js` prints the same `ACL SETUSER` lines Node sends (the password as its SHA-256,
  or with `--password` the name and password a console asks for). The operator step is in
  `docs/deployment/aws-eks.md`, "Redis on ElastiCache".

## Owner amendments

None.

## Status

**Accepted** 2026-11-13 under the owner's direction of 2026-11-12 (first principles, long term, best UX).
