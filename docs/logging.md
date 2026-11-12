# Logging

Goal: from the logs alone, tell **what** happened, **why**, and **with whom**. This page is the contract every logging change
follows. The Python side is `backend/python/app/utils/logger.py`; Node mirrors the line shape and field names.

## Configuration (Python)

| Env var | Values | Default | Effect |
| --- | --- | --- | --- |
| `LOG_LEVEL` | `debug`, `info`, `warn`/`warning`, `error`, `critical` (any case) | `info` | Level of the `app` logger, of every `create_logger` logger, and of the root logger. Root never goes below `info`, so third-party libraries never log at debug by accident (some log whole requests, prompts and bodies there). Use `debug`, `info`, `warn` or `error`: Node reads the same variable. |
| `LOG_LEVELS` | `name=LEVEL,name=LEVEL` | empty | Per-logger levels, applied last. Any logger can be named, `root` included. `LOG_LEVELS=app.services.graph_db=DEBUG,aiokafka=WARNING`. `LOG_LEVELS=root=DEBUG` opens third-party debug output on purpose. |
| `LOG_FORMAT` | `text`, `json` | `text` | Both runtimes: Node reads it at start-up (`Logger.applyEnv`) with the same line shape. |
| `DB_SLOW_QUERY_MS` | milliseconds, `0` = off | `500` | A database client call (ArangoDB, Neo4j) or Postgres statement that succeeds after at least this long writes `db.query.slow` at WARNING. An unusable value means `500`. |

A bad value never stops a service: an unknown `LOG_LEVEL` (or `notset`) means `info`, a malformed `LOG_LEVELS` entry is skipped,
an unknown `LOG_FORMAT` means `text`; each logs one warning. Values are read at import and again whenever they change before a
`create_logger` call (so a `.env` loaded later is honoured), and once more at service start-up (`install_log_redaction` in each
lifespan). Per-service files keep the format they were opened with.

Without a `LOG_LEVELS` entry, `opensearch` stays at `WARNING` and, when `DATA_STORE=neo4j`, `neo4j.notifications` at `ERROR`.
The successful (2xx) request lines of httpx and httpx2 (the MCP 2.x client) are always dropped; failures are kept, with credentials cut from the URL.

`LOG_LEVEL` does not reach first-party loggers named outside `app` (`agent_loop.events`, `sync_point`,
`pipeshub.attachment_audit`, `parsing_main`, `extraction_main`, `docker_proxy`); name them in `LOG_LEVELS` until they are renamed.

## One stream

Every logger, `logging.getLogger(__name__)` and `create_logger(name)` alike, writes through one handler on the root logger, to
**stdout**, with one formatter and redaction. Colour (yellow WARNING, red ERROR) is added only when stdout is a terminal.
`create_logger` adds no console handler of its own; it adds `logs/<name>.log` when the logging provider keeps files, and that file
holds only the lines of that logger and its children. stdout is the complete record.

Exceptions: uvicorn's own handlers (`uvicorn` on stderr, `uvicorn.access` on stdout). Under `LOG_FORMAT=json` they get the JSON
formatter at service start-up; the few lines uvicorn writes before that stay text. query, connectors and indexing run uvicorn
with `access_log=False` (`http.request.end` replaces the access line), and uvicorn's `Exception in ASGI application` is dropped
for an exception that `http.request.end` already logged. If a script configures the root logger before
it imports `app.utils.logger` (its own `basicConfig`), its handler is kept and no console handler is added.

## Line shape

Text (default):

```
2026-11-05 23:48:47,497 - connector_service - INFO - [req:<id> thr:<thread> task:<task>] [sync.py:42] - sync_run.end | connector_id=c1 status="ok two" duration_ms=812
```

The `[req:…]` part appears only while a request, message or job context is bound. Fields come after ` | ` as `key=value`,
separated by single spaces, in the order they were passed. A field whose value is `None` is left out. A traceback follows on
the next lines.

JSON (`LOG_FORMAT=json`): one object per line.

```json
{"timestamp": "2026-11-05T23:48:47.497Z", "level": "info", "logger": "connector_service", "filename": "sync.py", "line": 42, "message": "sync_run.end", "request_id": null, "connector_id": "c1", "status": "ok two", "duration_ms": 812}
```

| Key | Value |
| --- | --- |
| `timestamp` | UTC, ISO 8601, milliseconds, `Z` |
| `level` | lowercase level name, the same in both runtimes: `debug`, `info`, `warning`, `error`, `critical` |
| `logger` | the Python logger name (Node's `service` is its module or facade name; the process tier is `service_tier`) |
| `filename`, `line` | the call site |
| `message` | the message with `%` arguments applied |
| `request_id` | the bound request id, or `null` |
| fields | each `extra=` field with its JSON type (NaN and infinities as strings; a number the credential rule in Redaction masks as `"[REDACTED]"`); a field named like a standard key does not replace it, and `stack` is reserved (left out in text and JSON) |
| `stack` | traceback and `stack_info`, when present |

A logging provider (`app/utils/structured_logging`) may supply its own console formatter, decide whether per-service files are
kept, and decide how the org and user are shown: `log_identity` turns `{org_id, user_id}` into the `org_id`/`user_id` fields
(the OSS provider returns them raw; the default of the interface shows neither), and `hash_org_id` sets
`record.organization_id_hash`, an attribute for the provider's own formatter that the OSS text and JSON do not print. A provider
formatter still gets redaction; to print the context fields it calls `app.utils.logger.log_record_fields(record)`.

### Field values in text

A value is printed bare if it matches `^[A-Za-z0-9._:@/-]+$` (letters, digits and `.` `_` `:` `@` `/` `-`, so
snake_case values such as `error_class=rate_limited` stay bare); otherwise it is printed as a JSON string (`json.dumps(str(v),
ensure_ascii=False)` in Python, `JSON.stringify(String(v))` in Node). Booleans print as `true`/`false`; `None`/`null`/`undefined`
are left out. Use ints for numbers. These vectors are the shared test table for both runtimes (each line is
`[input, rendered]`; `null` rendered means the field is left out):

```json quoting-vectors
["c1", "c1"]
["65f0a1b2c3d4e5f6a7b8c9d0", "65f0a1b2c3d4e5f6a7b8c9d0"]
["sync_run.end", "sync_run.end"]
["rate_limited", "rate_limited"]
["web_search", "web_search"]
["job:kh_restamp", "job:kh_restamp"]
["_", "_"]
["a/b:c@d-e_f", "a/b:c@d-e_f"]
["ok two", "\"ok two\""]
["", "\"\""]
["a,b", "\"a,b\""]
["a+b", "\"a+b\""]
["it's", "\"it's\""]
["ü", "\"ü\""]
["x#1", "\"x#1\""]
["(1)", "\"(1)\""]
["a\"b", "\"a\\\"b\""]
["a=b", "\"a=b\""]
["a|b", "\"a|b\""]
["a\\b", "\"a\\\\b\""]
["line1\nline2", "\"line1\\nline2\""]
["tab\there", "\"tab\\there\""]
["\u0001", "\"\\u0001\""]
[12, "12"]
[-3, "-3"]
[0, "0"]
[true, "true"]
[false, "false"]
[null, null]
```

## Context and fields (Python)

Who and which work a line belongs to are added to **every** line from what is bound, with no change at the call site:

| Field | From |
| --- | --- |
| `org_id`, `user_id` | one source: `app.core.org_context` when bound (a system context has neither), else the model call's principal (`services/model_identity/call_context`; `user_id` only for a user), else the request context's org. Through the provider's `log_identity`. |
| `ctx_kind`, `ctx_source` | the OrgContext, when it is not a user's: `ctx_kind=job ctx_source=job:kh_restamp`, `ctx_kind=service ctx_source=scoped_jwt`. Python only. |
| `conversation_id`, `agent_id`, `record_id`, `connector_id`, `workload` | the model call context |
| bound ids | `bind_log_fields` / `log_fields` |
| `extra=` fields | the call site; an `extra=` value replaces a bound one, never `org_id`, `user_id`, `ctx_kind` or `ctx_source` |

Identity comes first, then the call context's ids, then bound ids, then `extra=` fields. Tests read them with
`log_record_fields(record)`.

```python
from app.utils.logging_fields import bind_log_fields, log_event, log_fields, log_once

with log_fields(connector_id=cid, run_id=run_id):          # every line inside gets them
    ...
bind_log_fields(run_id=run_id)                              # at the top of a task: lasts as long as the task
log_event(logger, "sync_run.end", status="ok", duration_ms=812)
if log_once(f"envelope:stripped:{topic}", 30): ...          # bounded keys only, prefixed with the module
```

- Bindable ids (`LOG_FIELD_NAMES`): `conversation_id`, `agent_id`, `run_id`, `parent_run_id`, `agent_run_id`, `attempt`, `connector_id`,
  `connector_type`, `record_id`, `kb_id`, `tool`, `tool_call_id`, `mcp_server_id`, `toolset_id`, `job`, and for credential
  refresh `refresh_kind`, `trigger`, `provider`, `scheduled_by`, `owner_id`, for broker messages `topic`, `message_id`,
  `event_type`, and for the user a step acts on `target_user_id` and `source_user_id` (see "Personal data"). A module that
  needs another adds it there and here. A name with
  a credential word in it (`token`, `secret`, `credential`, …) is masked like a secret, so ids never use one. Another key, or `org_id`/`user_id`, raises `ValueError`. `None` values are skipped. Pass literal keyword
  arguments, not a `**dict`. Do not hold `log_fields` across `yield` in an async generator.
- `log_event(logger, event, /, *, level=logging.INFO, **fields)`: message = the event name; `None` values, identity names and record
  attribute names are dropped; `[file:line]` is the caller's.
- Work entering with no request: `org_context.for_org` / `system` / `bind` also scope the request context (same root, or a new
  `sys-` root; the org of the scope), so job lines show `[req:sys-…]` and messages published there carry `requestId` and `orgId`.
  `org_context.spawn_detached(coro, name=…)` gives the task a fresh `sys-<name>-<hex>` root; a sync started under it mints its own.
  That root is shared by everything the task does and travels in what it publishes, so a loop that publishes per-unit work
  uses `run_periodic` or binds a root per unit.
  `await run_periodic(name, interval_s, fn)` (`app.core.periodic`; start it with `spawn_detached`) runs each tick in a fresh context
  with its own root and `job=<name>`, and ends it with `job.tick.end` (DEBUG on success, the failure's level otherwise).
- **Threads.** The context is copied by `asyncio.create_task`, `spawn_in_request`, `asyncio.to_thread` and
  `run_coroutine_threadsafe`. It is **not** copied by `loop.run_in_executor`, `ThreadPoolExecutor.submit`, `threading.Thread`, queue
  workers or child processes. There, run the work in a copy:
  `loop.run_in_executor(ex, contextvars.copy_context().run, fn, *args)`,
  `ex.submit(contextvars.copy_context().run, functools.partial(fn, **kwargs))`, or capture `copy_context()` when enqueuing and have
  the worker call `ctx.run(job)`. This also carries the OrgContext into the thread (`org_context.current()` then succeeds there), so
  test it.

## Failures

```python
from app.utils import error_class

try:
    ...
except Exception as e:
    error_class.log_failure(logger, "sync_run.end", e, connector_id=cid)   # where the error is handled, once
    raise
```

- `classify_error(exc_or_status)` is one of `auth_expired`, `forbidden`, `not_found`, `rate_limited`, `timeout`, `upstream_5xx`,
  `bad_request`, `conflict`, `unavailable`, `cancelled`, `misconfigured`, `internal`. `misconfigured` is **our** configuration: the
  LLM provider rejected our key, no LLM is configured, an OAuth client registration was rejected, or a service token was refused.
  `auth_expired` is a user's or a connector's grant that expired or was revoked. The first decisive exception in the chain wins,
  outermost first (`__cause__`, else `__context__` unless `from None`). OAuth error codes are read before the HTTP status, our
  own `HTTPException` 5xx counts as `internal`, and our programming errors (`KeyError`, `TypeError`, …) stop the walk. It never raises.
  Domain codes (`ParseErrorCode`, `MCPFailureReason`) go in their own field (`code=`, `reason=`).
- **Levels** (`level_for(error_class)`): `internal` and `misconfigured` → ERROR, `cancelled` → INFO, every other class → WARNING.
- `log_failure(logger, event, exc, /, *, level=None, error_class=None, include_error_text=True, **fields)` adds `error_class` (an
  explicit one wins: an LLM transport passes `"misconfigured"` for a provider 401/403), `error_type`, `error_message` (message and
  cause chain, URLs, account ids and secrets masked, at most 300 characters), `event_name` and `outcome="failure"`. The stack is
  written once, on the first line for that exception at ERROR or above. A later `log_failure` of the same exception, or of one
  raised `from` it, is DEBUG unless it asks for a higher level. `include_error_text=False` leaves `error_message` out and prints
  each exception in the stack as `Type: <message omitted>`, for errors whose text can echo what was sent (an MCP or OpenAPI server's
  answer quoting tool arguments). That record carries the stack as `exc_text` only, with no `exc_info`, so a handler that formats
  `exc_info` itself cannot print the message either.
- `logged_level(exc)` is the highest level `log_failure` already wrote `exc` (or an exception it was raised `from`) at, or `None`.
  A per-unit line that must keep its level when several units fail with one shared exception checks it: `log_failure` when it is
  `None` or lower, else `log_event` at the unit's level with the error fields (see "Tool calls and MCP").
- A lifecycle `*.end` line that must stay at INFO when the work failed uses `log_event(..., level=level_for(cls), error_class=cls,
  outcome="failure")`. `outcome` is `success`, `failure`, `partial` or `skipped` (cancellation is `failure` with `error_class=cancelled`).
- `app.agents.agent_loop.error_classification` also has a `classify_error` (user-facing text). Where both are in scope, import this
  module qualified, as above.

## HTTP requests (Python)

`install_request_logging(app)` (`app/api/middlewares/request_log.py`) is called by `query_main`, `connectors_main` and
`indexing_main` where they used to add `RequestContextMiddleware`. It writes one `http.request.end` per request when the
response is done (for a stream, when it ends), inside the request's context: the line has the request id and the org and user
the auth middleware bound.

| Event | Level | Fields |
| --- | --- | --- |
| `http.request.end` | INFO; DEBUG for a `/health*` answer below 400; INFO for a 4xx (WARNING for `reason=org_mismatch`); WARNING for a 5xx answered without an exception; an unhandled exception: ERROR with its stack (`internal`); an `HTTPException` of 500 or more that was raised: its class's level; WARNING at most, with no second stack, for an exception already logged; INFO with `error_class=cancelled reason=client_disconnect` when the client left before the answer was complete (see "When the client leaves" below) | `method`, `route`, `status`, `duration_ms`, `client_ip`, `outcome`, `error_class` (4xx, 5xx), `reason`, and for an exception the `log_failure` fields plus `cause_type` |

- `route` is the template (`/api/v1/kb/{kb_id}`), the same one `MetricsMiddleware` uses (`app/utils/route_template.py`); a request
  refused before routing gets the route it would have reached. `unmatched` when no route matches. The raw path and the query
  string are never logged.
- `status` is what the client received (for a client that left before the answer, what the route answered), and the level
  follows it: an `HTTPException(500)` an edition handler answers with 402
  is an INFO 4xx line. An unhandled exception before anything was sent is answered here with 500
  `{"detail": "Internal server error"}` (the same body in all three services) and the connection stays open. A stream that fails
  midway keeps the status already sent, with `outcome=failure`; the exception is re-raised, so uvicorn closes the connection.
- **An exception is logged once, here.** A route raises (`raise HTTPException(500, …) from e`); it does not log and re-raise. An
  exception nobody handled is `internal`, whatever its class (a leaked `ConnectionError` is our bug: the client got a 500). A
  raised `HTTPException` with nothing in its chain is classified by its status (500 `internal`, 503 `unavailable`, 504 `timeout`),
  one raised `from` a failure by that failure. A request whose exception was already logged (lower down, or behind another
  request that failed with the same object) still gets its own line, WARNING at most, without the stack.
- The client-facing `detail` is not a field. It appears only inside a 5xx line's `error_message` (the exception's text), redacted.
- **Auth refusals** in `app/api/middlewares/auth.py` and `admin_gate.py` raise `AuthRejected` (an `HTTPException`; the client
  gets the same status, detail and headers), whose `reason` this line carries: `missing_header`, `bad_scheme`, `missing_token`,
  `no_scopes`, `expired`, `invalid_token`, `scope_not_accepted`, `missing_org`, `session_ended`, `token_revoked`, `unconfirmed`
  (503: Node could not confirm the session), `unauthenticated`, `service_token_not_allowed`, `service_token_required`,
  `insufficient_scope`, `org_mismatch`, `not_admin`, `auth_error`, `auth_config` (500). A refusal is INFO because an expired
  session is routine; `org_mismatch` (a service token naming another org) is WARNING. Alert on `reason`, not on the level.
- Code that turns a failure into a response itself (the auth middleware's 401, a route that answers a 200 after a dependency
  failed) hands it over with `mark_request_failure(request, exc=None, /, *, error_class=None, reason=None,
  include_error_text=True)`. The line then logs it as a failure whatever the status: a 2xx or 3xx and a 5xx at the class's
  level (with the stack once for `internal`), a 4xx at INFO. `error_class` and `reason` win over the ones read from `exc`; a
  marked bare `HTTPException` is classed by its own status, not by the one answered. `include_error_text=False` keeps its text
  off the line, as in `log_failure`. An `HTTPException` raised `from` the marked one (`raise HTTPException(502) from e` after
  marking `e`) takes all its hints. An exception that escapes unhandled takes `error_class` and `reason` only when it is the
  marked object itself: `BaseHTTPMiddleware` re-raises with the implicit context as the cause, so a bug raised while handling a
  marked failure stays `internal` with its stack. `include_error_text=False` covers any exception whose chain (cause, or
  unsuppressed context, as in the text) holds the marked one.
  `exc` may be None for a failure with no exception (an OAuth callback that answers a redirect); `error_class` is then required
  (TypeError otherwise), and the line has no `error_type`, `error_message`, `cause_type` or stack. An `HTTPException` raised
  anywhere below is handed over by the exception handler, which then calls the handler registered before it.
- Ids: `bind_request_log_fields(request, toolset_id=…)` puts them on the route's lines and on this one. A router binds its path
  parameters with `dependencies=[Depends(path_log_fields(instance_id="toolset_id"))]` (an async dependency: a sync one runs in
  a thread, and the route's own lines would lose the binding). A route marks the `HTTPException` it raises, not its cause, then
  raises it `from` the cause. `add_request_log_fields(request, oauth_error=…)` adds a bounded event field to this line only.
- `cause_type` is the type name of the innermost cause (`__cause__`, else `__context__` unless suppressed; a group counts as its
  first leaf), on a failure line whose exception has a chain; it is a type name only, so it stays with
  `include_error_text=False`. For `HTTPException from MCPOAuthError from httpx.ConnectError` the line has
  `error_type=HTTPException cause_type=ConnectError`: the middle domain error is not on the line. A route that needs its domain
  code there marks it with `reason=`.
- `client_ip` is the peer as uvicorn resolves it (`FORWARDED_ALLOW_IPS`, default loopback), usually the Node API, and only when it
  parses as an IP address. The end user's address is on Node's `http.request.end` with the same `request_id`.
- **When the client leaves.** The middleware reads `receive` itself, one message ahead of the app, so it sees an
  `http.disconnect` whenever it arrives, even while a route is still working on a plain response (uvicorn drops that answer
  silently). What the line says depends on when it saw the client leave (`_end_outcome`, in this order):

  | Seen | Line |
  | --- | --- |
  | an exception escaped the route | the exception's line, whatever else: `ClientDisconnect` (a body read after the client left) is `cancelled reason=client_disconnect`, a cancelled request `cancelled`, anything else `internal` with its stack |
  | after `http.response.start`, before the last body message; or the client was not seen leaving and the last body message never came | `cancelled`, whatever the status or a marked failure: the client never got the whole answer (a `BaseHTTPMiddleware` below may still send a last body message after the disconnect) |
  | before `http.response.start` (the route answered after the client left) | a 4xx, a 5xx or a marked failure keeps its own line; a 2xx, a 3xx or no answer is `cancelled` |
  | never, or after the last body message was handed over | the normal line |

  "After the last body message was handed over" includes the time the server is still writing it: from this middleware that
  cannot be told apart from uvicorn's `http.disconnect` for a complete response (measured: in the indexing layout it arrives
  before `send` returns for every small answer), so a client that leaves while a large last message is being written is
  logged by the normal line.
- Limits: an org bound by a route dependency (not by the auth middleware) runs in a child task, so the line has no `org_id`
  from it (only the request context's org, without `user_id`). An exception raised by `MetricsMiddleware` or
  `RequestContextMiddleware` themselves gets uvicorn's line only. A middleware added before `install_request_logging` sits
  inside it, so the crash 500 sent here does not pass through it: an edition that adds `CORSMiddleware` adds it after
  `install_request_logging`, so that it wraps that 500 and the CORS headers stay. A mounted sub-app (`app.mount`) gets one line,
  but its `route` lacks the mount prefix and its own `HTTPException`s are not handed over: install on the sub-app too. None of
  the three services mounts one. Because the middleware reads `receive` first, uvicorn answers `Expect: 100-continue` as soon
  as the request starts; in query and connectors that is before an auth refusal (`100 Continue`, then `401`). When a route
  never reads a body larger than one message, or reads it only after its slow work, the line does not see the client leave
  (the read-ahead holds one message and waits for the route). A client that leaves before its request reaches a route that
  answers without awaiting anything can still be logged `success` when no `BaseHTTPMiddleware` sits below this middleware
  (indexing): the last body message is handed over before the read-ahead has read the disconnect. `request.is_disconnected()` sees the disconnect in a route with
  no `BaseHTTPMiddleware` between it and this middleware (indexing); behind the auth middleware (query, connectors) its poll
  is still cancelled before it reads.
- Node writes the same event and fields (`HttpAccess`, `access-log.middleware.ts`). Node also logs the error on its own
  `http.request.error` line; Python logs it on this one.
- Node: a 401 for a credential the request submitted (a sign-in code, a password, a CAPTCHA, an identity-provider token) that
  is wrong, expired or already used is a `WrongCredentialError`, logged `error_class=bad_request`; the request's own session or
  token refused stays `auth_expired`. `status=401 error_class=bad_request` on `http.request.end` selects credential guessing.

## Connector sync runs

`run_sync_task` (`app/connectors/core/sync/sync_runner.py`) is the one body every sync runs in. It binds `run_id` and
`connector_id` for the run; the entry points (`EventService.process_event`, `ConnectorFactory.create_and_start_sync`) bind
`connector_id` and `connector_type` (the instance's stored type once it is read, not the event's name). `org_id` comes from the
consumer's or the boot job's OrgContext. So every line of a run, the connector's own included, carries them.

| Event | Level | Fields |
| --- | --- | --- |
| `sync_run.start` | INFO | `sync_type` (`full`, `incremental`) |
| `sync_run.end` | INFO; WARNING for `partial` or a lost lease; a failure at its class's level | `sync_type`, `duration_ms`, `outcome`, `reason`, `full_sync_cut_short=true` when the next sync runs in full, the run's counts and `backoff_wait_ms` (below); on failure the `log_failure` fields |
| `sync_run.refused` | INFO | `reason` (the admission) |
| `sync_run.start_failed` | the failure's level | the `log_failure` fields |
| `sync_step.failed` | ERROR, WARNING for an optional step | `step` (`status_write`, `idle_write`, `drain`, `unlink`, …), the `log_failure` fields |

`outcome` on `sync_run.end`: `success`; `partial` (a full sync that finished but did not unlink what it did not see: the connector skipped part of the source, or the group-member write or the unlink failed);
`skipped` with `reason=<code>` (the connector declined, e.g. `DESKTOP_OFFLINE`); `failure` with `error_class=cancelled` and
`reason` = `requested`, `shutdown`, `cancelled` or `lease_lost` for a stop; `failure` with the exception's class otherwise. A
failed run's exception is logged once there; `SyncTaskManager` then logs it at DEBUG (`task.failed`).

Counts on `sync_run.end`, each left out when 0. Unlike `agent_turn.end`, which prints its zero counts, a sync line stays
short: a count that is absent is 0.
- `records_new`: not stored before. A placeholder (a stand-in for a parent not synced yet) is not counted; the real record
  that replaces it counts as new.
- `records_updated`: stored before, and its source revision changed, it came back from the trash, or it was renamed onto
  a new external id.
- `records_unchanged`: stored before with the same source revision and saved again, which a full sync does to every record.
  Its name, place or access may still have been refreshed.
- `records_deleted`: removed or sent to the trash, descendants included. A delete of something not stored counts nothing.
- `records_failed`: a batch of new or changed records that failed for good, a record the schema refused, or a delete the
  store refused. A single-record update that fails raises to the connector and is not counted.
- `records_skipped`, the sum of `skipped_owned_elsewhere` (its id belongs to another org or connector), `skipped_in_trash`
  (a user deleted it; the delete holds until the purge) and `skipped_not_content` (a system file such as `Thumbs.db`).
- `backoff_wait_ms`: how long this run's own calls kept the connector's `ResiliencePolicy` gate shut after a 429, a
  retryable 5xx or a network error, counted once however many calls waited behind it. Only clients built on
  `ResiliencePolicy` report it. A client that retries by itself (python-gitlab, the Google and Microsoft SDKs) does not, so
  no field there means "not measured", not "no wait". A backoff armed outside the run (a webhook or an API call on the same
  connector) is not counted, even when it held the run's calls.

A write counts once its transaction commits; deletes count after theirs. Limits: on a deadlock retry,
`retry_on_deadlock` re-runs the whole `on_new_records` call, so `records_unchanged` and `records_failed` can count again
records an earlier attempt stored or refused; likewise a connector that sends a failed batch again counts it failed, then
new. Work the run hands to `loop.run_in_executor` (which does not copy the context), or that a connector leaves running
after `run_sync` returns, is not counted. On Neo4j with `NEO4J_EXPLICIT_TRANSACTIONS=false` the single-record updates
(content, metadata, moves and permissions) commit each statement on their own, so one that fails part-way is not counted
although some of it stayed; batch writes still run in a real transaction. Records a connector writes to the graph without
the processor are not counted. A rename or move that the connector sends through `on_records_moved` rewrites the
stored record in place and is not counted either, unless the old record is missing and the move falls back to a plain add.

## Messaging consumers

The Redis Streams and Kafka consumers handle each delivery inside `message_log.delivery`
(`app/services/messaging/message_log.py`). It binds `topic` (the Kafka topic or Redis stream) and `message_id` (the Redis entry id,
or `<topic>-<partition>-<offset>` on Kafka); then the producer's request id, before the envelope is checked; then, once it is
verified, the envelope's org and user, `event_type`, and the payload's `record_id` and `connector_id`. Every line of the delivery
carries them: the handler's own, and the ack, retry or dead-letter decision. Before verification, and on a rejected envelope,
`org_id` is the org the producer claims, not a verified one. The indexing consumers bind the same for a record's whole run
(`message_log.bound_message`), the main-loop recovery and dead-letter path included; their end line is the indexing summary.

| Event | Level | Fields |
| --- | --- | --- |
| `message.end` | INFO for `success` and `skipped`; WARNING when the message will be retried; ERROR when it is dead-lettered (acknowledged or committed and dropped: nothing else records it); a retried `internal` or `misconfigured` failure is ERROR; a cancelled delivery INFO | `outcome`, `disposition` (`ack`, `retry`, `dead_letter`), `reason`, `code`, `attempt`, `duration_ms`, `size_bytes`; when an exception ended it, `error_class` and `error_type`, and `error_message` except for an unparseable message or a failure the handler already logged |

- Exactly one `message.end` per delivery, written after the broker call (XACK, commit, seek) that carries out the decision. A
  delivery that ends any other way (an error in the ack, a stop mid-handler) is `failure` with `disposition=retry`: the entry stays
  pending, the offset is read again. A redelivery is a new delivery with its own line. One exception: a stop in the short
  window after the XACK or commit, while the retry count is cleared, also says `retry` for a message already settled.
- `attempt` is the number of this delivery: 1 for a new message; after failures, the failure count with a retry tracker, or Redis's
  delivery count (`times_delivered`) without one.
- Kafka with no retry tracker commits a failed message: `disposition=dead_letter`. Redis with none leaves it pending: `retry`.
- `reason`: `duplicate` (the envelope was already handled), `already_settled` (Kafka: the offset was settled before), `no_value`,
  `handler_false` (the handler asked for the message again, e.g. a user whose org does not exist yet), `exception`, `unparseable` (not JSON, not an envelope, or not a valid
  message), `envelope_rejected` with `code` (`bad_signature`, `stale`, `malformed`, `org_mismatch`, `no_key`, …),
  `attempts_exhausted` (dead-lettered before running again), `no_handler`, `cancelled`. A rejected envelope is ERROR with no stack:
  most codes are our own configuration (a key rotation, a backlog older than the age limit), and each drops every message on the topic.
- A handler logs its own failure once (`entity_event.failed`, `entity_event.step_failed` with `step`, `message.handler_failed`,
  `ai_config.refresh_failed`) and raises; `message.end` does not repeat its text. A handler returns true when done or when the
  message is not for it (skipped at DEBUG), false only to ask for it again, and raises on failure, so the consumer's classifier
  decides: a terminal error (4xx, validation, `NonRetryableError` for a malformed message) is dead-lettered on its first delivery,
  a transient one is retried. A user event's failure carries
  `target_user_id`, the user the event is about (`user_id` is who sent it). `target_user_id` is not passed through the provider's
  `log_identity`.
- The message value and its payload are never logged whole. Lines carry only the ids taken from it (`record_id`, `connector_id`,
  `target_user_id`). A message that cannot be parsed logs its `size_bytes`; a validation error's text is left out (it quotes the
  payload's values). A handler's other exception text is logged as raised, so an error that quotes a payload value carries it.
- Work a handler starts in its own context (the sync task manager) keeps the message's ids; `message.end` is written when the
  delivery ends, not when that work does. The connector link passes started by `userAdded` run as an org job instead
  (`ctx_kind=job ctx_source=job:connector_link`), with only `connector_id`.

## Agent loop events (Python)

One chat turn is reconstructable from these lines; each carries the bound `org_id`, `user_id`, `conversation_id`,
`agent_id` and (inside the run) `run_id`.

| Event | Level | Fields |
| --- | --- | --- |
| `agent_turn.end` | INFO; WARNING for `max_turns` and for an error no other line reported; `level_for(error_class)` for a pre-loop rejection | `stop_reason`, `end_event`, `turns`, `tool_calls`, `tool_errors`, `llm_calls`, `tokens_in`, `tokens_out`, `cache_read_tokens`, `duration_ms`, `model`, `mode`, `spans` (when `PIPESHUB_CHAT_TIMING` is on, the default), `error_class`, `code` |
| `agent_turn.failed` | `log_failure` policy | a turn that crashed (before `agent_turn.end`, which is then INFO) |
| `llm_call.end` | INFO; DEBUG for a failed attempt. `llm_call.retry` or `llm_call.failed` reports a failed attempt; for a client disconnect, `agent_turn.end` with `stop_reason=client_disconnect` reports it. | `source`, `provider`, `model`, `duration_ms`, `tokens_in`, `tokens_out`, `cache_read_tokens`, `finish_reason`, `tools_offered`, `tool_calls`, `attempt`, `outcome`; on failure `error_class`, `error_type`, `status` |
| `llm_call.retry` | WARNING | `attempt`, `max_attempts`, `status`, `error_class`, `delay_ms` |
| `llm_call.failed` | `log_failure` policy | `turn`, `error_class`, `error_type`, `error_message` |

- `stop_reason`: `done`, `awaiting_input` (a clarifying question or an approval card), `max_turns`, `guardrail`, `cancelled`
  (the user pressed Stop), `client_disconnect` (the Node to Python connection dropped; Node keeps a run going when the browser
  leaves, so a closed tab ends `done`), `error`. `end_event` is the agent library's exit (`agent_complete`,
  `agent_complete_partial`, `agent_complete_after_max_turns`, `agent_failed`, `llm_call_failed`, `guardrail_blocked`,
  `turn_blocked`, `agent_blocked`, `agent_cancelled`, `context_budget_exceeded`, `task_complete`). `code` is the error code the
  client received.
- `llm_calls` and the token totals sum the request's usage ledger: every model call through the request's transports plus tool-made
  calls that report usage (grep generation, entity linking). Failed calls report no usage, so `llm_calls` need not equal the number
  of `llm_call.end` lines.
- A provider 401/403 on a model call is `misconfigured` (the deployment's key was refused), not `auth_expired`.
- `duration_ms` on a bridge's `agent_turn.end` counts from the bridge start; on a route exit (agent route) it counts from the request.

## Tool calls and MCP (Python)

`ToolExecutor.call_tool` binds `tool`, `tool_call_id` and the tool's `log_ids` (`mcp_server_id` or `toolset_id`) for the whole
call, so every line logged while a tool runs (approval gate, action, MCP client) carries them. A sub-agent the call starts
(`agent_tool`, `handoff`, `best_of_n`, a detached spawn) inherits them; the children of a `spawn_agent` batch, launched before
the calls are dispatched, bind their own call's `tool` and `tool_call_id`.

| Event | Level | Fields |
| --- | --- | --- |
| `tool_call.end` | success and skipped INFO; failure at `level_for(error_class)`, WARNING when unclassified; parked for approval DEBUG | `kind` (builtin, mcp, openapi, sub_agent), `outcome`, `deny_reason` (on `reason=denied`: rule, default_rule, unattended, rules_unavailable, approval_busy, approval_not_saved, consecutive_failures, middleware_error), `reason` (exception, tool_error, bad_args, malformed_args, unknown_tool, denied, declined, expired, unavailable, duplicate, awaiting_approval, blocked, server_error, stopped, cancelled_before_execution), `error_class`, `error_type`, `status`, `mcp_reason`, `duration_ms` (the run, without an approval wait) |
| `tool.blocked` | WARNING, once per streak | `tool_path`, `consecutive_failures` |
| `mcp.connect.end` | success DEBUG, failure INFO | `mcp_server_id`, `transport`, `mode`, `protocol_version`, `outcome`, `error_class`, `mcp_reason`, `status`, `error_type`, `cause_type` (the innermost cause's type), `duration_ms` |
| `mcp.retry` | WARNING; `reason=renegotiate` INFO | `mcp_server_id`, `op` (connect, list, call), `reason` (token_refresh, resend, relist, renegotiate), `error_type` |
| `mcp.discover.end` | a failure at its class's level, INFO for a sign-in failure with `source=listing`; skipped (`reason=no_attached_tools`, `listing_deadline`) WARNING | `mcp_server_id`, `outcome`, `error_class`, `mcp_reason`, `error_type`, `source` (turn, listing), `reason` (when skipped), `duration_ms` |
| `mcp.result.schema_mismatch` | WARNING (the result is kept) | `error_type` |
| `mcp.redirect_refused` | WARNING (the call fails with a blocked-URL error) | `mcp_server_id`, `target_host` |
| `mcp.session.ended` | WARNING, when a closed connection had a recorded cause | `mcp_server_id`, `error_type` |
| `mcp.load.end` | INFO, once per turn | `servers`, `servers_loaded`, `tools_listed`, `failed` |
| `openapi.retry` | WARNING | `status`, `reason` |
| `tool_approval.run_failed` | DEBUG in practice: the executor already logged it | `error_class`, `error_type` |
| `mcp.route.failed`, `toolset.route.failed` | the failure's class's level | `op` (the step), `error_class`, `error_type`, `error_message` (our own errors only; not for a provider or factory call), the bound ids |

- **Routes** (`api/routes/mcp_servers.py`, `toolsets.py`, `api_toolsets.py`): a request's failure is on its `http.request.end`, with
  `mcp_server_id`, `toolset_id` or `agent_id`, `reason`, and no server or provider text. `*.route.failed` is only for a step the route
  swallows and goes on without (counting a toolset's users, cancelling a refresh task). An MCP listing writes one
  `mcp.discover.end source=listing` per server it couldn't list (INFO when it only needs a sign-in); `source=turn` is a chat
  turn's. `toolset.route.failed op=load_instances` also appears on agent chat requests (`get_authenticated_toolsets`). An OAuth
  callback's failure is on the line with `outcome=failure` and the body's `error` code as `reason`; a provider's `error=` is
  `reason=provider_error` with `oauth_error` (a code RFC 6749 or OpenID Connect defines, else `other`) and is never ERROR.

- Every tool call has exactly one `tool_call.end` at INFO or above, whatever the exit: run, raised, denied, blocked, malformed,
  duplicate, skipped by Stop before it ran, or a parked call once its card is settled. A card never answered leaves only the DEBUG line.
  A call the executor never reached has none: when the turn's event stream or checkpoint store fails first, the turn fails and
  `agent_turn.failed` reports it. The success line at
  INFO is an agreed exception to "no more INFO": it is the one line per unit of work that says which tool ran, for whom, how long.
- A tool hands a failure to the executor as `ToolOutput(cause=exc, end_fields={...})` or raises; it never logs and returns.
  The executor logs it once (`log_failure`, so the stack prints only for `internal` and `misconfigured`). Calls that fail with one
  shared exception (a shared MCP listing, a memoised lookup) each keep their line at their own level, and the stack prints once:
  when `error_class.logged_level(exc)` says it was already written at that level, the line is a `log_event` with the error fields.
- Tool, MCP, OpenAPI and OAuth failures never log the error's text (`include_error_text=False`): a server's answer can quote the
  arguments. A refused redirect logs only the target's host, and an ended MCP session only the cause's type. Nor a STDIO server's stderr: it can echo earlier calls' arguments and its environment. What the model sees (`ToolOutput.error`, an `isError` result, an HTTP body) is never a field.
- `mcp.connect.end` (like `llm_call.end`) is an operation record: it stays at INFO when it failed, and the layer that retries or
  gives up (`mcp.retry`, `mcp.discover.end`, `tool_call.end`) logs the severity.
- `app.agents.mcp.failure.mcp_error_class` maps an MCP failure: `classify_error` first, then what its `MCPFailureReason` means; a
  server's JSON-RPC error is `bad_request` (invalid request, params or method) or `upstream_5xx`.

## Agent wiring (Python)

Approvals, sub-agents and the build of each turn's agent. Every line carries the bound `org_id`, `user_id`, `conversation_id`,
`agent_id` and `run_id` (the turn's); a sub-agent's lines also carry `agent_run_id` (its own run) and `parent_run_id` (the run
that spawned it: the turn's `run_id` for a child of the root, the parent's `agent_run_id` deeper down). So `run_id=X` finds the
whole spawn tree, `agent_run_id=C` one child, and `parent_run_id` rebuilds the tree.

| Event | Level | Fields |
| --- | --- | --- |
| `agent.build.end` | INFO, once per turn (`PipesHubAgentFactory.create`) | `mode`, `loop`, `tools`, `tools_registered`, `toolsets_failed`, `tool_disclosure`, `toolsets_grouped`, `toolsets_pinned`, `domain_agents`, `code_execution` (enabled, disabled, unavailable), `sandbox_network`, `has_knowledge`, `skills`, `duration_ms`: counts and enums, never names |
| `code_execution.unavailable` | WARNING, once per process | `error_type` (the deployment's `SANDBOX_MODE` can't be used) |
| `toolset.load_failed` | `log_failure`, no error text; `not_authenticated` is `auth_expired` (WARNING) | `toolset`, `outcome`, `reason` (not_authenticated, error) |
| `toolset.group_failed`, `tool.build_failed` | `log_failure` | `toolset` / `tool` |
| `toolset.client.retry` | WARNING | `toolset`, `attempt`, `max_attempts`, `error_type`, `delay_ms` |
| `intent.fallback` | INFO when the run's own layer already logged the failure (`end_event` in `turn_log.LOGGED_BY_THE_LAYER`); else WARNING | `reason` (run_failed, empty, error), `end_event` |
| `tool_approval.requested` | INFO; a card that couldn't be saved WARNING | `approval_id`, `source` (mcp, toolset), `kind`, `personal`, `outcome`, `reason=not_saved` |
| `tool_approval.answered` | INFO, exactly once per answer; `reason=other_user` WARNING (`error_class=forbidden`); a claim that failed is `log_failure` | `approval_id`, `decision` (approved, declined, expired, already_answered, refused, unavailable, claim_failed, ignored), `reason` (other_user, other_conversation, not_found, in_progress, tool_gone, stopped, not_resumable), `remember`, `remembered` (saved, not_allowed, block_kept, failed), `ran`, `status` (completed, failed, blocked), `tool`, `tool_call_id`, `mcp_server_id` or `toolset_id` |
| `tool_approval.rules_unavailable`, `tool_approval.rule_not_saved`, `tool_approval.outcome_not_kept` | `log_failure` when the store raised; WARNING `error_class=unavailable` when it said no | `source` / `rule`, `step` / `approval_id` |
| `sub_agent.end` | INFO, once per child (`AgentRuntime.run_child`) | `agent_run_id`, `parent_run_id`, `role`, `spawn_depth`, `team_id`, `outcome` (success, partial, failure), `end_event`, `error_class`, `error_type` (raised), `turns`, `duration_ms` |
| `sub_agent.detached_failed`, `sub_agent.failed` | `log_failure`, no error text (a failed prerequisite's quotes the child's error) | a detached child that couldn't run; a best_of_n candidate that raised |
| `middleware.failed` | `log_failure`, no error text | `middleware`, `context` (the hook context's type); a fail-closed pipeline also denies with `deny_reason=middleware_error` |
| `orchestrator.dispatch.end` | INFO; `orchestrator.dispatch.failed` `log_failure` (falls back to LLM dispatch) | `steps`, `outcome` |
| `turn_scope.check_failed`, `prefetch.failed` (no error text), `agent_turn.cleanup_failed` (`step`) | `log_failure` | the turn carries on |

- An approval answer is logged once whatever happens next: on the decided path the line is written first in `_settle`'s `finally`,
  before the outcome is kept and before the card's row is shown, so Stop or a client disconnect while the approved call runs is
  `decision=approved ran=true status=failed reason=stopped`. Join the asking turn and the answer by `approval_id`; the call's own
  line is `tool_call.end` (`tool_call_id`). The call's arguments are never logged.
- A spawn the library refuses is the spawn call's `tool_call.end` at WARNING, no stack: `reason=max_spawn_depth`, or
  `reason=dependency_failed` for a failed prerequisite and for the batch's validation errors (duplicate task_id, self-dependency,
  unknown dependency, unresolved tools, cycle), both `error_class=bad_request`.
- A domain agent (`AgentTool`) whose child failed shows as `tool_call.end reason=sub_agent_failed` with the child's class; a
  `spawn_agent` call returns its child's result as content, so its `tool_call.end` is a success and the child's failure is on `sub_agent.end`.
- A long-lived task first started inside a child keeps that child's `agent_run_id` (for example an MCP session's reader that a
  child's call opened) (inferred). Programmatic dispatch binds its synthetic call ids (`programmatic_spawn_<step>_<hex>`) as the
  children's `tool_call_id`; they have no `tool_call.end`.

## Credential refresh

The connector, toolset and MCP token refresh services log through `app/utils/credential_refresh_log.py`, so one
refresh of one credential reads the same everywhere:

| Event | Level | Fields |
| --- | --- | --- |
| `credential_refresh.scheduled` | INFO when the refresh time is new, DEBUG when a scan re-arms the same one | `next_refresh_at` (UTC), `delay_s` |
| `credential_refresh.start` | DEBUG | |
| `credential_refresh.end` | INFO on success; a failure at its class's level | `outcome`, `reason` (`adopted`, `superseded`, `invalid_grant`, `config_missing`, `save_failed`, `recursion`; MCP also `no_credential`, `invalid_token_record`, `no_refresh_token`, `no_token_url`, `credential_removed`, `lock_contention`, `url_blocked`, `host_unresolved`), `duration_ms`, and on failure `error_class`, `error_type`, `error_message`, `http_status`, `oauth_error`. Toolsets add `oauth_app` (`linked` to an admin OAuth app, or `unlinked`: a fact, not a warning; it is normal for a personal instance) |
| `credential_refresh.check_failed` | the failure's level; DEBUG when the attempt's `end` already logged it | as a failed `end` |
| `credential_refresh.strike` | INFO | `strike` (`2/3`), `counted` (false: the rejected token was already rotated), the cause's `error_class` and `oauth_error` |
| `credential_refresh.deauthenticated` | WARNING | `reason`, `strikes`, `app_disabled_event` (connectors: `sent`, `direct_write`), `path` (MCP: `background` after 3 rejections, `route` after one) |
| `credential_refresh.not_scheduled`, `.deauthenticate_failed`, `.app_disabled_event_failed` | WARNING, ERROR, by class | `reason` / failure fields; `deauthenticate_failed` has `reason=not_updated` when the write changed nothing, `not_found` when the record is gone |
| `credential_refresh.skipped` | WARNING | `outcome=skipped`, `reason` (`lock_wait_timeout`: another refresh of the same credential holds its lock) |

Every line in a refresh carries `refresh_kind` (`connector`, `toolset`, `mcp`), `trigger` (`scan`, `scheduled`, `on_demand`), the
kind's ids (connectors: `connector_id`, `connector_type`; toolsets: `toolset_id` and `owner_id`, the user id or agent key the
sign-in belongs to; MCP: `mcp_server_id` and `owner_id`), `provider` (the connector or toolset type; MCP lines have no
`provider`), and the org as a job context when nothing else bound one. A timer
firing gets its own `sys-credential_refresh-` root, with `scheduled_by` naming the root that armed it. Exactly one `end` is
written per attempt, by the code that calls the token endpoint. A misconfigured credential (no OAuth client, the provider
rejected our client) is ERROR with a stack once per credential per hour and DEBUG in between. `trigger` is set by the refresh
scope and never inherited from a sync's own `trigger`. The toolset service has no on-demand refresh: its lines say `trigger=scan`
or `scheduled`, and `on_demand` only on the `scheduled` line written when a sign-in completes.

Every MCP refresh, whoever starts it (the background service, the refresh route, tool listing or discovery, an agent's 401), goes
through `app/agents/mcp/token_refresh.py`, which writes its `end`; `duration_ms` includes waiting for another process's refresh lock.
The tool call and discovery lines of the same failure carry the same `error_class` and keep their own level. Per-turn MCP lines
classed `misconfigured` share one hourly ERROR per server and class (`tool_call.end`, `mcp.discover.end` turn and listing); the
others in that hour are DEBUG. A token host that does not resolve is `host_unresolved` (`unavailable`, WARNING); only a URL the
guard refuses by policy is `url_blocked` (`misconfigured`).

Nothing a token endpoint answered is logged as text: its body, and the token URL and redirect URI in the exception message, can
quote the refresh token in prose no redactor recognises. When the exception chain holds such an answer, every failure line of
a refresh service (`log_refresh_failure`) leaves the text out of `error_message` and the stack (each exception shows as
`Type: <message omitted>`) and logs `http_status` and `oauth_error` instead.

## Database queries (Python)

The ArangoDB and Neo4j clients (`arango_http_client.py`, `neo4j_client.py`) and the Postgres runtime (`app/core/db/uow.py`,
which every Postgres graph provider runs through) write one record per call that failed or was slow, through
`app/utils/db_query_log.py`. A call that succeeds in time writes nothing.

| Event | Level | Fields |
| --- | --- | --- |
| `db.query.failed` | `level_for(error_class)`: WARNING, except `internal` and `misconfigured` (ERROR) | `backend` (`arango`, `neo4j`, `postgres`), `op` (the client method, or `fetch`/`execute`/... and `begin`, `prologue`, `check_alive`, `commit`, `copy`, `rollback_to` on Postgres), `site` (Postgres `/* pipeshub:<site> */` tag), `caller`, `code` (ArangoDB errorNum, Neo4j code, SQLSTATE), `status` (ArangoDB HTTP status), `error_class`, `error_type`, `duration_ms`, `outcome`; Neo4j memory limit adds `bulk`, `size`; Postgres adds `db_request_id` |
| `db.query.slow` | WARNING | `backend`, `op`, `site`, `caller`, `duration_ms`, `threshold_ms`; Postgres `db_request_id` |
| `db.query.retry` | WARNING | `backend=neo4j`, `op`, `reason=connection_lost`, `error_type` |
| `db.connect.failed`, `db.disconnect.failed`, `db.schema.failed`, `db.edge.failed` | `log_failure` | `backend`, `op` (schema or edge) |

- **One record per call, outside the client's own retries.** A write conflict the ArangoDB or Neo4j client retries and wins
  writes nothing; one it gives up on writes one record. Retries above the client (`KernelDB.run`, the KB push, a provider
  transaction its owner re-runs) write one record per lost attempt. `duration_ms` is the whole client call, including the
  client's own back-off and every cursor page; a Postgres record is one statement.
- **An operation record that uses its class's level.** Unlike `mcp.connect.end` and `llm_call.end`, which stay below ERROR and
  leave the severity to the layer that gives up, `db.query.failed` is ERROR for our own bug (an AQL, Cypher or SQL error) or our
  configuration: many provider methods still swallow database errors, and the record can be the only ERROR line. It does not mark
  the exception as logged, so the handler's `log_failure` keeps its level and prints the one stack.
- **No driver text, ever.** Driver messages can quote bound values (a Neo4j constraint names the clashing value; a Postgres data
  error or asyncpg's encoding error quotes the input). The record carries `code` and `error_class` instead, and the client stamps
  the exception (`error_class.mark_error(exc, error_class, hide_text=True)`; without a class, `mark_error(exc, hide_text=True)` only
  hides the text and leaves the class to `classify_error`), so `classify_error` returns its class and `log_failure` leaves `error_message` out and
  prints the stack as `Type: <message omitted>` for any chain, or exception group member, that holds it. This also hides the
  server's own text for our bugs (an ArangoDB 1501 "syntax error … near …" reads `code=1501 caller=…`): diagnose from `code`,
  `caller` and `site`, and never add a database error's `{e}` or `str(e)` back to a log line. Our own texts stay visible (the
  Neo4j memory-limit hint).
- **Classes.** Only our bugs and our configuration are ERROR classes. Conflicts and lost races (ArangoDB 1200, 1210, 29, 1654,
  1655; Neo4j constraint, deadlock, entity lost to a delete; Postgres 23xxx, 40001, 40P01) are `conflict`; ArangoDB 18 and 1500,
  Neo4j transaction timeouts and Postgres 57014/55P03 are `timeout`; Postgres 22xxx, 54xxx, 23502 and 23514, and Neo4j data
  exceptions (GQL status 22xxx), are `bad_request` (a value or a size a caller handles), even when our own parameter caused them.
- **`caller` and `[file:line]`** are the first frame outside the client: the provider line that called it. In a Postgres provider
  transaction the statement runs in another task, so `_Txn.run` finds the caller while its stack is live and hands it over; the
  unit's own `begin` and `prologue` there have `caller=unknown`.

## Auth audit events (Node)

Every security decision of sign-in, lockout, OTP, password reset and change, SSO, JIT provisioning, sign-out and the sign-in
policy is one INFO line on the `audit` logger (`libs/services/audit.ts`), message = the event. Fields: `org_id`, `actor` (the
account the decision is about; null for an unknown account), `target` (an admin action's account), `outcome`, `reason`,
`auth_method` (an `AuthMethodType` value; anything else is `other`). Never an email, name, IP, password, code or token: the
access line has `client_ip` and the same `request_id`.

| Event | Outcome | `reason` on failure | `auth_method` |
| --- | --- | --- | --- |
| `auth.sign_in` | `success`, `partial` (a step passed, more required), `failure` | `unknown_user`, `bad_password`, `no_password`, `bad_otp`, `no_otp`, `otp_expired`, `otp_already_used`, `locked`, `account_disabled`, `service_account`, `account_changed`, `method_not_allowed`, `captcha_failed`, `no_session`, `session_expired`, `sso_token_invalid`, `sso_token_expired`, `sso_wrong_tenant`, `sso_no_email`, `sso_email_mismatch`, `no_account`, `account_removed` | yes |
| `auth.account.lock` | `success`, `reason=too_many_attempts` | | `password`, `otp` |
| `auth.account.unlock` | `success`, `reason=block_expired` or `admin`; `target` = the account | | |
| `auth.otp.send` | `success`, `failure` | `unknown_user`, `locked` | `otp` |
| `auth.password_reset.request` | `success`, `failure` | `unknown_user`, `captcha_failed` | |
| `auth.password_setup.request` | `success` | | |
| `auth.password_reset.complete` | `success`, `failure` | `no_token`, `token_invalid`, `token_expired`, `link_used`, `locked`, `unknown_user` | |
| `auth.password.change` | `success`, `failure` | `bad_current_password`, `captcha_failed`, `locked` | |
| `auth.account.setup` | `success` | | `password` |
| `auth.email.change` | `success` (`reason=link` or `sso_mail_claim`), `failure` | `no_token`, `token_invalid`, `token_expired`, `link_used`, `email_taken` | |
| `auth.sign_out` | `success` | | |
| `auth.session.refresh` | `failure` only | `no_token`, `token_invalid`, `token_expired`, `session_ended`, `account_inactive`, `no_credentials`, `locked` | |
| `auth.policy.update` | `success`, `failure` | `not_admin` | |
| `auth.jit_provision` | `success` | | `google`, `microsoft` |

How a refusal gets its line: the code that refuses throws its error tagged with `refusal(error, reason)` (`authRefusal` in
`modules/auth/utils/sign-in-audit.ts` checks the code). The tag is invisible to the client and to the logs. The handler, or the
refusing middleware (`authSessionMiddleware`; `scopedTokenValidator` for the refresh, password-reset and email-validation
scopes only), calls `auditRefusal` once in its catch; it writes a line only for a reason its event may carry
(`AUTH_AUDIT_REASONS`). An untagged error is a system failure: no audit line, and the error middleware logs it once.
Not audited: body validation 400s, rate limits, regular-JWT failures on authenticated routes, and service-to-service scoped tokens.
Each still has its `http.request.error` (or the limiter's) line.

## Upstream calls (Node)

Every call through a service command or an instrumented axios instance writes one `upstream.call.end` line **per attempt**,
from `backend/nodejs/apps/src/libs/http/upstream-call.ts`: the service commands in `libs/commands` (through `upstreamFetch`)
and every call on the default axios instance (instrumented in `index.ts`) or the knowledge base's own instance. A retry,
`fetchWithRetry`'s or axios-retry's, is a new line. Not covered, so they write no line: the raw `fetch` to a signed upload URL
(`knowledge_base/utils/utils.ts`, `uploadFileToSignedUrl`), the S3 health check's signed GET
(`storage/utils/s3-health-check.util.ts`), the Slack events proxy in `app.ts` (an `http.request` to the local Slack bot that
passes the client's headers, its `x-request-id` included, on as they came), and the cloud SDKs (AWS, Azure, Google), which use
their own HTTP clients.

| Field | Value |
| --- | --- |
| `upstream` | `query`, `connector`, `indexing`, `nodejs` (Node's own services over HTTP), or `external`. Named from the URLs `loadAppConfig` reads; a registered base matches by exact origin and whole path segments. Not `service`: that key names the logger. |
| `method`, `status` | `status` only when an answer came |
| `route` | a PipesHub service: the path with every id-like segment (ids, numbers, encoded text, emails, file names, anything upper-case) as `:id`. Anything else: the host only. |
| `duration_ms` | `fetch` (commands, `upstreamFetch`): the attempt's start to the answer's headers. axios: to the adapter's answer, which for a non-stream response is after the body is read, so it includes the transfer. A chat stream (`AIServiceCommand.executeStream`): to the stream's end. Any other stream, axios `responseType: 'stream'` included, ends at its headers; a failure midway there is reported by the request's own lines. |
| `attempt` | 1-based |
| `outcome`, `error_class` | `success`, or `failure` with the class (`classifyError`): an answer by its status, no answer by the error |
| `network_code` | no answer only: the first string `code` in the error's cause chain, or axios's own (`ECONNREFUSED`, `ENOTFOUND`, `UND_ERR_SOCKET`, `ECONNABORTED`) |

- **Levels.** Success is DEBUG: the access line is the request's INFO line, and the service's own end line has the same
  request id. A failure takes `levelFor(error_class)`: WARNING for an upstream 4xx, 5xx, timeout or outage, INFO when we
  cancelled it, ERROR when the request could not be made (our bug). A path with a `health` segment (`/health`,
  `/health/graph-db`) is a probe and DEBUG whatever its outcome, for internal and external targets alike. That includes a
  path word a user chose: a skill named `health` makes its calls' lines DEBUG (its request's own lines still report a failure). Unlike
  `llm_call.end`, a failed attempt is not lowered to DEBUG: the axios adapter cannot know whether axios-retry will try again,
  so both transports report each attempt at its class's level.
- **What it never has:** the URL, the query string, headers, bodies, or the error's message. The line is a fact about the
  call, not the error report: the request's `http.request.error` still logs the failure once. `handleBackendError` logs
  nothing; a 5xx it maps carries `{ status }` as its cause, so an upstream 500 is `upstream_5xx` (WARNING) on the request's
  lines too, and a 4xx it passes on keeps the service's words in its message. A 5xx's own detail stays in the service's log.
  About 30 controller catch blocks still log `error.response?.data` themselves; they are being removed module by module.
- **Request id.** A call to a PipesHub service always carries the request's sanitised root id, replacing an `x-request-id`
  copied from the client (`injectRequestHeaders`, and the axios adapter). The gateway never adds one for a third party.
- **Streams.** The chat stream's line is written when the body ends (DEBUG), fails midway (`unavailable`, with the 200), or
  is aborted by us (`cancelled`, INFO). A consumer that destroys the stream without aborting the call does not end it early:
  the line is written when the service ends the body.
- **Retries.** An `AIServiceCommand`'s `timeoutMs` is one budget across its attempts and backoff waits: once it, or the
  caller's signal, is spent, no further attempt starts, so a timed-out call writes one `timeout` line.
- **Known gaps.** A caller that expects a 404 (an existence check) still gets a WARNING `not_found` line: the transport
  cannot know what the caller expects.
- **New calls** use a command or axios, so they get the line; do not log the call again at the call site. A new raw `fetch`
  goes through `upstreamFetch`, and adds `injectRequestHeaders` only when the target is a PipesHub service: it does not
  check the destination, and the request id and MCP relay header must not reach a third party (the axios adapter adds them
  only for registered internal targets).

## Redaction

Both formats redact: the whole text line (message, fields, traceback), and in JSON every string value. Credentials (bearer and
basic tokens, JWTs, known key shapes, URL passwords and query secrets, private keys, this process's secret env values, and
`name=value` pairs whose name is a credential) are replaced by `[REDACTED]`. In text (the message, a traceback, the text format's
field list, and any JSON string value that holds such pairs), a pair value that only describes the credential is kept: a bare
`None`, `null`, `true`, `false`, `undefined`, `present` or `missing`; a time under a time name (`token_expires_at=1730000000`,
`refresh_token_expires_in: 7776000`, also with a fraction, but not a negative number such as `token_ttl=-1` and not
`updated_password=…`); and an `http(s)` URL with no query, fragment or userinfo under `token_url`, `token_uri`, `token_endpoint`,
`authorization_url` or `authorization_endpoint`. A field whose **name** is a credential (`token`, `apiKey`, …) is redacted in both
formats when its value is a string or an object, so a URL or an ISO time passed as a string field stays masked; the rules above
apply to `name=value` pairs inside text, not to a field's own string value. Booleans are always kept, and `None` is `null` in JSON
and left out in text. A number (a Python `int` or `float`) under a credential name follows the pair rule in both formats: it is
kept when it is a non-negative integer of up to 6 digits (`token_count=12`) or a non-negative number in plain decimal form under a
time name (`token_expires_at=1730000000`, `token_expires_in=3600.5`); any other number under a credential name
(`password=73915528`, `token_count=12345678`, `token_ratio=0.5`, `token_ttl=-1`, `1e+16`, NaN) is `"[REDACTED]"` in text and JSON
alike. Other numeric types (`Decimal`, numpy scalars) take the string path and are masked under a credential name. Redaction is a
safety net; it cannot recognise content, so the rules below still apply.

## Personal data

Log lines never carry an email or a person's name (full, display, first or last name, username, login), and never a whole user
object or list. Org and user ids are opaque and may be logged. Never mask (`a***@x.com`), truncate, keep only the domain, or hash an
email: it is still personal data, and an id says more.

Which id replaces it:

- **`target_user_id`**: the PipesHub user id (the users `_id`; `userId` on a graph user) of the user a step acts on, and only
  where the code holds that id: Node `user._id` or `req.user.userId`, Python graph `User.user_id`, or a connector's `created_by`.
  Never `AppUser.id` (a fresh uuid each sync run) or a graph key, which support cannot match. `user_id` stays the caller's id, set
  from the bound context.
- **`source_user_id`**: the provider's id of an external account, and only where that id is opaque: a Google directory id, a
  Microsoft Graph object id, an Atlassian Cloud `accountId`, a Box, Dropbox (`dbmid:`/`dbid:`), Salesforce, GitLab, GitHub (the
  numeric id, not the login), Linear, Notion, Zammad or BookStack id. Some connectors' ids can be an email or a login, so they never
  log one: Gmail individual (`source_user_id` is the address), Nextcloud (the uid is a login), Jira and Confluence Data Center (the
  key can be the username), a Dropbox invitee's `Permission.external_id`, connector core (it can't tell which connector's id it
  has), and every connector that fills `source_user_id` from `user.source_user_id or user.id or user.email`.
- **Neither**: drop the value. Where a count helps, use a counter the code already keeps or the sync run's stats, and keep the
  summary at the old line's level.

In a per-user loop, bind the id once inside the per-user coroutine: `with log_fields(source_user_id=user.source_user_id):` at the
top of the function that `gather` runs. `gather` runs each coroutine in a task that copies the context at the `gather` call, so a
binding made around `asyncio.gather(...)` reaches every coroutine, and all users' lines would carry the same id: bind only fields
they share there (`connector_id`). A binding made while the coroutines are only being created, and ended before `gather`, reaches
none of them. A line about one user's result logged after `gather` passes the id with `extra=`.

If an exception's text can quote an email (a driver error, `Email already in use: <address>`), keep its class only: in Python
pass `include_error_text=False` to `log_failure` (it logs the text by default, and the redactor masks secrets, not emails); in
Node, drop the address from the thrown message.

**Finding a user's lines (support).** Get the PipesHub id from the admin console (Users, open the user: the profile URL has
`userId=<id>`) or `GET /api/v1/users?search=<email>` (`_id`), then filter on `user_id=<id>` (requests made by that user) or
`target_user_id=<id>` (sign-in, user management and connector-creator lines). A connector's per-user lines carry
`source_user_id`: find that id for the email in the provider's admin console (Google Admin user id, Entra object id, Atlassian
account id, Box or Salesforce user id). For a connector with no opaque id (the list above), per-user lines carry only
`connector_id` and counts: reproduce with a one-user sync, or read the run's `sync_run.end` stats.

**CI.** `.github/scripts/new_lint_findings.py log-pii` (scanner `scripts/log_pii.py`, which reads TypeScript through
`scripts/log_pii_ts.cjs`) fails a pull request that adds an email or a person's name to a log call under `backend/python/app` or
`backend/nodejs/apps/src`, in a new call or in one that already logs one (each value counts): `user.email`, `user_email`, `payload["email"]`, `m.get("emailAddress")`,
`extra={"email": …}`, `{ email }`, `member.name`, `full_name` of a user, `users`. Like the emoji check it counts per file, so
ones already on main do not fail and moving one to another file counts as new. Names of connectors, files, tools, groups and
repos are not findings (`group.email` either), and neither are ids, counts, flags or settings about email (`email_count`,
`bool(user.email)`, `email_format`, `mail_server`), nor a bare `name` or `user` in Python, nor a field named `user` (`{ user: userId }`). It reads names, not values, so an email stored in an id-named field (`source_user_id`, `external_id`) or copied to another
variable is not caught: the rules above still apply, and tests use `tests/support/log_capture.py` (`assert_not_logged`,
`assert_not_logged_mock`) or Node's `assertNotLogged` (`tests/helpers/mock-logger.ts`) with the email seeded into id fields too.
A real false positive is better renamed (`repo_full_name`, `entry` for a tar member); otherwise mark the line of the flagged value
with `# log-pii: ok <reason>` (`// log-pii: ok <reason>` in TypeScript). The mark covers only the values on its line, must be a
comment and must give a reason, and each new one is shown to reviewers as a `LOG-PII-OK` warning. Run the scanner's tests locally
with `TS_MODULE=backend/nodejs/apps/node_modules/typescript python3 -m unittest discover -s scripts -p 'test_*.py'`.

## Conventions

- **Bind ids once** where work enters: an HTTP request, a broker message, a scheduled tick. Never thread ids through call sites.
- **Message = static text or an event name**; ids and values go in fields, not into the message string:
  `logger.info("sync_run.end", extra={"connector_id": cid, "duration_ms": ms})`.
- **Event names** are dotted lowercase: `sync_run.end`, `tool_call.end`, `http.request.end`.
- **Field names** are snake_case and the same in Python and Node: `org_id`, `user_id`, `request_id`, `conversation_id`, `agent_id`,
  `run_id`, `parent_run_id`, `attempt`, `connector_id`, `connector_type`, `record_id`, `kb_id`, `tool`, `tool_call_id`,
  `mcp_server_id`, `toolset_id`, `job`, `workload`, `target_user_id`, `source_user_id`, `duration_ms`, `status`, `error_class`,
  `error_type`, `outcome`, `method`, `route`, `client_ip`, `reason`, `topic`, `message_id`, `event_type`, `disposition`. Python
  also has `ctx_kind`, `ctx_source`, `error_message` and `cause_type`; Node's `error` is an object (`error.message`). Node also
  has `upstream` and `network_code` (on `upstream.call.end`).
- **Fields are scalars**: ids, sizes, counts, enums. Never pass a config, a request, a response or a document as a field.
- **Log an error once**, at the layer that handles it, with `error_class.log_failure` (see "Failures"). Lower layers raise
  (`raise … from e`), they do not log and re-raise.
- **Levels**: ERROR = our fault, needs a human. WARNING = degraded, retried, or an upstream 4xx. INFO = lifecycle, about one line
  per unit of work. DEBUG = per item.
- **Never log** secrets, tokens, request or response bodies, prompts, document content, emails or names. Log ids, sizes, counts and
  enums. Org and user ids are opaque ids and may be logged; the provider can hash or hide them. Emails and names: see "Personal
  data", which CI checks.
- **No emoji** in log text: the level already says it. CI (`.github/scripts/new_lint_findings.py log-emoji`, scanner
  `scripts/log_emoji.py`) fails a pull request that adds an emoji log call to a file under `backend/python/app`; ones already on
  main do not fail, and moving one to another file counts as new. It reads the direct arguments of any method named after a
  level, so `warnings.warn("✅")` counts too.
- Don't change behaviour to add logging.
