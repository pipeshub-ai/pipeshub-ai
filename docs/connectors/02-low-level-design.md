# Connectors: low-level design

The class-level reference for building a connector. Paths are relative to `backend/python/app/`. Where this file and the code disagree, the code is right; fix this file in the same PR.

## Package layout

A connector named `acme` normally has:

```text
app/sources/client/acme/acme.py            auth-aware client: AcmeClient.build_from_services(...), IClient
app/sources/external/acme/acme.py          API wrapper ("data source"): one async method per endpoint
app/connectors/sources/acme/common/apps.py AcmeApp(App)
app/connectors/sources/acme/connector.py   @ConnectorBuilder(...) class AcmeConnector(BaseConnector)
app/connectors/sources/acme/*.py           optional: access.py, deletes.py, transforms, per-area helpers
```

Keep the connector class about orchestration. Put the mapping of source objects to `Record`s, the permission mapping and the deletion detection in their own modules once they grow past a few functions (Dropbox's `access.py`, `deletes.py` and `shared_folders.py` are an example).

## The API client layer

| Layer | Responsibility | Example |
| --- | --- | --- |
| `sources/client/<vendor>/` | Read auth config, build an authenticated HTTP or SDK client, refresh credentials. Implements `IClient.get_client()` (`sources/client/iclient.py`) and an `async build_from_services(logger, config_service, connector_instance_id)` classmethod that reads `/services/connectors/{connector_id}/config` and branches on `auth.authType`. | `sources/client/linear/linear.py` |
| `sources/external/<vendor>/` | A thin async wrapper with one method per endpoint, returning a response object that carries success, data, error and status. Often generated. | `sources/external/linear/linear.py` (`LinearDataSource`) |

The connector does `client = await AcmeClient.build_from_services(...)` and then `AcmeDataSource(client)`.

- Generators for the `external` wrappers live in `backend/python/code-generator/` (one script per vendor; OpenAPI-based ones take a spec URL). They write into `code-generator/<vendor>/`; copy the result into `app/sources/external/<vendor>/` and review it. They are dev-only and not run in CI.
- HTTP clients take a `ResiliencePolicy` for rate limiting and retries: `HTTPClient(token, ..., resilience=self.resilience)` (`sources/client/http/http_client.py`, transport in `http_resilient_transport.py`). The policy comes from `.with_resilience_config(...)` on the decorator and is cached on the connector (`BaseConnector.resilience`). Prefer this to a hand-written retry loop. Notion, Nextcloud and Drupal Wiki use it.
- Re-read the token from config before each batch of calls, not once at `init()`. OAuth tokens are refreshed in the background and written back to config; a client built at `init()` holds an expired token an hour later. Linear's `_get_fresh_datasource()` is the pattern. For a 401 in the middle of a sync, refresh once and retry the call: `TokenRefreshService.refresh_now(connector_id, connector_type, refresh_token)` (get the service from `startup_service.get_token_refresh_service()`). Pass the refresh token you already held. `refresh_now` is the single-flight; the OAuth bullets below say what not to wrap around it. The shared HTTP retry does not retry a 401.
- A connector's server address is typed by a user, so its requests follow the deployment's address rule: loopback, link-local and cloud metadata addresses never, private ones only while `PIPESHUB_BLOCK_PRIVATE_ADDRESSES` is off. `HTTPClient` applies it to every request, redirects included (`sources/client/http/address_guard.py`). A client on another library uses the same guard: `guarded_async_client()` for httpx, `AddressGuardAdapter` mounted on a requests session, `create_guarded_session(allow_private=not private_addresses_blocked())` for aiohttp, or, only for an SDK that connects by itself and takes no guarded transport, `check_url_allowed(url)` before it connects. That one checks the address but cannot pin it: the SDK looks the name up again. A URL that comes from content rather than configuration, such as an image link inside a record, reaches public addresses only: `guarded_async_client(allow_private=False)`. A user-supplied base URL must also be `https` unless the human agreed otherwise. The Web connector's `address_guard.py` is the reference for fetching arbitrary URLs.
- A synchronous SDK must not run on the event loop. Submit its calls to the connector's share of the shared thread pool: acquire it in `init()` with `self._thread_lease(max_concurrency)`, then `await asyncio.wrap_future(lease.submit(fn, *args))`, and release it in `cleanup()` with `await self._release_thread_lease()`. Google Drive individual is the example.
- `HTTPClient` takes `token_type` (default `"Bearer"`). A Discord-style bot token is `token_type="Bot"`. Do not build a second HTTP stack for that.
- Before writing a client, search for one that already exists: `app/sources/client/<vendor>/`, `app/sources/external/<vendor>/`, and `app/agents/actions/<vendor>/`. The agent toolset may already call that client. Changing it is a shared change. Airtable is the case: the client exists, and the connector class in `registry/connector.py` is still a placeholder.

OAuth is already implemented. Do not reimplement the token exchange in the client.

- Authorization starts at `OAuthProvider.start_authorization()` (`oauth_service.py`). The connector route calls it with no arguments, and PKCE defaults to on.
- The authorization-code exchange and the refresh both POST to the `token_url` registered on the builder (`oauth_service.py`). There is no separate refresh URL. A vendor that refreshes somewhere else needs a change in shared code, which you ask for first. Do not register a refresh-only endpoint as `token_url`: the first sign-in sends the authorization code there, and that endpoint does not accept the code grant.
- Both POSTs go through `OAuthProvider.session`, an aiohttp `ClientSession` created with `max_field_size=32 * 1024`. aiohttp's default refuses any response header over 8190 bytes, and GitHub's token endpoint sends a Content-Security-Policy header of about 9 KB. Do not lower the limit, and do not create a default `ClientSession` for a token request.
- Basic-auth at the token endpoint is a hostname list, `_BASIC_AUTH_ONLY_TOKEN_HOSTS` in `app/utils/oauth_config.py` (`airtable.com`, `zoom.us` today). A public client with no secret is already handled.
- `TokenRefreshService.refresh_now` already refreshes one connector at a time. Under `connector_refresh_lock` it re-reads config, and `_adopt_already_rotated_token` returns the stored access token when the refresh token you pass is no longer the stored one, without posting (`token_refresh_service.py`). Pass the refresh token you held before the call. A lock that re-reads config and passes the current refresh token skips that adopt and rotates a rotating token a second time. GitLab (`gitlab/runtime.py`) snapshots the client token before its own lock. If that token, or the stored access token, has already changed, it returns and does not call `refresh_now`. Otherwise it calls `refresh_now` with the refresh token it held. Copy that comparison. Do not write another token exchange.
- A static API token or bot token has no refresh token. The refresh service skips it. A 401 on that connector is a revoked token: stop and `notify`. Retrying it only spends the vendor's invalid-request budget.

## `App`

```python
# app/connectors/sources/acme/common/apps.py
from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App

class AcmeApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.ACME, AppGroups.ACME, connector_id)
```

Add `ACME` to `Connectors` and `AppGroups` in `config/constants/arangodb.py`. The `Connectors` value is what is stored as `connectorName` on every record.

## `BaseConnector` contract

`connectors/core/base/connector/connector_service.py`. Every abstract method must be implemented or the class cannot be instantiated, and the contract test fails.

| Method | Called by | Must do |
| --- | --- | --- |
| `@classmethod async create_connector(cls, logger, data_store_provider, config_service, connector_id, scope, created_by, data_entities_processor, **kwargs)` | `ConnectorFactory` | Construct and return the instance. Take `scope` and `created_by` as named parameters and accept `**kwargs`; the factory also passes `org_id`. The contract test checks only the five base names, and the factory catches a `TypeError` here, logs it with its traceback and returns `None`, so a missing `scope` leaves the connector unbuilt. Call `super().__init__(app, logger, data_entities_processor, data_store_provider, config_service, connector_id, scope, created_by)` in that order. |
| `async init() -> bool` | event service, on init or first use | Read config, build the client, set up `SyncPoint`s. Return `False` on failure, or raise `ConnectorInitError` with a message the user should see ("the API token was rejected"). Do not start a sync. |
| `async test_connection_and_access() -> bool` | toggle-enable, filter options, and stream lazy-init, via `_build_and_store_connector` after `init()` (`router.py`); awaited, though the base declares it sync. Not the `<connector>.init` event, and not the setup-form test route (`check_connection`) | One cheap authenticated call that also checks the scopes or permissions this connector needs. Returning `False` is shown as "Connection test failed. Please verify your credentials have proper access." Raise `ConnectorInitError` to name the missing grant; that message is what the user sees. Notion does this for a missing scope. Log the vendor's reason. |
| `@classmethod async check_connection(cls, auth_config, logger) -> ConnectionCheckResult`, optional | setup form | Override to test credentials the user has typed but not saved. Returns `ConnectionCheckResult(success, message)`, not a bool. |
| `@classmethod auth_save_problem(cls, auth_config, stored_auth_config) -> str \| None`, optional | every auth save on the server (create, `PUT …/config/auth`, `PUT …/config`) and the setup-form test route, after `auth_config_problems` | Say why these auth settings may not be saved, in words for the user; the route answers 400 with it. `None` (the default) lets the save go ahead. `auth_config` is what the save would store, laid over `stored_auth_config`; that is `None` while the instance is being created, so a rule can depend on what an instance already has. OneDrive refuses a client secret on a new connector but lets one that already signs in with a secret keep it. |
| `@classmethod config_action_items(cls, auth_config) -> list[ActionItem] \| None`, optional | every auth save, with the settings just saved | What the admin has to do about the saved auth settings, stored on the instance as `actionItems` and shown as an action-needed badge and callout. Return `[]` to clear them; `None` (the default) leaves what is stored. Each `ActionItem` has a `code`, `severity` (`warning` or `error`), `title`, `message`, optional `action` (`authenticate` opens the Authenticate tab) and optional `docs_url`. Dump with `model_dump(by_alias=True)`: it is stored as `docsUrl`. |
| `async run_sync() -> None` | `SyncTaskManager` via `run_sync_task` | The whole sync: full if no checkpoint, incremental otherwise. See "Sync flow" below. |
| `async run_incremental_sync() -> None` | nothing in the platform today | Implement it (delegating to `run_sync()` is acceptable, as Linear does). |
| `full_sync_coverage() -> FullSyncCoverage`, optional | sync runner, after a full `run_sync()` returns | Say how much of the source the run read. Default: all of it. Two different overrides: `complete=False` when the run skipped part of the source after an error (nothing is unlinked); `window_start_ms` (plus `windowed_group_types`) when it reads only a date window by design (records before the window, windowed groups created before it and their records keep hierarchy and inheritance but lose their direct grants; every other unseen node is unlinked). See `01-high-level-design.md`. |
| `async get_signed_url(record) -> Optional[str]` | nothing in the platform; your own `stream_record` may use it | A short-lived download URL, or `None` / `""` if the source has none. To let indexing download directly, set `record.signed_url` on the record instead (sent as `signedUrl` in the index event; only File, Webpage, Link and Ticket records carry it). Never return a URL that embeds a long-lived credential. |
| `async stream_record(record, user_id=None, convertTo=None) -> StreamingResponse` | stream route (indexing and users) | Return the content. The route calls `stream_record(record)`: `user_id` is passed only to the Google Workspace connectors, `convertTo` never (the router converts to PDF itself), and access is checked before the call. Do not rely on either. Files: `create_stream_record_response(stream_content(signed_url, ...), ...)` from `utils/streaming.py`, as Box does. Structured items (issues, pages): JSON blocks with `MimeTypes.BLOCKS`, as Linear does. Map source failures with `stream_errors` (`07-errors-and-logging.md`). |
| `handle_webhook_notification(notification)` | nothing in the platform today | Implement as a no-op or log, unless the human asked for webhooks. |
| `async reindex_records(record_results)` | `<connector>.reindex` | For each record, check the source; upsert changed ones with `on_new_records`, re-publish the rest with `reindex_existing_records`. Linear is the pattern. |
| `async get_filter_options(filter_key, page, limit, search, cursor) -> FilterOptionsResponse` | filter options route | Return options for every `DYNAMIC` filter you declared (for example the list of spaces). Raise for an unknown key. |
| `async cleanup()` | disable, delete, shutdown | Close clients, release the thread lease. Must not raise. |

Other members you will use:

- `self.data_entities_processor`, `self.config_service`, `self.data_store_provider`, `self.connector_id`, `self.scope`, `self.created_by`, `self.logger`.
- `display_name`: the instance name the user sees. Use it in user-facing messages.
- `ensure_creator_user_app_relation()`: personal-scope connectors call it so the creator can reach the App (see `06-permissions.md`). It needs `self.creator_email`, set only by `await self._load_creator_email()`; call that in `init()` first, or the method logs a warning and the creator gets no access (`github/connector.py` and `notion_personal/connector.py` do this).
- `self.last_action_items`: set it during a run to the action items that run found (a list, `[]` for none); the sync runner stores it with the run's outcome, replacing what `config_action_items` wrote. Left `None`, the stored items stay as they are.
- `ConnectorSyncSkippedError`: raise from `run_sync()` when the sync cannot run for a reason the user must fix, instead of returning as if it succeeded.
- `notify(type, severity, title, message, ...)`: tell the connector's creator and last syncer about something they must act on (auth failure, a missing scope, a skipped container, an expired cursor). Fire-and-forget; repeats of the same title and message are backed off automatically (from one hour, doubling to seven days) except INFO and SUCCESS. Use `notify_and_wait(...)` and check for `NotificationOutcome.SENT` when you persist that a notice was sent. Name the instance with `display_name`.

## Registration: `ConnectorBuilder`

`connectors/core/registry/connector_builder.py`. The decorator is the single source of the connector's metadata: the UI builds the connector card and settings form from it.

```python
@ConnectorBuilder("Acme")\
    .in_group(AppGroups.ACME.value)\
    .with_description("Sync pages and attachments from Acme")\
    .with_categories(["Knowledge Management"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Acme",
            authorize_url="https://acme.example/oauth/authorize",
            token_url="https://acme.example/oauth/token",
            redirect_uri="connectors/oauth/callback/Acme",
            scopes=OAuthScopeConfig(personal_sync=[], team_sync=["read"], agent=[]),
            fields=[CommonFields.client_id("Acme"), CommonFields.client_secret("Acme")],
        ),
        AuthBuilder.type(AuthType.API_TOKEN).fields([CommonFields.api_token("Acme API token")]),
    ])\
    .with_resilience_config(rate_limit=10.0, max_retries=3)\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.ACME.value))
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(True)
        .add_filter_field(FilterField(name="space_ids", display_name="Spaces",
                                      filter_type=FilterType.LIST, category=FilterCategory.SYNC,
                                      option_source_type=OptionSourceType.DYNAMIC))
        .add_filter_field(CommonFields.modified_date_filter("Only pages modified in this range."))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
    )\
    .build_decorator()
class AcmeConnector(BaseConnector):
    ...
```

Check every builder call against the code before you copy it: `CommonFields` helper names and `with_resilience_config` parameters are in `connector_builder.py`, auth types and `OAuthScopeConfig` in `connectors/core/registry/auth_builder.py`, field types in `connectors/core/registry/types.py`. `examples/` code and old docs have used methods that no longer exist.

| Call | Notes |
| --- | --- |
| `ConnectorBuilder(name)` | The display and registry name. The factory key is this name lower-cased with spaces removed. Integration suites must use it exactly, spaces included. |
| `.with_scopes([...])` | `ConnectorScope.TEAM` (an admin sets it up for the org), `PERSONAL` (each user for their own account), or both. Default is personal only. |
| `.with_permission_model(PermissionModel.X)` | `RECORD_LEVEL` (default): the source has per-item access. `APP_LEVEL`: anyone who can reach the App can read everything in it. `RECORD_GROUP_LEVEL` is rejected here. See `06-permissions.md` before setting `APP_LEVEL`. |
| `.with_auth([AuthBuilder...])` | One entry per supported auth type. OAuth entries register with the OAuth registry. The redirect URI is always `connectors/oauth/callback/<Name>` (`OAuthRedirectPaths` in `connectors/core/constants.py`); the Node callback route is generic. |
| `.with_info(text)` | A note shown on the connector card. Use `CONNECTOR_EMAIL_IDENTITY_INFO` when users are matched by email. |
| `.with_resilience_config(rate_limit=..., max_retries=3, base_delay=1.0, max_delay=60.0)` | One limiter for the whole connector, in this process. `rate_limit` is requests per second. `Retry-After` is honoured only as a number of seconds, and it is capped at `max_delay` (60 unless you set it): `ResiliencePolicy.backoff` in `app/sources/client/resilience.py`. An HTTP-date `Retry-After` is ignored and the policy falls back to jittered exponential backoff. A per-route bucket, a per-container limit, or a daily quota does not fit this one number. Ask before adding a second limiter. Jira Cloud already waits on its own 429s in `_rate_limit_delay` (`jira_cloud/connector.py`), also capped at 60 seconds, for calls this policy does not cover; copy that only after asking, and do not add a sleep loop beside the policy. A daily `429` is not a short retry: stop, `notify`, and leave the checkpoint where it is. |
| `.configure(lambda b: ...)` | `ConnectorConfigBuilder`: icon, sync strategies and schedule, realtime and agent support, documentation links, custom sync fields, filters, conditional display. |

Register the class in `ConnectorFactory._connector_registry` (`connectors/core/factory/connector_factory.py`), keyed by the lower-cased, space-free name, with its import at the top of that file. Put connectors that are not ready for general use in `_beta_connector_definitions` instead.

## Configuration and credentials

Everything lives in one config document, read with `await self.config_service.get_config(f"/services/connectors/{self.connector_id}/config")`:

| Key | Holds |
| --- | --- |
| `auth` | `authType` and the auth form fields (`apiToken`, `clientId`, ...) |
| `credentials` | `access_token`, `refresh_token` after OAuth |
| `filters.sync.values`, `filters.indexing.values` | the admin's filter choices |
| `sync` | the chosen strategy and schedule |

`use_cache` defaults to `False`; never pass `True` for a credentials path. Pass `raise_on_error=True` where a missing value would lead you to delete records or write empty grants: without it, a failed read returns the default and looks the same as "not set". A shared OAuth app is stored under `/services/oauth/<type>` and fetched with `BaseConnector._fetch_oauth_config_by_id`. Never write credentials anywhere else, and never log them.

## Entities

`models/entities.py`. Build these and hand them to the processor. Timestamps are epoch milliseconds.

**`Record`** (and its subclasses `FileRecord`, `MailRecord`, `WebpageRecord`, `LinkRecord`, `CommentRecord`, `TicketRecord`, `ProjectRecord`, `PullRequestRecord`, ...). Choose the subclass that matches the item; add to `RecordType` only if none fits.

| Field | Set it to |
| --- | --- |
| `external_record_id` | The source's stable id. Never a path or name that changes on rename. |
| `external_revision_id` | Something that changes exactly when content changes: an etag, version number, or `updated_at`. A content change is detected by this field. |
| `version` | `0` for a new record; the processor bumps it when the revision changes. |
| `record_name`, `record_type`, `origin=OriginTypes.CONNECTOR`, `connector_name=Connectors.ACME`, `connector_id` | Required. |
| `external_record_group_id`, `record_group_type` | The container it belongs to. |
| `parent_external_record_id`, `parent_record_type` | The record it nests under, if any. |
| `mime_type` | `MimeTypes.*`. Folders are `MimeTypes.FOLDER`; test for a folder with `in FOLDER_MIME_TYPES`. Structured content is `MimeTypes.BLOCKS`. |
| `weburl` | A link that opens the item in the source. Citations use it. |
| `source_created_at`, `source_updated_at` | From the source. |
| `inherit_permissions`, `inherit_permissions_from_group`, `access_rule`, `rewrite_permissions` | Permission fields. See `06-permissions.md`. |
| `signed_url`, `fetch_signed_url` | Only if the source gives a URL the indexing service can download from. |
| `related_external_records` | Links to other records: a list of `RelatedExternalRecord(external_record_id, record_type, relation_type=RecordRelations.X)` (or `weburl` / `issue_key` to point at a record by link or key). For a ticket, project, SQL table, SQL view, message or pull request, a successful write replaces that record's links, and an empty list is that replacement. `None` on a message means this save did not read links, so the stored links stay. Every other record type ignores the list. A missing target in the same connector becomes a placeholder. A failed association read is not an empty list: keep the stored links and mark the item failed. These edges do not grant access. If the source's id is unique only inside an object type, namespace it (`0-1:123` and `0-2:123`), or two objects collide. |
| `is_dependent_node`, `parent_node_id` | Set on a child that should hang off a parent record in the knowledge hub (Slack's files, links and burst children). `parent_node_id` is the parent's internal id, not the external id. |
| `shared_with_me_record_group_ids` | Extra record groups this record also belongs to, besides `external_record_group_id`. Use it when one item is in several containers and none of them is "the" parent. `shared_groups_complete` says the list is the full set, so groups left off it are dropped. |
| `root_record_group_id` | Required for connectors in `ROOT_SCOPED_CONNECTOR_TYPES` (`app/services/graph_db/common/utils.py`, Slack today). Search then filters by the root group instead of every nested group. Adding a connector to that set is shared code: ask first. |
| people fields | On `TicketRecord`: `assignee_email`, `reporter_email`, `creator_email`. On message records: `mentioned_user_ids`, `mentioned_group_ids`. The processor turns them into assigned-to, reported-by, created-by and mention edges. |
| `indexing_status` | Leave the default unless an indexing filter excludes the record (`AUTO_INDEX_OFF`). |

**`RecordGroup`**: `name`, `external_group_id`, `group_type` (`RecordGroupType`), `connector_name`, `connector_id`, `parent_external_group_id`, `web_url`, plus the permission fields `inherit_permissions` (defaults to `False`) and `access_rule`. `hide_children` hides the group's children in the knowledge-base tree. `permission_model = RECORD_GROUP_LEVEL` is only for a group whose every nested group and record has that group's audience (see `06-permissions.md`).

`record_type` on a record is `RecordType` in `app/models/entities.py`. `RecordTypes` in `app/config/constants/arangodb.py` is a second enum and the two have drifted. A new value is a change under `app/models/` or shared constants: ask first, and do not add it to only one of them. `RecordGroupType` is the same kind of change.

**`AppUser`**: `app_name`, `connector_id`, `source_user_id`, `email`, `full_name`, `is_active` (defaults to `False`; set `True` for active users), `is_guest` (set `True` for guests and external accounts, see `06-permissions.md`). Users are matched to PipesHub users by email.

**`AppUserGroup`**: `app_name`, `connector_id`, `source_user_group_id`, `name`.

**`Permission`** (`models/permission.py`): `external_id` or `email`, `type` (`READ`, `WRITE`, `OWNER`), `entity_type` (`USER`, `GROUP`, `ROLE`, `ORG`, `TEAM`, ...).

## `DataSourceEntitiesProcessor`

`connectors/core/base/data_processor/data_source_entities_processor.py`. The only way a connector writes to the graph. Write order matters: users and groups before the records that grant to them, parents before children where you can (the processor creates restricted placeholders for parents it has not seen, and fills them in later).

| Method | Use |
| --- | --- |
| `on_new_app_users(users)` | Source users. |
| `on_new_user_groups([(group, members)])`, `on_user_group_member_added/_removed`, `on_user_group_deleted` | Source groups and membership. |
| `on_new_app_roles(...)` | Source roles, for connectors with role-based access. |
| `on_new_record_groups([(group, permissions or None)])` | Containers. `None` keeps the stored grants (you could not read them); `[]` clears them. |
| `on_new_records([(record, permissions)], *, replace_permissions=False)` | New and changed records. Written in slices of 100, each in its own transaction; index events are published per committed slice. |
| `on_record_content_update(record, permissions=None)` | Content changed. Publishes `updateRecord`. |
| `on_pushed_record(record, *, expected, new, content_changed)` | Not for connectors: the write behind the knowledge-base push route. It claims the record against a concurrent push or a trash in the first statement of one explicit transaction, then upserts it. A claim that does not apply raises `PushClaimRefused`, and a record the upsert skips (one in the trash) raises `PushRecordSkipped`; both roll everything back. After the commit, a content change publishes `newRecord` (create) or `updateRecord`. Returns `PushOutcome(event, published)`; `published=False` (the producer raised or returned `False`) leaves the record `NOT_STARTED` with a fresh `queuedAt` for the stranded-record sweep. Not wrapped in `@retry_on_deadlock()`: the push service runs its own bounded retry loop (`MAX_ATTEMPTS`, `RETRY_BUDGET_S`), so do not add a second one. |
| `on_record_metadata_update(record)` | Rename, parent change, and a link-only change. It publishes no indexing event (`publishes_event=False`) and keeps the stored indexing status. Use it when associations changed and the content hash did not. A failed association read must not be sent as an empty `related_external_records` list: that replaces the stored links. |
| `on_updated_record_permissions(record, permissions)` | Access changed, content did not. Edges are keyed on `record.id`: pass the stored record from `get_record_by_external_id`, or set `record.id` to the stored id. A freshly built `Record` has a new id and the write goes nowhere. |
| `on_records_moved([(old_external_id, new_record, permissions)], permissions_fetched=...)` | Only for sources whose external id changes on a rename or move (path-keyed: Local FS, GitHub, GitLab). With a stable id, re-send the moved record through `on_new_records` with its new parent and group ids. |
| `delete_record_by_external_id(connector_id, external_id)` | A record gone at the source, by its source id. Attachments go with it; surviving children are re-pointed at what it hung under. |
| `on_record_deleted(record_id)` | Not the same delete: by the internal PipesHub id, it removes only that record and re-parents **every** child, attachments included (with soft delete on, it trashes only that record). For a record gone at the source, use `delete_record_by_external_id`, or a deleted page's files stay behind. |
| `on_records_deleted_cascade(record_ids, connector_id, cascade_children=True, ...)` | Deletes the records and, by default, their whole subtree. With `cascade_children=False` survivors are re-parented. Returns `success: False` on failure rather than raising. |
| `on_record_group_deleted(...)` | Deletes the group's whole subtree. |
| `get_record_by_external_id(...)`, `get_record_group_by_external_id(...)` | Look up what is stored, for incremental decisions. `get_record_by_external_id` returns a base `Record` (`Record.from_arango_base_record`), which cannot be queued for indexing. For the subclass (`FileRecord`, `WebpageRecord`, `TicketRecord`, …) call `get_typed_record_by_external_id` on the data entities processor. Both carry the `access_rule` the record was stored with, read by `read_access_rule` in `app/models/entities.py`: a missing `accessRule` reads as `OPEN`, and an unrecognised value reads as `RESTRICTED` and is logged, so it fails closed as the permission traversal does (`06-permissions.md`). Compare against that rule when deciding whether a restriction changed. |
| `get_records_in_record_group(connector_id, external_group_id, limit, after_key)` | Page this connector's stored records in one group. Pass the last record's id as `after_key`. An id-diff of that group uses this after a listing that finished with no error (`03-decisions.md`). `get_records_by_record_type` returns every record of one type in one list. It takes no `limit` or `after_key`. |
| `reindex_existing_records(records)` | Re-publish for indexing without changing the record. |

Retry on deadlock: the write methods are wrapped in `@retry_on_deadlock()` (`connectors/core/base/data_store/graph_data_store.py`). A deadlock, write conflict or other transient error re-runs the **whole method call** from the start in a new transaction, up to 6 attempts with backoff (about 11.5 s in all). In an explicit transaction a failed attempt has rolled back, so nothing it wrote is left; with `NEO4J_EXPLICIT_TRANSACTIONS=false` each statement commits on its own, so earlier writes of a failed attempt stay and are repeated by the retry. Calls into the processor must therefore be safe to repeat, and anything done outside the transaction (a lookup, a callback) can run more than once.

How `on_new_records` decides between insert and update:

- It matches on `(connector_id, external_record_id)`. A match is updated in place and keeps its PipesHub id.
- The match is a lookup and then an insert, and nothing in the graph keeps one record per external id. So when two paths can create the same record at once, such as the sync and `stream_record` creating a child the parent links to, give a record you create the id `new_record_id(connector_id, external_record_id)` (`connectors/utils/record_ids.py`) on every one of those paths, never `uuid4()`. With random ids both writers insert, and the next full sync unlinks the copy its lookup did not return.
- A changed `external_revision_id` re-queues the record for indexing, and bumps `version` if you left it at `0`. On an already indexed record this happens even when you set `AUTO_INDEX_OFF`.
- An unchanged record that is already indexed (`COMPLETED`) is not re-queued, so re-sending unchanged records in a full sync is cheap. An unchanged record not yet indexed is re-published as `newRecord`; `on_new_records` never sends `updateRecord`.
- A missing `weburl` or source timestamp is carried over from the stored record.
- Records that are `AUTO_INDEX_OFF`, internal or placeholders are stored but not sent for indexing.
- A file not yet stored (`FileRecord` with `is_file`) named as an Office lock file (`~$...`) or an operating system file (`Thumbs.db`, `desktop.ini`, `.DS_Store`, any case) is neither stored nor sent for indexing (`connectors/core/base/data_processor/system_files.py`). This holds for every write method, not only `on_new_records`. A record already stored under that external id is still updated, and a Knowledge Base upload is always stored.

## Sync points

`connectors/core/base/sync_point/sync_point.py`. One `SyncPoint(connector_id, org_id, SyncDataPointType.X, data_store_provider)` per kind of checkpoint (`USERS`, `GROUPS`, `RECORDS`, `RECORD_GROUPS`), created in `init()`. Take `org_id` from `self.data_entities_processor.org_id`, as Linear does.

- `read_sync_point(key)`, `update_sync_point(key, data)`, `delete_sync_point(key)`. Keys are stored as `{org}/{connector}/{type}/{key}`; build per-container keys with `generate_record_sync_point_key(record_type, entity_name, entity_id)`.
- Save a checkpoint only after the items before it are written. Use `FailedItems` so a failed item holds a time cutoff before it: `failed.add(item_modified_ms)` for each failure, then save `failed.checkpoint(max_seen_ms)`. `add` takes milliseconds only. An id cursor (a snowflake) is a field on the sync point; leave it at the last id fully written when a page fails.
- Take the next incremental cursor (a delta token, a `start_page_token`, an id cursor, the sync start time) **before** a full listing starts, and save it after the listing finishes. A cursor taken at the end skips every change made while the listing ran. OneDrive, Google Drive team and Dropbox do this.
- When the source rejects a cursor as expired or invalid (Gmail's 404 on an old history id, a Graph delta `410`), drop that container's checkpoint and re-list it in full, and notify the user. Never reset the cursor to "now", which skips the changes in between.
- Store times as UTC epoch milliseconds and send date filters to the source in UTC (or in the time zone the API says it expects).
- Pass `encrypt_fields=[...]` to `update_sync_point` for any secret kept in checkpoint data.
- A full sync deletes all of the connector's sync points before `run_sync()` starts.

## Filters

`connectors/core/registry/filters.py`.

- `FilterCategory.SYNC` filters decide what to fetch; apply them in the API query where the API allows it. `FilterCategory.INDEXING` filters decide what to index; apply them per record by setting `indexing_status`.
- Use the standard keys (`SyncFilterKey`, `IndexingFilterKey`) and `CommonFields` where they fit.
- Load them at the start of every `run_sync()`: `sync_filters, indexing_filters = await load_connector_filters(self.config_service, "<name>", self.connector_id, self.logger)`. Admins change them between runs.
- `DYNAMIC` options are served by your `get_filter_options`.
- Helpers: `extension_passes_filter`, `name_passes_filter`, `FilterCollection.get / get_value / is_enabled`.

## Sync flow

A `run_sync()` that fits the platform:

1. Load filters. Refresh the client's token.
2. Users: list and `on_new_app_users`. Groups and members: `on_new_user_groups`.
3. Containers: list and `on_new_record_groups` with their permissions, parents first.
4. For each container, read its checkpoint; list items changed since (or all, if none); map each to a `Record` plus permissions; write in batches with `on_new_records`; record failures in `FailedItems`; save the checkpoint.
5. Deletions and moves the source can report: apply them (`delete_record_by_external_id`; a moved record with a stable id is re-sent through `on_new_records`). Deletions it cannot report are handled by the full-sync cleanup (`01-high-level-design.md`).
6. Return normally only if the run did what it says. If part of the source was skipped after an error, `full_sync_coverage()` returns `complete=False`; if the run reads a date window, it returns `window_start_ms`; if nothing could run, raise.

Keep memory bounded: process page by page, write batch by batch, and do not hold the whole source in a list.

### Placeholders and filters

When a record names a parent the sync has not written (because it comes later, or a filter excluded it), the processor creates a restricted placeholder. Filling placeholders is the connector's job: after the main pass, list them with `get_placeholder_records(connector_id, record_group_id=None)`, fetch the real parents, and write them. Keep a placeholder when its fetch fails. Do not fetch parents from containers the admin's filters exclude. Jira Cloud ("placeholder sweep", with a depth cap) and Confluence Cloud are the references.

When an admin narrows a sync filter, the platform queues a full sync and its cleanup unlinks whatever that sync did not write. To delete out-of-scope records outright, use the `folder_scope` helpers (`app/connectors/core/registry/folder_scope.py`), as Azure Blob and GCS do. Changing an indexing filter queues nothing: records already left `AUTO_INDEX_OFF` stay unindexed until reindexed.

## Content: signed URL and streaming

The indexing service first tries `record.signed_url` (sent as `signedUrl` in the index event); if absent or failing, it calls the connector's internal stream route, which runs `stream_record()` inside `streaming_for_indexing()` (`connectors/core/base/connector/stream_purpose.py`). Users opening a record go through the public stream route to the same method. Stream; do not read a large file into memory.

`stream_content` (`app/utils/streaming.py`) GETs a URL and takes no headers and no token. Use it for a URL the indexing service can fetch by itself. A download that needs a bearer token is streamed from your own client, still in chunks. Do not put the token in the URL.

Do not set `record.signed_url` when the URL expires before indexing runs. Airtable attachment URLs expire; some CDNs expire sooner. Resolve a fresh URL inside `get_signed_url` or `stream_record` on each fetch. Notion does this.

Structured content is a `BlocksContainer` (`app/models/blocks.py`): `block_groups` and `blocks`, streamed as `MimeTypes.BLOCKS`. Hash the canonical blocks you would stream, not the raw payload, when the payload contains volatile fields (signed URLs, thumbnails, view counts). Include a comment signature when comments are folded into the parent, or a comment edit never re-indexes.

Content the source cannot export is `not_downloadable` (`stream_errors.py`): HTTP 422 from `stream_record`, which is what a person opening the item sees. Do not set `indexing_status` to `ProgressStatus.FILE_TYPE_NOT_SUPPORTED` to keep the record out of indexing. `_after_slice_committed` still publishes unless the status is `AUTO_INDEX_OFF`, the record is internal, the status is `COMPLETED` or `FAILED`, it is a knowledge-base folder, or it is a placeholder. A record the connector stores as `FAILED` (the Web connector's page that answered an error, with its reason) is therefore never sent to indexing, which would only fetch it again and replace that reason. Indexing writes `FILE_TYPE_NOT_SUPPORTED` itself when the mime or extension is outside its supported set, before it calls the connector. A 422 from the stream route is terminal, and the final attempt stores `FAILED`, so the record is not retried and a status set on the way in does not remain.
