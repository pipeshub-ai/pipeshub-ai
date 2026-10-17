# Connector errors and logging

A connector runs inside a customer's deployment, against a customer's account at the source. When a sync fails there, the connector service log is usually the only evidence anyone gets. Write every error log so that someone who can read the log, but not the customer's source account, can say what the source refused and why.

The rules below are required. `CONNECTOR_CHECKLIST.md` repeats them as checks.

## Rules

1. **Log what the source said.** When a vendor API call fails, the log line carries the vendor's own error message or reason, not just "failed" or a bare exception type.
2. **Log the status and the vendor error code.** Include the HTTP status, and the vendor's error code when the API has one (Microsoft Graph `error.code`, Slack `error`, Google `reason`, Jira `errorMessages`, a GraphQL `extensions.code`).
3. **Name the operation and the object.** Say which call failed and on what: `list children of folder <external id>`, `fetch page <id> in space <key>`. Use source ids, not names, when the name could be personal data.
4. **Never log a secret.** No access tokens, refresh tokens, API keys, client secrets, passwords, PATs, signed URLs, `Authorization` / `Cookie` / `X-Api-Key` headers, or whole request or response header maps. This holds at every log level, including `debug`.
5. **Do not log whole response bodies.** A body can echo a token, contain customer content, or be megabytes long. Extract the error fields; if you must keep raw text, bound it and redact URLs in it (see below).
6. **Keep the traceback for unexpected errors.** Use `exc_info=True` (or `logger.exception`) when the failure is not one you anticipated. An expected, handled vendor refusal (404 on a deleted item, 403 on a private space) needs the status and reason, not a traceback.
7. **Do not swallow a failure.** If you catch, either re-raise, or log at `error`/`warning` with the details above and record the item as failed so the saved checkpoint stays before it (`FailedItems.add(modified_ms)`, then save `failed.checkpoint(max_seen_ms)`; `app/connectors/core/base/sync_point/sync_point.py`). A bare `except Exception: pass`, or an `except` that returns an empty list as if the source had no data, makes the connector delete or skip data silently.
8. **Pick the level by who must act.** `error`: the sync, or a whole user, group or container, could not be read. `warning`: one item was skipped and the sync continues. `info`: progress and counts. `debug`: per-item detail with no secrets in it.

## Helpers that already exist

Use these instead of writing a new one.

| Need | Use | Path |
| --- | --- | --- |
| HTTP status off any SDK exception (httpx, aiohttp, requests, botocore, googleapiclient, ...) | `extract_source_status(exc)` | `app/connectors/core/base/error/stream_errors.py` |
| Map a source failure to the HTTP status a user sees when opening or indexing a record | `to_stream_error(exc, connector=...)`, `map_source_status(status, ...)`, `raise_for_stream_fetch(...)`, `not_found_at_source`, `connector_not_ready`, `not_downloadable` | same file |
| Status-carrying exceptions for your own client | `HTTPException(status_code, message)` and subclasses (`UnauthorizedError`, `TooManyRequestsError`, ...) | `app/sources/client/http/exception/exception.py` |
| Status, headers and body of an httpx response | `HTTPResponse.status`, `.json()`, `.text()` | `app/sources/client/http/http_response.py` |
| Strip credentials from a URL, or from free text that contains URLs | `redact_url`, `redact_sensitive_query_params`, `redact_urls_in_text` | `app/utils/url_redaction.py` |
| A reference for pulling code, message and status out of a vendor error | `sanitize_graph_error` (Microsoft Graph) | `app/connectors/sources/microsoft/onedrive/connector.py` |

`stream_errors.py` reports an expired or revoked connection as **409**, not 401: the frontend treats any 401 as the PipesHub session expiring and logs the user out. Keep that rule when you map errors in `stream_record`. `to_stream_error` passes any `HTTPException` through unchanged, and the client exceptions in `exception.py` are `HTTPException`s, so an `UnauthorizedError` raised by your client would reach the user as 401. In `stream_record`, convert them with `map_source_status(e.status_code, connector=self.display_name)`.

If your vendor's errors have a stable shape, write one small function in the connector package that turns a vendor exception or response into `"<code>: <message> (HTTP <status>)"`, the way `sanitize_graph_error` does, and use it at every log site. That keeps the format the same across the connector and keeps secrets out in one place.

## Examples

Bad: no status, no vendor reason, no object.

```python
except Exception as e:
    self.logger.error(f"Failed to fetch spaces: {e}")
```

Bad: the status alone. The customer sees `403` and cannot tell a missing scope from a private space.

```python
self.logger.error(f"Failed to fetch spaces: {response.status}")
```

Bad: leaks the token.

```python
self.logger.debug(f"Request headers: {self.headers}")
```

Good: operation, object, status, vendor code and message; no secret; traceback only when the failure was unexpected.

```python
except VendorApiError as e:
    self.logger.warning(
        "Fetching page %s in space %s failed: %s",
        page.id,
        space_key,
        describe_vendor_error(e),  # "permission_denied: Missing scope read:page (HTTP 403)"
    )
    failed.add(page.updated_at_ms)
except Exception:
    self.logger.exception("Fetching page %s in space %s failed unexpectedly", page.id, space_key)
    failed.add(page.updated_at_ms)
```

Good: a bounded body with URLs redacted, for an API whose errors have no structured fields.

```python
from app.utils.url_redaction import redact_urls_in_text

detail = redact_urls_in_text(response.text()[:500])
self.logger.warning("Fetch of item %s returned HTTP %s: %s", item_id, response.status, detail)
```

## Before you finish

Search your connector and its client for these and fix every hit:

```bash
cd backend/python
# log calls, including multi-line ones, that mention credentials
rg -niU 'logger\.\w+\((?s:[^)]{0,300})(headers|token|secret|password|api_key|authorization|cookie|credential|signed_url)' app/connectors/sources/<name> app/sources/client/<name> app/sources/external/<name>
# except blocks that swallow the failure
rg -nU 'except[^\n]*:\s*\n\s*(pass|return( \[\]| None| \{\})?|continue)\s*$' app/connectors/sources/<name> app/sources/client/<name>
```

A hit is not always a leak or a swallowed error: a log that names the field `token_expired` is fine, and so is a `continue` after a logged warning and a `FailedItems.add`. Read each one.
