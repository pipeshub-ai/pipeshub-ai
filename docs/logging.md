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
kept, and decide how an org is shown (`hash_org_id`; a value it returns is set on the record as `organization_id_hash` and so
appears as a field). A provider formatter still gets redaction.

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

## Redaction

Both formats redact: the whole text line (message, fields, traceback), and in JSON every string value. Credentials (bearer and
basic tokens, JWTs, known key shapes, URL passwords and query secrets, private keys, this process's secret env values, and
`name=value` pairs whose name is a credential) are replaced by `[REDACTED]`. A field whose **name** is a credential (`token`,
`apiKey`, …) is redacted when its value is a string or an object; numbers and booleans (`token_count=12`) are kept. Redaction is a
safety net; it cannot recognise content, so the rules below still apply.

## Conventions

- **Bind ids once** where work enters: an HTTP request, a broker message, a scheduled tick. Never thread ids through call sites.
- **Message = static text or an event name**; ids and values go in fields, not into the message string:
  `logger.info("sync_run.end", extra={"connector_id": cid, "duration_ms": ms})`.
- **Event names** are dotted lowercase: `sync_run.end`, `tool_call.end`, `http.request.end`.
- **Field names** are snake_case and the same in Python and Node: `org_id`, `user_id`, `request_id`, `conversation_id`, `agent_id`,
  `run_id`, `connector_id`, `connector_type`, `record_id`, `kb_id`, `tool`, `tool_call_id`, `mcp_server_id`, `toolset_id`,
  `duration_ms`, `status`, `error_class`, `outcome`.
- **Fields are scalars**: ids, sizes, counts, enums. Never pass a config, a request, a response or a document as a field.
- **Log an error once**, at the layer that handles it, with the stack (`logger.exception` or `exc_info=True`) and an
  `error_class`. Lower layers raise (`raise … from e`), they do not log and re-raise.
- **Levels**: ERROR = our fault, needs a human. WARNING = degraded, retried, or an upstream 4xx. INFO = lifecycle, about one line
  per unit of work. DEBUG = per item.
- **Never log** secrets, tokens, request or response bodies, prompts, document content, emails or names. Log ids, sizes, counts and
  enums. Org and user ids are opaque ids and may be logged; the provider can hash or hide them.
- **No emoji** in log text: the level already says it. CI (`.github/scripts/new_lint_findings.py log-emoji`, scanner
  `scripts/log_emoji.py`) fails a pull request that adds an emoji log call to a file under `backend/python/app`; ones already on
  main do not fail, and moving one to another file counts as new. It reads the direct arguments of any method named after a
  level, so `warnings.warn("✅")` counts too.
- Don't change behaviour to add logging.
