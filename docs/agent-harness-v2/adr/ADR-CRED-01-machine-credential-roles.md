# ADR-CRED-01: one machine credential per role, each admitted only where that role calls

- Fixes SEC-C-10 (R38-IDENT, 2026-11-13).
- Amends: REPORT 6.1.8 (the per-deployment worker credential) and C9.04 (the scraper's credential).
- Related: SEC-C-10, [ADR-KEY-02](ADR-KEY-02-per-component-keys.md).

## Context

One bearer secret, the worker credential, stood in for four callers with very different needs:

- Prometheus reads `/metrics` (Node writes the credential to a file for it);
- the egress proxy mints a run's gateway token and reads the operator egress stops;
- the sandbox broker registers bindings with the egress proxy;
- the harness worker claims runs and reads their state.

Every holder could do what any other could: `POST /runs/claim` hands back any org's run and its attempt token. So a
foothold in the scraper's volume, in the proxy (which parses sandbox-driven HTTP and TLS) or in the broker became a
read of every org's runs. The proxy and the broker also verified callers by holding the credential itself, which
meant holding the power to claim.

## Decision

**D1. Four roles, four credential sets.** `worker`, `scrape`, `egress-proxy`, `sandbox-broker`
(`backend/nodejs/apps/src/modules/agent-harness/domain/credential-roles.ts`). Each is a random `phw_` secret Node
generates, stores encrypted like the worker credential (`/services/agentHarness/roleCredentials/<role>`; the worker
keeps its old path) and rotates the same way.

**D2. Each route admits the roles that call it, and no other.** On Node's internal listener:

| Route | Role |
| --- | --- |
| `GET /metrics` | `scrape` |
| `POST /runs/:runId/gateway-token`, `GET /operator-controls/egress` | `egress-proxy` |
| `POST /runs/claim`, `POST /runs/:runId/claim`, `GET /runs/:runId/audit`, `GET /control-plane/orgs/:orgId/live` | `worker` |

On the services' own listeners: the broker admits `worker` on its routes and `scrape` on `/metrics`; the egress
proxy admits `worker` and `sandbox-broker` on its control routes and `scrape` on `/metrics`; the gateways admit
`scrape` on `/metrics`. A credential of another role is refused exactly as a wrong one is (401, the same message), so
a caller learns nothing about which roles exist. The guard carries its roles (`requireRoleCredential`), and the
cohesion test C-26 holds the table above.

**D3. The proxy and the broker verify by digest.** They never hold another role's credential: the document Node
seals for each (ADR-KEY-02) carries SHA-256 digests of the credentials it admits, with the previous one's expiry.
Every entry is compared in constant time, so the time taken does not say which role or slot matched.

**D4. Rotation needs no restart and no downtime.** An operator rotation (`ROTATE_SIGNING_SECRETS`, or
`rotateWorkerCredential`) rotates every role at once. Each verifier keeps accepting the previous credential for
`PREVIOUS_WORKER_CREDENTIAL_GRACE_MS` (15 minutes). Every holder re-reads its credential on use (the file Prometheus
reads is rewritten each minute). Node republishes the components' documents at once and every five minutes.

**D5. Refusals are logged per route with their reason (`authority.refused`), never with a credential.**

**D6. The proxy's credential names no run; the broker's proof does (R38IDENT-FU-7, R41-OPS, 2026-11-13).**
`POST /runs/:runId/gateway-token` needs, besides the `egress-proxy` credential and a leased attempt, a `bindingProof`
the sandbox broker made for exactly that org, run and attempt. Without it a foothold in the proxy could mint a token,
and so call the model and tool gateways, as any run of any org that held a lease.

- *Who signs.* Every egress binding reaches the proxy through the broker's `DockerEgressWiring.bind`, from the labels
  the worker gave the sandbox (a kept sign-in sandbox's bound keys are set again on each re-acquire), so the broker is
  the one place that knows which attempt a sandbox serves. It signs there, and again on every resync after a proxy
  restart; the proxy keeps the proof with the binding (in its sealed state too) and forwards it uninterpreted.
- *The proof.* `v1.` + base64url (no padding) of HMAC-SHA256 over `pipeshub sandbox binding v1` followed by org, run
  and attempt, each a 4-byte big-endian length and its UTF-8 bytes. Node accepts exactly one spelling.
- *The key.* HKDF of the broker's component key (`sandbox-broker/binding/v1`, ADR-KEY-02's derivation), which Node
  already holds as `SANDBOX_BROKER_COMPONENT_KEY`; a broker run holding `SECRET_KEY` (from source, no component key)
  signs under HKDF(`SECRET_KEY`, salt `pipeshub/sandbox-binding`, info `ring/v1`). Node accepts the component subkey
  and both ring members' keys, read per request, so a `SECRET_KEY` rotation in flight keeps sandboxes served. The
  proxy holds neither input. Nothing new is stored or sealed: the proof needs no Node state, so a Node restart, a
  broker restart (it re-signs adopted sandboxes) and Node replicas all agree.
- *Why not a broker route recording bindings in Node.* That needs a store, its expiry and its cleanup on every
  release and crash, and a second write on the sandbox start path; the attempt's lease already bounds a proof's life
  (a proof for an attempt that let go mints nothing), so a stateless proof gives the same binding with none of that.
- *Refusals.* A missing proof is 400 (the strict body); a wrong one is 401 before any run is read, logged as
  `gateway_token.binding_refused` with org, run and attempt only. A binding with no proof is never sent: the proxy
  refuses the sandbox's request as `binding_unattested`, so a broker that signs nothing does not look like Node down.
- *Contract.* `contracts/secret-key/sandbox-binding.json` holds Node (`domain/sandbox-binding.ts`) and Python
  (`app/harness/sandbox/binding_proof.py`) to the same keys, bytes and proofs.

## As built

- `HarnessAuthority.credentialFor`, `credentialSetFor` and `verifyRole`; `requireRoleCredential` and `routeRolesOf`
  in `backend/nodejs/apps/src/modules/agent-harness/transport/internal-auth.ts`.
- Prometheus' credential file and the metrics fan-out use the `scrape` credential (`app.ts`, `internal-routes.ts`).
- Python: `backend/python/app/harness/credential_roles.py` (`RoleCredentialAuth`, `ComponentCredentials`,
  `PlatformRoleVerifier`). `BrokerAuthVerifier.verify` takes the request path, so a listener can admit different
  roles on `/metrics`.
- Tests: `backend/nodejs/apps/tests/cohesion/c26-internal-route-guards.test.ts` (the role table, and no claim or read
  route admitting the scraper, proxy or broker); `backend/nodejs/apps/tests/modules/agent-harness/sec-c-platform-appsec.test.ts`
  (SEC-C-10's proof and its bypasses: every outside role on every claim and read route, gateway-token minting by the
  wrong role, header spellings, rotation grace); `backend/nodejs/apps/tests/modules/agent-harness/component-credentials.test.ts`;
  `backend/python/tests/unit/harness/test_credential_roles.py`. D6: `backend/nodejs/apps/tests/modules/agent-harness/sandbox-binding.test.ts`
  (vectors, every malformed or foreign proof), `gateway-token.http.test.ts` (the route's bypasses: another run,
  attempt or org, a foreign key, tampering and re-encodings, no proof, replay after a re-claim, a Node with no key);
  `backend/python/tests/unit/harness/security/test_r41ops_sandbox_binding_proof.py` (vectors, the broker signs on bind
  and resync, kept sandboxes, the wire and sealed state); `backend/python/tests/unit/harness/gateway/test_node_token_source_contract.py`
  (the proxy's token source against the real Node router).

## Deviations

- The finding suggested a gateway-token-only credential "for the proxy". The proxy also reads the operator egress
  stops, so its role admits both routes; it still claims nothing and reads no run's state.
- ~~`POST /runs/:runId/gateway-token` still names any run with a lease~~: closed by D6 (R38IDENT-FU-7). A proxy
  compromise still reaches every attempt whose sandbox it fronts while that attempt holds its lease, which it already
  sees the traffic of.
- A Kata or Kubernetes sidecar that registers bindings must sign the same proof (R41OPS-FU-5, with P5.3).
- ~~A proxy upgraded alone while runs are in flight refuses tokens for bindings restored from sealed state written before
  D6 (no proof) until the broker re-registers them~~: closed by R49-SMALL (R41OPS-FU-6). A restored binding record that
  does not name `attestation` at all (only a proxy from before D6 wrote one that way) changes the proxy's instance id, so
  every broker's resync registers its sandboxes again, signed; egress goes on meanwhile.

## Owner amendments

None.

## Status

**Accepted** 2026-11-13 under the owner's direction of 2026-11-12 (first principles, long term, best UX).
