# Org context

Every request, consumed message and job runs inside an **org context**: the org (and user) the work acts for, held in a `ContextVar` (Python, `backend/python/app/core/org_context.py`) or `AsyncLocalStorage` (Node, `backend/nodejs/apps/src/libs/context/orgContext.ts`). The two modules mirror each other: `current()` / `currentOrgContext()`, `bind` / `runWithOrgContext`, `for_org` / `forOrg`, `system`, `spawn_detached` / `spawnDetached`.

## Where it is set

Only from a verified source, never from a body, query string or header other than `Authorization`. The one exception is a broker message without a `ctx` (an older producer, or signing switched off): the org then comes from `payload.orgId`, which is a claim, not a verified value, and is logged and counted (see Signed broker envelope):

| Entry point | Binds |
| --- | --- |
| Python `authMiddleware` (`api/middlewares/auth.py`) | the verified token's `orgId` and `userId`. A service token without `orgId` stays unbound; org-scoped routes refuse it (`service_token_org`, 401). |
| Node `AuthMiddleware.authenticate` and `scopedTokenValidator` | the verified token's `orgId` and `userId`. Pre-org scoped tokens (no `orgId`) stay unbound. |
| Broker consumers (Python Kafka/Redis Streams, Node Kafka/Redis Streams) | the org from the signed envelope (below). |
| Crawling worker | `forOrg(job.orgId, "crawl")` per job, after checking the org id is a valid id and the org exists and is active (otherwise the job is dropped, not failed). |
| Periodic loops (trash purge, storage release, named-entity sweep, sync resume, Node metrics refresh) | enumeration under `system(reason)`, each org's work under `for_org` / `forOrg` so nothing leaks between iterations. |

Outside these, no context is bound and `current()` raises `MissingOrgContext`. It is a bug in the caller, not a user error.

## Long-lived work

`asyncio.create_task` copies the creating request's context, and Node's `AsyncLocalStorage` follows timers, emitters and sockets created inside a request. A consumer loop, refresher or client created lazily inside request A would then serve every later event as org A. Start such work with `spawn_detached(coro)` / `spawnDetached(fn)` (empty context); use `spawn_in_request` when a child task should keep the caller's context. CI ratchets (below) block new bare task creation.

## Signed broker envelope (v2)

Wire shape: `{"eventType": "...", "ctx": {v, eventId, orgId, userId, requestId, issuedAt, sig}, "payload": "<JSON string>"}`. `sig` is HMAC-SHA256 over the topic, the message `eventType`, the ctx fields and the SHA-256 of the payload string as transmitted, keyed by HKDF-SHA256 of `scopedJwtSecret`. Re-sending a signed message with a different `eventType`, a different payload, another topic or another org fails verification. `ctx.requestId` (a trace id) is signed but not acted on. Consumers dead-letter a bad signature, a future `issuedAt` (more than 300 s) and a `payload.orgId` that differs from `ctx.orgId`. A `ctx` of any other shape, and a top-level `org_ctx` key (which is stripped), never reach a handler.

- **Producers.** They sign only when `ENVELOPE_SIGN=true`. A message that cannot be signed (no org bound, or a payload naming another org) is sent unchanged and counted in `pipeshub_envelope_unsigned_total{topic,reason}`; with no key it raises in enforce mode and is counted and logged in shadow mode. Retries re-publish under the message's own org, so they are signed like the original. Enable `ENVELOPE_SIGN` only once every consumer of the topic reads v2.
- **Unsigned messages.** While `ENVELOPE_SIGN` is off, a message with no `ctx` is v1: it binds from `payload.orgId` (logged, counted in `pipeshub_envelope_ctx_missing_total{topic}`). While it is on, a message with no `ctx` is a stripped message, counted in `pipeshub_envelope_stripped_total{topic}` and logged; `enforce` mode rejects it, shadow mode accepts it through the same fallback. A v2 message that lost its `ctx` is delivered in the v1 shape (decoded payload), or rejected as malformed. With `ENVELOPE_CTX_REQUIRED=true` any message without `ctx` is dead-lettered. A consumer that writes org-scoped tables must call `assert_may_start` / `assertMayStart`, which refuses to start while that flag is false.
- **Signature age.** A validly signed message older than `ENVELOPE_MAX_AGE_SECONDS` (default 900) is stale: counted in `pipeshub_envelope_stale_total{topic}` and logged in shadow mode, rejected in enforce mode. The window must exceed the longest legitimate time between signing and the last open of a message. A retry is re-signed when it is re-published, and its backoff (at most 300 s) is waited after the message is opened, so retries are inside the window. A message redelivered unchanged (consumer crash, rebalance, a pending Redis entry claimed after `claim_min_idle_ms`) keeps its original signature time, and so does a message that waits in a long backlog. Watch `pipeshub_envelope_stale_total` in shadow mode and raise the window above the observed lag, and above `RECORD_PROCESSING_TIMEOUT` (default 1800 s) if crash redelivery of long records matters, before switching to enforce.
- **Replay.** Inside the window a signed message can still be delivered twice; exactly-once replay rejection needs a durable consumer inbox that records event ids in the same transaction as the effect. The hook is in place (`configure(keys, inbox)` / `configureEnvelope(keys, inbox)` take an object with `claim(topic, eventId, orgId)`), and the database-backed inbox is a follow-up. No Redis seen-ids are kept.
- **Key.** Each service calls `configure_from_config` / `configureEnvelopeFromSecret` at startup (key derived from `scopedJwtSecret`). A Python service whose key cannot be read retries (briefly at startup, then in the background with backoff) and is never silent. While there is no key and signing or `ENVELOPE_CTX_REQUIRED` is on, a message carrying a `ctx` is rejected in enforce mode and processed through the counted, loudly logged fallback (`pipeshub_envelope_unverified_total{topic}`) in shadow mode. With neither flag on, it is processed through the counted fallback with a warning. With a key, a bad signature is always rejected.
- A shared test vector pins the Python and Node implementations to one canonical string.

## Shadow and enforce

`ORG_CONTEXT_MODE` is `shadow` (default) or `enforce`; any other value stops the service at startup. At org-scoped choke points, an access with no ambient org (`missing`) or an explicit org differing from the ambient one (`mismatch`) is, in `shadow`, counted (`pipeshub_org_context_violations_total{kind,site}`) and logged once per site with the call site, then allowed. A call made with the org-less bootstrap org `system` is counted as `system_token`, not `mismatch`, and is refused in `enforce` like a mismatch, so such a call must bind the org it acts for. In `enforce` it raises `MissingOrgContext` / `OrgMismatch`. Python: every `org_id` argument of every `IGraphDBProvider` implementation method (wrapped by `instrument_org_methods`). Node: `ambientOrg(orgId, site)`, currently at the crawling worker. Flip to `enforce` once the counters are zero over the simulator sites and the scale matrix.

## Service tokens without an org

A service token with no `orgId` stays unbound. Python answers 401 ("Token missing orgId") on every route except the pre-org allowlist `PRE_ORG_SERVICE_ROUTES` (`api/middlewares/auth.py`):

| Route | Why |
| --- | --- |
| `GET /api/v1/parse/providers` | lists parser providers; no org data |

Adding a route is a security decision; a test pins the list. Node scoped tokens without an org (pre-org login and mail flows) stay unbound and are not yet gated by an allowlist.

## Guards

- Python: `backend/python/tests/unit/core/test_org_context_guards.py` (baseline `org_context_baseline.json`).
- Node: `backend/nodejs/apps/tests/libs/context/org-context-guards.test.ts` (baseline `org-context-baseline.json`).

Both fail when a file gains a bare task/timer/worker creation (detached_work) or a new direct read of `orgId` off claims or the request user (direct_org_reads). Counts may only go down; re-record with `ORG_CONTEXT_UPDATE_BASELINE=1` after an intentional change.
