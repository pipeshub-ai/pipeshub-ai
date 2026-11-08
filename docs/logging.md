# Logging

Goal: from the logs alone, tell **what** happened, **why**, and **with whom**. This page is the contract every logging change
follows. The Python side is `backend/python/app/utils/logger.py`; Node mirrors the line shape and field names.

## Configuration (Python)

| Env var | Values | Default | Effect |
| --- | --- | --- | --- |
| `LOG_LEVEL` | `debug`, `info`, `warn`/`warning`, `error`, `critical` (any case) | `info` | Level of the `app` logger, of every `create_logger` logger, and of the root logger. Root never goes below `info`, so third-party libraries never log at debug by accident (some log whole requests, prompts and bodies there). Use `debug`, `info`, `warn` or `error`: Node reads the same variable. |
| `LOG_LEVELS` | `name=LEVEL,name=LEVEL` | empty | Per-logger levels, applied last. Any logger can be named, `root` included. `LOG_LEVELS=app.services.graph_db=DEBUG,aiokafka=WARNING`. `LOG_LEVELS=root=DEBUG` opens third-party debug output on purpose. |
| `LOG_FORMAT` | `text`, `json` | `text` | Python only (Node keeps its own format). |

A bad value never stops a service: an unknown `LOG_LEVEL` (or `notset`) means `info`, a malformed `LOG_LEVELS` entry is skipped,
an unknown `LOG_FORMAT` means `text`; each logs one warning. Values are read at import and again whenever they change before a
`create_logger` call (so a `.env` loaded later is honoured), and once more at service start-up (`install_log_redaction` in each
lifespan). Per-service files keep the format they were opened with.

Without a `LOG_LEVELS` entry, `opensearch` stays at `WARNING` and, when `DATA_STORE=neo4j`, `neo4j.notifications` at `ERROR`.
httpx's successful (2xx) request lines are always dropped; failures are kept, with credentials cut from the URL.

`LOG_LEVEL` does not reach first-party loggers named outside `app` (`agent_loop.events`, `sync_point`,
`pipeshub.attachment_audit`, `parsing_main`, `extraction_main`, `docker_proxy`); name them in `LOG_LEVELS` until they are renamed.

## One stream

Every logger, `logging.getLogger(__name__)` and `create_logger(name)` alike, writes through one handler on the root logger, to
**stdout**, with one formatter and redaction. Colour (yellow WARNING, red ERROR) is added only when stdout is a terminal.
`create_logger` adds no console handler of its own; it adds `logs/<name>.log` when the logging provider keeps files, and that file
holds only the lines of that logger and its children. stdout is the complete record.

Exceptions: uvicorn's own handlers (`uvicorn` on stderr, `uvicorn.access` on stdout). Under `LOG_FORMAT=json` they get the JSON
formatter at service start-up; the few lines uvicorn writes before that stay text. If a script configures the root logger before
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
| fields | each `extra=` field with its JSON type (NaN and infinities as strings); a field named like a standard key does not replace it, and `stack` is reserved (left out in text and JSON) |
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

- Bindable ids (`LOG_FIELD_NAMES`): `conversation_id`, `agent_id`, `run_id`, `parent_run_id`, `attempt`, `connector_id`,
  `connector_type`, `record_id`, `kb_id`, `tool`, `tool_call_id`, `mcp_server_id`, `toolset_id`, `job`, and for credential
  refresh `refresh_kind`, `trigger`, `provider`, `scheduled_by`. A module that needs another adds it there and here. A name with
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

## Connector sync runs

`run_sync_task` (`app/connectors/core/sync/sync_runner.py`) is the one body every sync runs in. It binds `run_id` and
`connector_id` for the run; the entry points (`EventService.process_event`, `ConnectorFactory.create_and_start_sync`) bind
`connector_id` and `connector_type` (the instance's stored type once it is read, not the event's name). `org_id` comes from the
consumer's or the boot job's OrgContext. So every line of a run, the connector's own included, carries them.

| Event | Level | Fields |
| --- | --- | --- |
| `sync_run.start` | INFO | `sync_type` (`full`, `incremental`) |
| `sync_run.end` | INFO; WARNING for `partial` or a lost lease; a failure at its class's level | `sync_type`, `duration_ms`, `outcome`, `reason`, `full_sync_cut_short=true` when the next sync runs in full; on failure the `log_failure` fields |
| `sync_run.refused` | INFO | `reason` (the admission) |
| `sync_run.start_failed` | the failure's level | the `log_failure` fields |
| `sync_step.failed` | ERROR, WARNING for an optional step | `step` (`status_write`, `idle_write`, `drain`, `unlink`, …), the `log_failure` fields |

`outcome` on `sync_run.end`: `success`; `partial` (a full sync that finished but did not unlink what it did not see: the connector skipped part of the source, or the group-member write or the unlink failed);
`skipped` with `reason=<code>` (the connector declined, e.g. `DESKTOP_OFFLINE`); `failure` with `error_class=cancelled` and
`reason` = `requested`, `shutdown`, `cancelled` or `lease_lost` for a stop; `failure` with the exception's class otherwise. A
failed run's exception is logged once there; `SyncTaskManager` then logs it at DEBUG (`task.failed`).

## Agent loop events (Python)

One chat turn is reconstructable from these lines; each carries the bound `org_id`, `user_id`, `conversation_id`,
`agent_id` and (inside the run) `run_id`.

| Event | Level | Fields |
| --- | --- | --- |
| `agent_turn.end` | INFO; WARNING for `max_turns` and for an error no other line reported; `level_for(error_class)` for a pre-loop rejection | `stop_reason`, `end_event`, `turns`, `tool_calls`, `tool_errors`, `llm_calls`, `tokens_in`, `tokens_out`, `cache_read_tokens`, `duration_ms`, `model`, `mode`, `spans` (when `PIPESHUB_CHAT_TIMING` is on, the default), `error_class`, `code` |
| `agent_turn.failed` | `log_failure` policy | a turn that crashed (before `agent_turn.end`, which is then INFO) |
| `llm_call.end` | INFO; DEBUG for a failed attempt (`llm_call.retry` or `llm_call.failed` reports it) | `source`, `provider`, `model`, `duration_ms`, `tokens_in`, `tokens_out`, `cache_read_tokens`, `finish_reason`, `tools_offered`, `tool_calls`, `attempt`, `outcome`; on failure `error_class`, `error_type`, `status` |
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
(`agent_tool`, `handoff`, `best_of_n`, a detached spawn) inherits them; the children of a `spawn_agent` batch don't, because
the batch is launched before the calls are dispatched.

| Event | Level | Fields |
| --- | --- | --- |
| `tool_call.end` | success and skipped INFO; failure at `level_for(error_class)`, WARNING when unclassified; parked for approval DEBUG | `kind` (builtin, mcp, openapi, sub_agent), `outcome`, `reason` (exception, tool_error, bad_args, malformed_args, unknown_tool, denied, declined, expired, unavailable, duplicate, awaiting_approval, blocked, server_error, stopped, cancelled_before_execution), `error_class`, `error_type`, `status`, `mcp_reason`, `duration_ms` (the run, without an approval wait) |
| `tool.blocked` | WARNING, once per streak | `tool_path`, `consecutive_failures` |
| `mcp.connect.end` | success DEBUG, failure INFO | `mcp_server_id`, `transport`, `mode`, `protocol_version`, `outcome`, `error_class`, `mcp_reason`, `status`, `error_type`, `cause_type` (the innermost cause's type), `duration_ms` |
| `mcp.retry` | WARNING; `reason=renegotiate` INFO | `mcp_server_id`, `op` (connect, list, call), `reason` (token_refresh, resend, relist, renegotiate), `error_type` |
| `mcp.discover.end` | a failure at its class's level; `reason=no_attached_tools` WARNING | `mcp_server_id`, `outcome`, `error_class`, `mcp_reason`, `error_type`, `source`, `duration_ms` |
| `mcp.result.schema_mismatch` | WARNING (the result is kept) | `error_type` |
| `mcp.redirect_refused` | WARNING (the call fails with a blocked-URL error) | `mcp_server_id`, `target_host` |
| `mcp.session.ended` | WARNING, when a closed connection had a recorded cause | `mcp_server_id`, `error_type` |
| `mcp.load.end` | INFO, once per turn | `servers`, `servers_loaded`, `tools_listed`, `failed` |
| `openapi.retry` | WARNING | `status`, `reason` |
| `tool_approval.run_failed` | DEBUG in practice: the executor already logged it | `error_class`, `error_type` |

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

## Credential refresh

The connector (and later toolset and MCP) token refresh services log through `app/utils/credential_refresh_log.py`, so one
refresh of one credential reads the same everywhere:

| Event | Level | Fields |
| --- | --- | --- |
| `credential_refresh.scheduled` | INFO when the refresh time is new, DEBUG when a scan re-arms the same one | `next_refresh_at` (UTC), `delay_s` |
| `credential_refresh.start` | DEBUG | |
| `credential_refresh.end` | INFO on success; a failure at its class's level | `outcome`, `reason` (`adopted`, `invalid_grant`, `config_missing`, `save_failed`, `recursion`), `duration_ms`, and on failure `error_class`, `error_type`, `error_message`, `http_status`, `oauth_error` |
| `credential_refresh.check_failed` | the failure's level; DEBUG when the attempt's `end` already logged it | as a failed `end` |
| `credential_refresh.strike` | INFO | `strike` (`2/3`), `counted` (false: the rejected token was already rotated), the cause's `error_class` and `oauth_error` |
| `credential_refresh.deauthenticated` | WARNING | `reason`, `strikes`, `app_disabled_event` (`sent`, `direct_write`) |
| `credential_refresh.not_scheduled`, `.deauthenticate_failed`, `.app_disabled_event_failed` | WARNING, ERROR, by class | `reason` / failure fields |

Every line in a refresh carries `refresh_kind` (`connector`, `toolset`, `mcp`), `trigger` (`scan`, `scheduled`, `on_demand`), the
kind's ids (`connector_id`, `connector_type`), `provider`, and the org as a job context when nothing else bound one. A timer
firing gets its own `sys-credential_refresh-` root, with `scheduled_by` naming the root that armed it. Exactly one `end` is
written per attempt, by the code that calls the token endpoint. A misconfigured credential (no OAuth client, the provider
rejected our client) is ERROR with a stack once per credential per hour and DEBUG in between. `trigger` is set by the refresh
scope and never inherited from a sync's own `trigger`.

Nothing a token endpoint answered is logged as text: its body, and the token URL and redirect URI in the exception message, can
quote the refresh token in prose no redactor recognises. When the exception chain holds such an answer, every failure line of
a refresh service (`log_refresh_failure`) leaves the text out of `error_message` and the stack (each exception shows as
`Type: <message omitted>`) and logs `http_status` and `oauth_error` instead.

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
apply to `name=value` pairs inside text, not to a field's own value. Numbers and booleans are kept: in JSON always, and in text
booleans always and integers of up to 6 digits or under a time name (`token_count=12`, `token_expires_at=1730000000`); a longer
number or a fraction under another credential name (`token_count=12345678`, `token_ratio=0.5`) is masked in text.
Redaction is a safety net; it cannot recognise content, so the rules below still apply.

## Conventions

- **Bind ids once** where work enters: an HTTP request, a broker message, a scheduled tick. Never thread ids through call sites.
- **Message = static text or an event name**; ids and values go in fields, not into the message string:
  `logger.info("sync_run.end", extra={"connector_id": cid, "duration_ms": ms})`.
- **Event names** are dotted lowercase: `sync_run.end`, `tool_call.end`, `http.request.end`.
- **Field names** are snake_case and the same in Python and Node: `org_id`, `user_id`, `request_id`, `conversation_id`, `agent_id`,
  `run_id`, `parent_run_id`, `attempt`, `connector_id`, `connector_type`, `record_id`, `kb_id`, `tool`, `tool_call_id`,
  `mcp_server_id`, `toolset_id`, `job`, `workload`, `duration_ms`, `status`, `error_class`, `error_type`, `outcome`. Python also has
  `ctx_kind`, `ctx_source` and `error_message`; Node's `error` is an object (`error.message`).
- **Fields are scalars**: ids, sizes, counts, enums. Never pass a config, a request, a response or a document as a field.
- **Log an error once**, at the layer that handles it, with `error_class.log_failure` (see "Failures"). Lower layers raise
  (`raise … from e`), they do not log and re-raise.
- **Levels**: ERROR = our fault, needs a human. WARNING = degraded, retried, or an upstream 4xx. INFO = lifecycle, about one line
  per unit of work. DEBUG = per item.
- **Never log** secrets, tokens, request or response bodies, prompts, document content, emails or names. Log ids, sizes, counts and
  enums. Org and user ids are opaque ids and may be logged; the provider can hash or hide them.
- **No emoji** in log text: the level already says it. CI (`.github/scripts/new_lint_findings.py log-emoji`, scanner
  `scripts/log_emoji.py`) fails a pull request that adds an emoji log call to a file under `backend/python/app`; ones already on
  main do not fail, and moving one to another file counts as new. It reads the direct arguments of any method named after a
  level, so `warnings.warn("✅")` counts too.
- Don't change behaviour to add logging.
