# Tool effects (PA-5, reviewed list)

Every `@tool` under `backend/python/app/agents/actions` carries `Tag(key="effect", value="read" | "write")`
(`harness_core.tools.tags.EFFECT_TAG_KEY`). The durable kernel classifies a call from it
(`harness_core.kernel.durable.effects.classify_tool_effect`): a `read` call changes nothing anywhere, so
it runs with the batch's other reads and is re-executed after a crash; a `write` call gets its own
fenced intent commit, and a crash between intent and outcome suspends the run as `tool.unknown`
(REPORT 6.2.3). Legacy ignores the tag: the keyword tool index leaves it out of `search_tools` ranking.

**Evidence rule.** Each tool was classified by reading its code down to the API call it makes (the
data source under `app/sources/`, the SDK or Graph request builder, the SQL it runs) and the provider's
documented semantics of that endpoint. A tool name or its older `type` tag was never evidence. `read`
means no state changes anywhere: not in the external system (no send, post, upload, share link, "mark
as read", reaction, join, watch registration), and not in PipesHub storage, graph or artifact registry.
Anything that could not be traced to a concrete call, and anything uncertain, is `write` (fail closed).
POST endpoints count as reads only where the provider documents them as queries (Jira JQL search,
Graph `/search/query`, Notion search/query, Slack `search.messages`, Google `freeBusy`).

**Tests.** `tests/unit/harness/test_tool_effects.py`: every decorator has exactly one effect tag (RO-1,
a new untagged tool fails CI); this list equals the tags in the code (RO-3); the durable platform tools
have reviewed effects (RO-2).

Totals: 553 tools, 324 `read`, 229 `write`.

Reviewed exceptions worth knowing:
- `salesforce.soql_query` is tagged `write` although it only calls `GET /query`: the model writes the
  SOQL, and `FOR VIEW` / `FOR REFERENCE` clauses update last-viewed dates. Since P1b (C-3) the durable
  port classifies each call (see "Per-call classification" below); the tag stays the fail-closed default.
- Tagged `read` before, but they write: `slack.get_dm_history` (`conversations.open` creates the DM),
  `clickup.search_tasks` (creates and deletes a temporary view), `confluence.download_attachment`
  (stores the file in PipesHub storage), `dropbox.get_shared_link` (creates a shared link).
- Tagged `write` before, but they only read: `jira.get_create_issue_fields`,
  `jira_data_center.get_create_issue_fields`.
- SQL tools that run model-written statements without a code-enforced read-only guard are `write`
  (`redshift.execute_query`, `mariadb.execute_query`, the database sandbox).
- Broken today, classified fail-closed: `evernote.search_notes` and `evernote.get_user_info` call data
  source methods that do not exist (`write`, not reached).
- Dynamic tools (tagged where they are built, `app/agents/agent_loop/tool_loader.py`): `web_search`,
  `fetch_url`, `fetch_slack_thread`, `fetch_slack_nearby_messages` are `read` (the first two with an `external`
  sink, see "Where a read's request goes"; the Slack context tools with sink `none`: platform knowledge reads behind
  the record-access gate, on the indexed record's own connector, KN2); `execute_sql_query` is
  `write` (it saves a CSV of the result to conversation storage). Platform tools: `search_tools`,
  `list_toolsets`, `fetch_tools`, `final_answer`, `retrieve_artifact_content` and
  `knowledgegraph__fetch_record` (the dynamic `fetch_full_record`) are `read`. An MCP tool's class comes
  from its MCP annotations since FG2 (see "MCP tools" below); without them it stays `write` (REPORT 6.5). Skill tools (S5c, PA-17): `skills_list` (`SkillManager.list_skills`),
  `skill_search` (the skill index), `load_skill` (`activate_skill` without a session, so the usage
  tracker records nothing) and `load_skill_resource` (`get_resource`) are `read`; `skill_manage`
  (create, edit, patch, delete, deprecate, files) is `write`.
- `salesforce.soql_query` stayed `write` after the S5c review: whether a call writes depends on its
  model-written SOQL (`FOR VIEW`, `FOR REFERENCE`, `UPDATE TRACKING`, `UPDATE VIEWSTAT` update
  record metadata). C-3 (P1b) classifies it per call.
- Stated explicitly since P2.1 (P2.3c), each with the class it already had: every MCP tool was `write`
  (`MCPToolAdapter.tags`; changed by FG2, below); `run_code` and
  `install_packages` are `execute`, `read_sandbox_file` is `read` (it downloads a file from the
  sandbox and changes nothing); `skill_manage` is `write` (it had only `category=write`).
  `tests/unit/harness/test_effect_tags_complete.py` (ET-1) fails on any tool a durable run can reach
  without exactly one effect tag; domain-agent delegates are derived, not tagged (ET-6).

## Where a read's request goes (FG2)

A read changes nothing, but its request can still carry data out: a URL to fetch or a web search query holds
whatever the model put in it, and the host is chosen by the call. The `sink` tag says where a tool's arguments go
(`harness_core.tools.tags`); on a read it is `self` when untagged (the request goes to the system the tool reads, with
the user's own access) and `external` when the request carries what the model chose to a host outside the
organization. The flow rule holds an `external` call, read or write, once the run has read private data; before any
private read, or after public web pages only, it does not ask. Egress rules cannot do this:
they see the host, never the data in the URL.

PipesHub's own two web tools are the exception (`flow=platform_web`, `TAG_FLOW_PLATFORM_WEB`): they keep
`sink=external`, but the flow rule never holds them (`harness_core.effects.flow.flow_holds`), on the native loop,
today's loop and an engine's Tool Gateway alike, and the Rule of Two does not either. The page fetch also carries
`flow=opens_address` (`TAG_FLOW_OPENS_ADDRESS`): once a flow rule would hold a send, it opens only an address the run
was given (`app/web/addresses.py`: a search result, a recorded page or a link in one, a record's link, the person's
words) and any other is refused with `tool.denied{rule: web_address}`, never asked. No rule layer governs either web
tool (`PlatformWebRuns`). Their safety is in the tools:
secrets are redacted, private addresses and internal URLs are refused, and a page's text is labelled as
outsider-written. What they return still marks the run `untrusted_content_read`, so a later send is held. The tag
counts only on a `provider=platform` read; an approval layer's own `tools` rule for `web@<provider>/web_search` or
`web@<provider>/fetch_url` still asks.

| Tool | Sink | Evidence |
|---|---|---|
| `dynamic__fetch_url` (`app/web/tools/fetch_url.py`, #4300) | external | an unauthenticated GET of the URL the model passes, directly or through a configured page reader (Firecrawl, Crawl4AI); the path and query can hold anything. Its result is public content: `app/web/fetch/http.py` resolves every hop with `resolve_public_http_target`, and the readers and the headless browser check the URL and the page they land on with `check_public` |
| `dynamic__web_search` (`app/web/tools/web_search.py`, #4300) | external | the query goes to the configured search provider and, when it fails, the other stored ones (`app/web/search/`: DuckDuckGo, DDGS, Serper, Tavily, Exa, Brave, SearXNG, Firecrawl) |
| `onedrive.get_onenote_sections`, `outlook.get_meeting_transcripts`, `teams.get_my_meeting_transcripts`, `teams.get_people_attended` | self (reviewed) | they take a `web_url` / `join_url`, but it names an item in the org's own Microsoft 365 tenant and the request goes to Microsoft Graph, not to the URL's host |

Every other read is `self`. C-30 (`tests/cohesion/test_c30_read_tools.py`) keeps this list: a read that takes a
URL-like argument (`url`, `uri`, `link`, `href`, `endpoint`, `webhook`, `host`) must be tagged `sink=external` or
listed above with its reason, and the catalog's external reads must be exactly the two web tools.

## Where a platform write's arguments go (SINK1)

An untagged write is `external` (fail closed), so the flow rule held the built-in image generator after any read
("Blocked by a policy"). Every write of a `provider=platform` toolset now declares its sink; a new one without a
`sink` tag fails `test_sink1_every_platform_write_declares_where_its_arguments_go`
(`tests/unit/harness/test_ux1_sink_catalog.py`).

| Tool | Sink | Evidence |
|---|---|---|
| `artifacts.save_artifact`, `update_artifact`, `promote_artifact` | none | the conversation's own artifacts (UX1 CL-3) |
| `image_generator.generate_image` | self | the prompt (and, for an edit, the user's own artifact) goes only to the image model the org configured under AI Models (`get_image_generation_config`), the same trust as the chat model; the image is saved as an artifact of this conversation |
| `coding_sandbox.execute_python`, `execute_typescript` | external, `self` under docker/broker (SINK2) | under `SANDBOX_MODE=local` the code runs as a host subprocess with the host's network. `DockerExecutor` runs it with `network_mode="none"` and `BrokerExecutor` with `EgressDefault.DENY`; their install step reaches the index, but only with names `validate_packages` took from the curated allowlist (pip and npm fetch by name, so a version specifier is not sent), so the model's code and data never leave |
| `database_sandbox.execute_sqlite`, `execute_postgresql` | external, `self` under docker/broker (SINK2) | under `SANDBOX_MODE=local` `sqlite3`/`psql` run as host subprocesses whose shell escapes (`.shell`, `\!`) reach the network; docker and broker run them in the same offline container as code |

SINK2: the declared tag stays `external` (it is what the catalog and a run's descriptor pin digest); when a toolset is
loaded, `CodingSandbox`/`DatabaseSandbox.tools_for_request` (`app.sandbox.manager.with_executor_sink`) re-tag it `self`
where `SANDBOX_MODE` is `docker` or `broker`. Neither loop offers these two toolsets today (`factory.py` and the durable
`tool_sources.py` skip them; code runs through `run_code`), so this is the posture any future loader gets.

The same rule holds on `run_code`, the tool both loops do offer (BUGS-B): its `sink=none` needs the backend to take
the network away as well as the run's network to be off. `CodingSandboxTool.tags` asks the registered backend
(`SandboxManager.reaches_network`, its `capabilities().supports_network`): docker, broker and E2B with network off
report false and keep `none`; the local backend is a host subprocess with the host's network and always reports true,
so under `SANDBOX_MODE=local` `run_code` is `external`: the flow rule holds it after a private read and a class `ask`
is not waived as own-sandbox code. Before, the tag read only `SANDBOX_ALLOW_NETWORK`, so local code ran unasked.

## What a platform tool's result tells the ledger (FLOWTRUST)

The flow rule decides from what the run has read (`SessionSourceLedger`, `app/harness/labels.py`): a knowledge tool's
records make it private, web pages and the loop's own machinery add nothing, and any other result is content nobody
can name, which makes the run `unknown` and ranks above `private` (fail closed). That held every web search after the
built-in image generator: in one test run six `web_search`/`fetch_url` calls asked under
`flow_external_write` only because an earlier `generate_image` result counted as `unknown` (decision D3, keep
asking before data leaves; stop counting the platform's own tools as unknown).

A platform tool whose result is computed by PipesHub's code from the call's own arguments declares it with
`TAG_OUTPUT_DERIVED` (`output=derived`, `harness_core.tools.tags`) in its `@tool` registration. The ledger counts its
result as no new content: the run keeps the label of what it read before the call, which is where the arguments came
from, so a prompt built from a private record leaves the run private and a later search still asks. The ledger honours
the tag only on a platform tool id (`pipeshub@<toolset>/<tool>`, which only `ToolsetSource` assigns), looked up in the
release's toolset registry (`labels.derived_output`); an MCP, connector or web tool of the same name stays `unknown`.
Today's loop honours it with `provider=platform` (`hooks/flow_guard.py`).

| Tool | Evidence |
|---|---|
| `calculator.calculate_single_operand`, `calculate_two_operands`, `evaluate_expression`, `date_difference`, `count_text` | pure arithmetic and counting over the arguments; no I/O |
| `date_calculator.get_exclusion_dates`, `list_weekend_dates`, `parse_holiday_dates` | date arithmetic and parsing over the arguments; no I/O |
| `image_generator.generate_image` | the model reads back an acknowledgement built from the arguments (file name, size, count, provider and model names), never the image; for an edit, also the `record_id` it was given and that artifact's file name |

Not derived, so still `unknown`: `run_code` and the sandbox tools (code may read the workspace or reach the network),
connector actions and MCP tools. `test_flowtrust_first_party_labels.py` pins the list: a new derived tool is a review.

## Code in the run's own sandbox does not ask (CODEAUTO)

Decision: code that runs in the run's own sandbox needs no approval; the agent shows it as activity instead. A call whose effect is `execute` and whose sink is `none`
(`harness_core.effects.policy.in_own_sandbox`) runs code in the run's own sandbox with no network: nothing it does
reaches anyone outside the run. A layer's class rule no longer asks about it (`ApprovalRules.decide`): the `writes`
switch, `classes.execute`, an engine profile's `approvals.execute`, and the fail-closed `writes: ask` of an
unreadable layer. The timeline shows the call as the tool row it always had (what ran, exit status, files); no card.

Before, the org rule `writes: ask` (which the e2e and human stacks seed, `deployment/e2e/seed_config.py`) asked
before every `run_code`: the human stack journaled `effects.default_own_space ... toolId=pipeshub@sandbox/run_code`
(the engine default had already let it through, UX4) and then `tool.approval_explained rule=writes` from the org
layer.

| Call | Effect, sink | Under `writes: ask` |
|---|---|---|
| `run_code` without network (`SANDBOX_ALLOW_NETWORK` off, a docker, broker or E2B backend) | execute, `none` (tagged `sink=none`) | runs, `allowedBy: own_sandbox` |
| `run_code` on the local backend | execute, `external` (the host's network; SINK2) | asks |
| `read_sandbox_file` | read, `self` | runs (a read; never asked) |
| `run_code` with network | execute, `external` (no sink tag) | asks |
| `install_packages` | execute, `external` (no sink tag) | asks: it exists to reach the package registry. Kept fail-closed on purpose (package installs that reach the network keep asking); `run_code`'s own `packages` argument installs only names from the curated allowlist (SINK2), which is why `run_code` stays `none` |
| an engine's own shell and file actions | never reach the policy: native permission modes (`approvalPolicy: never`, `dontAsk`, opencode rules) run the profile's tools, and an engine's own ask is refused, never a card (`test_ct15`) | no card |
| an engine's gateway call to `run_code` | as the loop's `run_code` | runs, `allowedBy: own_sandbox` |
| a write of any sink, an MCP or connector call, a send | write or execute, any sink but an offline execute | asks |
| a call the flow rule holds (`flow_external_write`) | an external sink after a private read | asks |

What still asks or refuses about own-sandbox code: an admin's **tool rule** (`tools: {"pipeshub@sandbox/run_code":
"ask"}` in the org's or an agent's approval rules; it is the org's own rule and no conversation grant answers it),
and any **deny** (`writes: deny`, `classes.execute: deny`, a tool's deny). A class's `ask` yields; its `deny` does not.

The Approval policy page's **"Ask before running code"** switch (ASKCODE), for the organization and for each agent
with its own rule, is that tool rule: on stores `tools["pipeshub@sandbox/run_code"] = "ask"` in the layer, off removes
it, and nothing else is stored, so the API and the page edit one rule. It is a tool rule rather than a class rule
because `ApprovalRules.decide` lets a class's `ask` yield to `in_own_sandbox` by design; only the tool's own rule
reaches offline `run_code`. The rule holds the loop's `run_code`, an engine's gateway call to it, and a pinned run
(the lifted `approvals` ceiling keeps `tools`). A `deny` for the tool set through the API shows the switch on and
locked ("Code runs are blocked by a rule set through the API") and the page never overwrites it. An engine's own
shell and file actions inside its container never reach the policy, with the switch on or off; the switch's info
tooltip says so. Tests: `frontend/app/(main)/workspace/approval-policy/__tests__/approval-policy.test.tsx`
(AC-5..AC-8), `tests/unit/harness/test_askcode_switch.py` (AC-1..AC-4: loop, engine gateway, pinned ceiling, agent).

The journal records the decision: the call's `tool.intent` carries `allowedBy: "own_sandbox"` when the policy let it
run without a person, written only when the claim's Node names feature `intent_allowed_by` (an older Node gets the
intent as before), and the worker logs `tool.allowed rule=own_sandbox` (the audit line). A call a person approved
carries no `allowedBy`. Tests: `tests/unit/harness_core/effects/test_codeauto_own_sandbox.py` (CA-1..CA-12: class
rules, what still asks, deny, the flow rule, engine default, loop and engine kernels, an older Node, a person's
approval, replay), `tests/unit/harness/test_codeauto_admin_override.py` (CA-13..CA-15: the real sandbox tools' tags
and the approvals document).

## MCP tools (FG2)

An MCP tool's effect and sink come from the MCP spec's own annotations (`harness_core.mcp_hub.annotations`, decided
2026-10-10), the same on today's loop, the durable loop and engine runs:

| Annotations the server declares | Effect | Sink |
|---|---|---|
| none (or none that are booleans) | write | external (fail closed, REPORT 6.5) |
| `readOnlyHint: true` | read | external (no `openWorldHint` is open world, as the spec says: SEC-B-02, 2026-11-13) |
| `readOnlyHint: true`, `openWorldHint: false` | read | self |
| `readOnlyHint: true`, `openWorldHint: true` | read | external (held after a private read) |
| `openWorldHint: false`, not read-only | write | self |
| anything else | write | external |

A hint is the server's claim about itself and is trusted as far as the admin who installed and approved the server.
The hints are part of the tool's descriptor digest (`ToolDescriptor.hints`, "annotations" in the canonical form; a tool
with none digests as before), shown on the manifest review, and stored with the approval: a server that flips a hint is
`mcp_tool_changed` until approved again, and a run pinned to the old digest loses it. An org's approval rule for the
tool or its class (`tools: {<toolId>: ask|deny}`, `classes: {read: ask}`) applies over the hint on the durable loop and
engine runs; today's loop applies only flow rules, so there the hint and its approval are the bound.

## Per-call classification (C-3)

A tool whose effect depends on its arguments has a classifier in
`app/harness/assembly/call_effects.py` (`CALL_EFFECTS`, keyed by the `@tool` path). The durable tool
port (`RegistryToolPort.describe_call`, `harness_core`) asks it for each call before the kernel plans
the call; the tool's static tag is what the call gets when the tool has no classifier, when the
classifier raises (logged `tool.call_effect_failed`, warning, `toolId` and `errorType` only) and for
`describe(name)`. A read call is journaled with its outcome and re-run after a crash; a write call
commits its intent first, as before. Legacy is unchanged (it ignores effects).

| Tool | Static tag | Per call | Evidence |
|---|---|---|---|
| `/tools/salesforce/soql_query` | write | `read` only when the `query` argument is a string that, with its single-quoted literals blanked (backslash escapes honoured), starts with `SELECT`, has no `;`, and names none of `INSERT`, `UPDATE`, `DELETE`, `UPSERT`, `MERGE`, `UNDELETE`, `FOR VIEW`, `FOR REFERENCE` (so `FOR UPDATE`, `UPDATE TRACKING` and `UPDATE VIEWSTAT` are writes too). Anything else is `write`: a missing or non-string argument, an unterminated literal, SOSL, several statements | `SalesforceDataSource.soql_query` is `GET /services/data/vXX/query?q=…` and nothing else. The REST query resource runs SOQL only (DML is refused), and a plain SELECT changes no state. The clauses that do change state: `FOR VIEW` and `FOR REFERENCE` update `LastViewedDate` / `LastReferencedDate`; `UPDATE TRACKING` and `UPDATE VIEWSTAT` update Knowledge article statistics; `FOR UPDATE` locks the rows. Keywords inside string literals do not run, so they are ignored; a statement the scan cannot read reliably fails closed. Tests: `tests/unit/harness/test_call_effects.py` (8 reads, 15 writes, non-string arguments, the real tool's path and tag, the run port end to end) and `tests/unit/harness_core/durable/test_call_effects.py` (port and kernel: a read call's intent rides with its outcome, a write commits it first; a failing classifier stays `write`) |


## `airtable/airtable.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/airtable/create_records` | write | AirtableDataSource.create_records (POST records) |
| `/tools/airtable/get_record` | read | AirtableDataSource.get_record: HTTP GET /{base}/{table}/{record} |
| `/tools/airtable/list_records` | read | AirtableDataSource.list_records: HTTP GET /{base}/{table} |
| `/tools/airtable/update_records` | write | AirtableDataSource.update_records (PATCH records) |
| `/tools/airtable/delete_records` | write | AirtableDataSource.delete_records |
| `/tools/airtable/search_records` | read | AirtableDataSource.list_records with filterByFormula: HTTP GET |

## `artifacts/artifacts.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/artifacts/save_artifact` | write | ArtifactRegistryService.register_output - writes blob + graph artifact record/version |
| `/tools/artifacts/update_artifact` | write | ArtifactRegistryService.add_version - writes new blob version + graph update |
| `/tools/artifacts/get_artifact_download_url` | read | registry.get_download_url: graph get_document/get_edge auth + Node storage GET for signed URL (version=None, no recovery write path) |
| `/tools/artifacts/get_artifact_content` | read | registry.resolve + registry.get_content: graph reads + storage GET download (version=None) |
| `/tools/artifacts/get_record_download_url` | read | graph get_record_by_id + BlobStorage.get_download_url (Node storage GET) |
| `/tools/artifacts/list_artifacts` | read | registry.list_for_conversation: graph get_documents_paginated + authorize_read + lineage edge reads |
| `/tools/artifacts/promote_artifact` | write | ArtifactRegistryService.promote_to_visible - updates artifact visibility in graph |

## `azure/azure_blob.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/azure_blob/create_container` | write | Azure SDK ContainerClient.create_container |
| `/tools/azure_blob/get_container` | read | Azure SDK ContainerClient.get_container_properties |
| `/tools/azure_blob/delete_container` | write | Azure SDK ContainerClient.delete_container |
| `/tools/azure_blob/upload_blob` | write | Azure SDK BlobClient.upload_blob |
| `/tools/azure_blob/get_blob` | read | Azure SDK BlobClient.get_blob_properties |
| `/tools/azure_blob/delete_blob` | write | Azure SDK BlobClient.delete_blob |
| `/tools/azure_blob/search_blobs_by_tags` | read | Azure SDK BlobServiceClient.find_blobs_by_tags |

## `bookstack/bookstack.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/bookstack/create_page` | write | BookStackDataSource.create_page (POST /api/pages) |
| `/tools/bookstack/get_page` | read | BookStackDataSource.get_page: HTTP GET /api/pages/{id} |
| `/tools/bookstack/update_page` | write | BookStackDataSource.update_page (PUT /api/pages/{id}) |
| `/tools/bookstack/delete_page` | write | BookStackDataSource.delete_page (DELETE) |
| `/tools/bookstack/search_all` | read | BookStackDataSource.search_all: HTTP GET /api/search |

## `box/box.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/box/get_file` | read | BoxDataSource.files_get_file_by_id -> SDK files.get_file_by_id (GET /2.0/files/{id}) |
| `/tools/box/update_file` | write | BoxDataSource.files_update_file_by_id (PUT /2.0/files/{id}) |
| `/tools/box/delete_file` | write | BoxDataSource.files_delete_file_by_id (DELETE) |
| `/tools/box/upload_file` | write | BoxDataSource.uploads_upload_file (upload) |
| `/tools/box/search_content` | read | BoxDataSource.search_search_for_content -> SDK search.search_for_content (GET /2.0/search) |

## `calculator/calculator.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/calculator/calculate_single_operand` | read | Pure local math (sqrt/cbrt), no eval/I/O |
| `/tools/calculator/calculate_two_operands` | read | Pure local arithmetic, no eval/I/O |
| `/tools/calculator/count_text` | read | Pure local letter/word counting of input text, no I/O |
| `/tools/calculator/date_difference` | read | Pure local datetime arithmetic, no I/O |
| `/tools/calculator/evaluate_expression` | read | AST-walked arithmetic (`calculator/exact.py`), no eval/I/O |

## `calculator/date_calculator.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/date_calculator/get_exclusion_dates` | read | Pure local datetime computation, no I/O |
| `/tools/date_calculator/list_weekend_dates` | read | Pure local datetime computation, no I/O |
| `/tools/date_calculator/parse_holiday_dates` | read | Local regex/datetime parsing of input text, no I/O |

## `clickup/clickup.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/clickup/get_authorized_user` | read | GET /api/v2/user |
| `/tools/clickup/get_authorized_teams_workspaces` | read | GET /api/v2/team |
| `/tools/clickup/get_spaces` | read | GET /team/{team_id}/space |
| `/tools/clickup/get_folders` | read | GET /space/{space_id}/folder |
| `/tools/clickup/get_lists` | read | GET /folder/{folder_id}/list |
| `/tools/clickup/get_folderless_lists` | read | GET /space/{space_id}/list |
| `/tools/clickup/create_space` | write | POST /team/{team_id}/space |
| `/tools/clickup/create_folder` | write | POST /space/{space_id}/folder |
| `/tools/clickup/create_list` | write | POST /folder/{folder_id}/list or POST /space/{space_id}/list |
| `/tools/clickup/update_list` | write | PUT /list/{list_id} |
| `/tools/clickup/get_tasks` | read | GET /team/{team_id}/task |
| `/tools/clickup/search_tasks` | write | POST /team/{team_id}/view (creates temp view), GET /view/{id}/task, DELETE /view/{id}; creates/deletes a workspace view (leaks it if delete fails) |
| `/tools/clickup/get_task` | read | GET /task/{task_id} |
| `/tools/clickup/create_task` | write | POST /list/{list_id}/task |
| `/tools/clickup/update_task` | write | PUT /task/{task_id} |
| `/tools/clickup/get_comments` | read | GET /comment/{comment_id}/reply or GET /task/{task_id}/comment |
| `/tools/clickup/create_task_comment` | write | POST /comment/{comment_id}/reply or POST /task/{task_id}/comment |
| `/tools/clickup/create_checklist` | write | POST /task/{task_id}/checklist |
| `/tools/clickup/create_checklist_item` | write | POST /checklist/{checklist_id}/checklist_item |
| `/tools/clickup/update_checklist_item` | write | PUT /checklist/{checklist_id}/checklist_item/{item_id} |
| `/tools/clickup/get_workspace_docs` | read | GET v3 /workspaces/{workspace_id}/docs |
| `/tools/clickup/get_doc_pages` | read | GET v3 /workspaces/{workspace_id}/docs/{doc_id}/pages |
| `/tools/clickup/get_doc_page` | read | GET v3 /workspaces/{workspace_id}/docs/{doc_id}/pages/{page_id} |
| `/tools/clickup/create_doc` | write | POST v3 /workspaces/{workspace_id}/docs |
| `/tools/clickup/create_doc_page` | write | POST v3 /workspaces/{workspace_id}/docs/{doc_id}/pages |
| `/tools/clickup/update_doc_page` | write | PUT v3 /workspaces/{workspace_id}/docs/{doc_id}/pages/{page_id} |

## `code_graph/code_graph.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/codegraph/query_code_graph` | read | main #3841: graph_provider reads of the code graph (codeFiles, symbols, edges) and the blob store's stored file text, each hop checked against the caller's access; writes nothing |
| `/tools/codegraph/get_neighbour` | read | main #3841: graph_provider reads of the code graph (codeFiles, symbols, edges) and the blob store's stored file text, each hop checked against the caller's access; writes nothing |
| `/tools/codegraph/read_code` | read | main #3841: graph_provider reads of the code graph (codeFiles, symbols, edges) and the blob store's stored file text, each hop checked against the caller's access; writes nothing |
| `/tools/codegraph/find_symbol_path` | read | main #3841: graph_provider reads of the code graph (codeFiles, symbols, edges) and the blob store's stored file text, each hop checked against the caller's access; writes nothing |
| `/tools/codegraph/search_code` | read | main #3841: graph_provider reads of the code graph (codeFiles, symbols, edges) and the blob store's stored file text, each hop checked against the caller's access; writes nothing |
| `/tools/codegraph/outline` | read | main #3841: graph_provider reads of the code graph (codeFiles, symbols, edges) and the blob store's stored file text, each hop checked against the caller's access; writes nothing |
| `/tools/codegraph/impact` | read | main #3841: graph_provider reads of the code graph (codeFiles, symbols, edges) and the blob store's stored file text, each hop checked against the caller's access; writes nothing |
| `/tools/codegraph/entry_points` | read | main #3841: graph_provider reads of the code graph (codeFiles, symbols, edges) and the blob store's stored file text, each hop checked against the caller's access; writes nothing |
| `/tools/codegraph/search_semantic` | read | main: graph_provider reads of the code graph's symbols ranked by meaning, each checked against the caller's access; writes nothing |
| `/tools/codegraph/impact_of_diff` | read | main #5561: graph_provider reads of the code graph (the diff's files, their symbols and callers), each checked against the caller's access; writes nothing |
| `/tools/codegraph/history` | read | main #5561: graph_provider reads of the pull requests that MODIFIES a file and the tickets they link, each checked against the caller's access; writes nothing |

## `coding_sandbox/coding_sandbox.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/coding_sandbox/execute_python` | write | Executes code in sandbox; uploads produced artifacts to blob (upload_artifacts_to_blob) |
| `/tools/coding_sandbox/execute_typescript` | write | Executes code in sandbox; uploads produced artifacts to blob (upload_artifacts_to_blob) |

## `confluence/confluence.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/confluence/create_page` | write | POST /wiki/api/v2/pages |
| `/tools/confluence/get_page_content` | read | GET /pages/{id}, /footer-comments, /inline-comments, /attachments, /spaces; POST /users-bulk (read-only bulk user lookup) |
| `/tools/confluence/list_page_attachments` | read | GET /wiki/api/v2/pages/{id}/attachments |
| `/tools/confluence/get_attachment_metadata` | read | GET /wiki/api/v2/attachments/{id} |
| `/tools/confluence/download_attachment` | write | GET attachment + download from Confluence, then registry.register() writes an artifact into PipesHub storage |
| `/tools/confluence/get_pages_in_space` | read | GET /wiki/api/v2/spaces/{id}/pages (+ GET /spaces) |
| `/tools/confluence/update_page_title` | write | PUT /wiki/api/v2/pages/{id}/title |
| `/tools/confluence/get_child_pages` | read | GET /wiki/api/v2/pages/{id}/children, GET /pages/{id}, GET /spaces |
| `/tools/confluence/search_pages` | read | GET /wiki/rest/api/search (CQL) |
| `/tools/confluence/search_content` | read | GET /wiki/rest/api/search (CQL full text) |
| `/tools/confluence/search_users` | read | GET /wiki/rest/api/search/user; GET user by key |
| `/tools/confluence/get_spaces` | read | GET /wiki/api/v2/spaces |
| `/tools/confluence/get_space` | read | GET /wiki/api/v2/spaces/{id} |
| `/tools/confluence/update_page` | write | PUT /wiki/api/v2/pages/{id} |
| `/tools/confluence/get_page_versions` | read | GET /wiki/api/v2/pages/{id}/versions |
| `/tools/confluence/comment_on_page` | write | POST /wiki/api/v2/footer-comments |
| `/tools/confluence/add_attachments` | write | main: POST /wiki/rest/api/content/{id}/child/attachment (files the user shared), and with inline display PUT /wiki/api/v2/pages/{id} |

## `confluence_data_center/confluence_data_center.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/confluence_data_center/validate_connection` | read | GET /rest/api/user/current |
| `/tools/confluence_data_center/get_current_user` | read | GET /rest/api/user/current |
| `/tools/confluence_data_center/get_spaces` | read | GET /rest/api/space |
| `/tools/confluence_data_center/get_space` | read | GET /rest/api/space |
| `/tools/confluence_data_center/get_pages_in_space` | read | GET /rest/api/content/search |
| `/tools/confluence_data_center/get_page_content` | read | GET /rest/api/content/{id} |
| `/tools/confluence_data_center/create_page` | write | POST /rest/api/content |
| `/tools/confluence_data_center/update_page` | write | PUT /rest/api/content/{id} |
| `/tools/confluence_data_center/get_child_pages` | read | GET /rest/api/content/{id}/child/page |
| `/tools/confluence_data_center/get_page_versions` | read | GET /rest/experimental/content/{id}/version |
| `/tools/confluence_data_center/add_comment` | write | POST /rest/api/content (comment via create_content_v1) |
| `/tools/confluence_data_center/get_comments` | read | GET /rest/api/content/{id}/child/comment |
| `/tools/confluence_data_center/search_pages` | read | GET /rest/api/search (CQL) |
| `/tools/confluence_data_center/search_content` | read | GET /rest/api/search (CQL full text) |
| `/tools/confluence_data_center/search_users` | read | GET /rest/api/search?cql=type=user; GET user by key/username |
| `/tools/confluence_data_center/add_attachments` | write | main: POST /rest/api/content/{id}/child/attachment (files the user shared), and with inline display PUT /rest/api/content/{id} |

## `database_sandbox/database_sandbox.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/database_sandbox/execute_sqlite` | write | Runs model-written SQL in sandbox executor; schedules CSV export to blob store |
| `/tools/database_sandbox/execute_postgresql` | write | Runs arbitrary SQL against SANDBOX_PG_URL (no read-only guard); schedules CSV export to blob store |

## `discord/discord.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/discord/send_message` | write | POST /channels/{channel_id}/messages |
| `/tools/discord/get_channel` | read | GET /channels/{channel_id} |
| `/tools/discord/create_channel` | write | POST /guilds/{guild_id}/channels |
| `/tools/discord/delete_channel` | write | DELETE /channels/{channel_id} |
| `/tools/discord/get_messages` | read | GET /channels/{channel_id}/messages |
| `/tools/discord/get_guilds` | read | GET /users/@me/guilds |
| `/tools/discord/get_guild_channels` | read | GET /guilds/{guild_id}/channels |
| `/tools/discord/send_direct_message` | write | POST /users/@me/channels (open DM) + POST /channels/{id}/messages |
| `/tools/discord/get_guild_members` | read | GET /guilds/{guild_id}/members |
| `/tools/discord/create_role` | write | POST /guilds/{guild_id}/roles |

## `dropbox/dropbox.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/dropbox/get_account_info` | read | SDK users_get_current_account -> /2/users/get_current_account |
| `/tools/dropbox/list_folder` | read | SDK files_list_folder -> /2/files/list_folder |
| `/tools/dropbox/get_metadata` | read | SDK files_get_metadata -> /2/files/get_metadata |
| `/tools/dropbox/download_file` | read | SDK files_download -> /2/files/download (content returned, nothing stored) |
| `/tools/dropbox/upload_file` | write | SDK files_upload -> /2/files/upload |
| `/tools/dropbox/delete_file` | write | SDK files_delete_v2 -> /2/files/delete_v2 |
| `/tools/dropbox/create_folder` | write | SDK files_create_folder_v2 -> /2/files/create_folder_v2 |
| `/tools/dropbox/search` | read | SDK files_search_v2 -> /2/files/search_v2 |
| `/tools/dropbox/get_shared_link` | write | SDK sharing_create_shared_link_with_settings -> /2/sharing/create_shared_link_with_settings (creates a public link) |

## `evernote/evernote.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/evernote/create_note` | write | Thrift note_store.createNote |
| `/tools/evernote/get_note` | read | Thrift note_store.getNote (tool kwargs don't match datasource signature; only call reachable is getNote) |
| `/tools/evernote/update_note` | write | Thrift note_store.updateNote |
| `/tools/evernote/delete_note` | write | Thrift note_store.deleteNote |
| `/tools/evernote/create_notebook` | write | Thrift note_store.createNotebook |
| `/tools/evernote/get_notebook` | read | Thrift note_store.getNotebook |
| `/tools/evernote/update_notebook` | write | Thrift note_store.updateNotebook |
| `/tools/evernote/get_default_notebook` | read | Thrift note_store.getDefaultNotebook |
| `/tools/evernote/search_notes` | write | UNTRACEABLE: calls EvernoteDataSource.search_notes, which does not exist (no __getattr__); tool always raises AttributeError. Fail closed. |
| `/tools/evernote/get_user_info` | write | UNTRACEABLE: calls EvernoteDataSource.get_user_info, which does not exist (datasource has get_user); tool always raises AttributeError. Fail closed. |

## `freshdesk/freshdesk.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/freshdesk/create_ticket` | write | Freshdesk POST /api/v2/tickets |
| `/tools/freshdesk/get_ticket` | read | Freshdesk GET /api/v2/tickets/{id} |
| `/tools/freshdesk/update_ticket` | write | Freshdesk PUT /api/v2/tickets/{id} |
| `/tools/freshdesk/delete_ticket` | write | Freshdesk DELETE /api/v2/tickets/{id} |
| `/tools/freshdesk/create_note` | write | Freshdesk POST /api/v2/tickets/{id}/notes |
| `/tools/freshdesk/create_reply` | write | Freshdesk POST /api/v2/tickets/{id}/reply (emails requester) |
| `/tools/freshdesk/create_agent` | write | Freshdesk POST /api/v2/agents |
| `/tools/freshdesk/search_tickets` | read | Freshdesk GET /api/v2/search/tickets |

## `github/github.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/github/create_repository` | write | PyGithub create_repo -> POST /user/repos |
| `/tools/github/get_repository` | read | PyGithub get_repo -> GET /repos/{owner}/{repo} |
| `/tools/github/get_owner` | read | PyGithub get_user/get_organization -> GET /user, /users/{u}, /orgs/{o} |
| `/tools/github/list_repositories` | read | PyGithub get_user().get_repos -> GET /user/repos or /users/{u}/repos |
| `/tools/github/create_issue` | write | PyGithub create_issue -> POST /repos/{o}/{r}/issues |
| `/tools/github/get_issue` | read | PyGithub get_issue -> GET /repos/{o}/{r}/issues/{n} |
| `/tools/github/list_issues` | read | PyGithub get_issues -> GET /repos/{o}/{r}/issues |
| `/tools/github/close_issue` | write | PyGithub issue.edit(state=closed) -> PATCH /repos/{o}/{r}/issues/{n} |
| `/tools/github/update_issue` | write | PyGithub issue.edit -> PATCH /repos/{o}/{r}/issues/{n} |
| `/tools/github/list_issue_comments` | read | PyGithub get_comments -> GET /repos/{o}/{r}/issues/{n}/comments |
| `/tools/github/get_issue_comment` | read | PyGithub get_comment -> GET /repos/{o}/{r}/issues/comments/{id} |
| `/tools/github/create_issue_comment` | write | PyGithub create_comment -> POST /repos/{o}/{r}/issues/{n}/comments |
| `/tools/github/create_pull_request` | write | PyGithub create_pull -> POST /repos/{o}/{r}/pulls |
| `/tools/github/get_pull_request` | read | PyGithub get_pull + issue get_comments -> GET /repos/{o}/{r}/pulls/{n}, /issues/{n}/comments |
| `/tools/github/get_pull_request_commits` | read | PyGithub get_commits -> GET /repos/{o}/{r}/pulls/{n}/commits |
| `/tools/github/get_pull_request_file_changes` | read | PyGithub get_files -> GET /repos/{o}/{r}/pulls/{n}/files |
| `/tools/github/list_pull_requests` | read | PyGithub get_pulls -> GET /repos/{o}/{r}/pulls |
| `/tools/github/merge_pull_request` | write | PyGithub merge -> PUT /repos/{o}/{r}/pulls/{n}/merge |
| `/tools/github/get_pull_request_reviews` | read | PyGithub get_reviews -> GET /repos/{o}/{r}/pulls/{n}/reviews |
| `/tools/github/create_pull_request_review` | write | PyGithub create_review -> POST /repos/{o}/{r}/pulls/{n}/reviews |
| `/tools/github/list_pull_request_comments` | read | PyGithub get_review_comments -> GET /repos/{o}/{r}/pulls/{n}/comments |
| `/tools/github/create_pull_request_review_comment` | write | PyGithub create_review_comment -> POST /repos/{o}/{r}/pulls/{n}/comments |
| `/tools/github/search_repositories` | read | PyGithub search_repositories -> GET /search/repositories |

## `gitlab/gitlab.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/gitlab/create_project` | write | python-gitlab projects.create -> POST /projects |
| `/tools/gitlab/get_project` | read | python-gitlab projects.get -> GET /projects/:id |
| `/tools/gitlab/update_project` | write | python-gitlab project.save() -> PUT /projects/:id |
| `/tools/gitlab/delete_project` | write | python-gitlab project.delete() -> DELETE /projects/:id |
| `/tools/gitlab/create_issue` | write | python-gitlab issues.create -> POST /projects/:id/issues |
| `/tools/gitlab/get_issue` | read | python-gitlab issues.get -> GET /projects/:id/issues/:iid |
| `/tools/gitlab/update_issue` | write | python-gitlab issue.save() -> PUT /projects/:id/issues/:iid |
| `/tools/gitlab/delete_issue` | write | python-gitlab issue delete -> DELETE /projects/:id/issues/:iid |
| `/tools/gitlab/create_merge_request` | write | python-gitlab mergerequests.create -> POST /projects/:id/merge_requests |
| `/tools/gitlab/get_merge_request` | read | python-gitlab mergerequests.get -> GET /projects/:id/merge_requests/:iid |
| `/tools/gitlab/merge_merge_request` | write | python-gitlab mr.merge() -> PUT /projects/:id/merge_requests/:iid/merge |
| `/tools/gitlab/search_projects` | read | python-gitlab projects.list(search=) -> GET /projects?search= |

## `google/calendar/calendar.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/calendar/get_calendar_events` | read | Calendar events().list (GET) |
| `/tools/calendar/create_calendar_event` | write | Calendar events().insert (POST) |
| `/tools/calendar/update_calendar_event` | write | Calendar events().get then events().update (PUT) |
| `/tools/calendar/create_meet_link` | write | Calendar events().insert with conferenceData (POST) - creates an event |
| `/tools/calendar/delete_calendar_event` | write | Calendar events().delete (DELETE) |
| `/tools/calendar/get_calendar_list` | read | Calendar calendarList().list (GET) |
| `/tools/calendar/get_calendar_list_by_id` | read | Calendar calendars().get (GET) |

## `google/docs/docs.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/docs/get_document` | read | Docs documents().get (HTTP GET) |
| `/tools/docs/create_document` | write | Docs documents().create |
| `/tools/docs/batch_update_document` | write | Docs documents().batchUpdate |
| `/tools/docs/insert_text` | write | Docs documents().batchUpdate insertText |
| `/tools/docs/replace_text` | write | Docs documents().batchUpdate replaceAllText |

## `google/drive/drive.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/drive/get_files_list` | read | Drive SDK files().list().execute() |
| `/tools/drive/get_file_details` | read | Drive SDK files().get().execute() |
| `/tools/drive/create_folder` | write | Drive SDK files().create() (folder) |
| `/tools/drive/search_files` | read | Drive SDK files().list().execute() |
| `/tools/drive/get_drive_info` | read | Drive SDK about().get().execute() |
| `/tools/drive/get_shared_drives` | read | Drive SDK drives().list().execute() |
| `/tools/drive/get_file_permissions` | read | Drive SDK permissions().list().execute() |
| `/tools/drive/get_file_content` | read | Drive SDK files().get + files().get_media/export_media download; local parse via FileContentParser (no storage writes) |

## `google/enterprise/enterprise.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/google_drive_enterprise/get_users_list` | read | Admin SDK users().list (GET) |
| `/tools/google_drive_enterprise/get_groups_list` | read | Admin SDK groups().list (GET) |
| `/tools/google_drive_enterprise/get_domains_list` | read | Admin SDK domains().list (GET) |
| `/tools/google_drive_enterprise/get_group_members_list` | read | Admin SDK members().get (GET) |
| `/tools/google_drive_enterprise/get_user_info` | read | Admin SDK users().get (GET) |
| `/tools/google_drive_enterprise/get_group_info` | read | Admin SDK groups().get (GET) |

## `google/gmail/gmail.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/gmail/reply` | write | Gmail users.messages.send (POST); may attach PipesHub records |
| `/tools/gmail/draft_email` | write | Gmail users.drafts.create (POST) |
| `/tools/gmail/send_email` | write | Gmail users.messages.send (POST) |
| `/tools/gmail/search_emails` | read | Gmail users.messages.list + users.messages.get(format=metadata) (GET) |
| `/tools/gmail/get_email_details` | read | Gmail users.messages.get(format=full) (GET) |
| `/tools/gmail/get_email_attachments` | read | Gmail users.messages.get(format=full) (GET); lists attachment parts only |
| `/tools/gmail/get_user_profile` | read | Gmail users.getProfile (GET) |

## `google/google_calendar/google_calendar.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/google_calendar/get_calendar_events` | read | Calendar events().list (GET) |
| `/tools/google_calendar/create_calendar_event` | write | Calendar events().insert |
| `/tools/google_calendar/update_calendar_event` | write | Calendar events().get then events().update |
| `/tools/google_calendar/delete_calendar_event` | write | Calendar events().delete |
| `/tools/google_calendar/get_calendar_list` | read | Calendar calendarList().list (GET) |
| `/tools/google_calendar/get_calendar_list_by_id` | read | Calendar calendars().get (GET) |

## `google/google_drive/google_drive.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/drive/get_files_list` | read | Drive files().list (GET) |
| `/tools/drive/create_folder` | write | Drive files().create folder |
| `/tools/drive/upload_file` | write | Drive files().create with media_body (upload) |
| `/tools/drive/download_file` | read | Drive files().get_media (GET) |
| `/tools/drive/delete_file` | write | Drive files().delete |
| `/tools/drive/get_file_details` | read | Drive files().get (GET) |

## `google/sheets/sheets.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/sheets/create_spreadsheet` | write | Sheets spreadsheets().create (POST) |
| `/tools/sheets/get_spreadsheet` | read | Sheets spreadsheets().get (GET) |
| `/tools/sheets/get_values` | read | Sheets spreadsheets.values().get (GET) |
| `/tools/sheets/update_values` | write | Sheets spreadsheets.values().update (PUT) |
| `/tools/sheets/append_values` | write | Sheets spreadsheets.values().append (POST) |
| `/tools/sheets/clear_values` | write | Sheets spreadsheets.values().clear (POST) |
| `/tools/sheets/batch_get_values` | read | Sheets spreadsheets.values().batchGet (GET) |

## `google/youtube/youtube.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/youtube/search_videos` | read | YouTube Data API search().list() (GET) |
| `/tools/youtube/get_video_details` | read | YouTube videos().list() (GET) |
| `/tools/youtube/get_channel_info` | read | YouTube channels().list() (GET) |
| `/tools/youtube/get_playlist_videos` | read | YouTube playlistItems().list() (GET) |
| `/tools/youtube/get_user_playlists` | read | YouTube playlists().list() (GET) |
| `/tools/youtube/get_video_comments` | read | YouTube commentThreads().list() (GET) |
| `/tools/youtube/get_trending_videos` | read | YouTube videos().list(chart=mostPopular) (GET) |

## `image_generator/image_generator.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/image_generator/generate_image` | write | Calls provider generate/edit and schedules upload_bytes_artifact to blob + ArtifactRecord |

## `internal_tools/intrim_tools.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/internaltools/ask_user_question` | read | Only normalizes input and returns json.dumps payload; no I/O |

## `jira/jira.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/jira/validate_connection` | read | GET /rest/api/3/myself |
| `/tools/jira/get_current_user` | read | GET /rest/api/3/myself |
| `/tools/jira/convert_text_to_adf` | read | Local text->ADF conversion, no I/O |
| `/tools/jira/get_create_issue_fields` | read | GET /rest/api/3/issue/createmeta/{project}/issuetypes and .../issuetypes/{id} |
| `/tools/jira/create_issue` | write | POST /rest/api/3/issue (plus GET /field, GET user search) |
| `/tools/jira/update_issue` | write | PUT /rest/api/3/issue/{key}; POST /rest/api/3/issue/{key}/transitions |
| `/tools/jira/get_projects` | read | GET /rest/api/3/project (+ GET oauth accessible-resources for site URL) |
| `/tools/jira/get_project` | read | GET /rest/api/3/project/{key} |
| `/tools/jira/get_issues` | read | POST /rest/api/3/search/jql (read-only JQL search) + GET /rest/api/3/field |
| `/tools/jira/get_issue` | read | GET /rest/api/3/issue/{key} + GET /rest/api/3/field |
| `/tools/jira/search_issues` | read | POST /rest/api/3/search/jql (read-only JQL search) + GET /rest/api/3/field |
| `/tools/jira/add_comment` | write | POST /rest/api/3/issue/{key}/comment |
| `/tools/jira/get_comments` | read | GET /rest/api/3/issue/{key}/comment |
| `/tools/jira/search_users` | read | GET /rest/api/3/user/picker |
| `/tools/jira/get_project_metadata` | read | GET /rest/api/3/project/{key} |
| `/tools/jira/add_attachments` | write | main: POST /rest/api/3/issue/{key}/attachments (files the user shared), and with inline display PUT the issue description |

## `jira_data_center/jira_data_center.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/jira_data_center/validate_connection` | read | GET /rest/api/2/myself |
| `/tools/jira_data_center/get_current_user` | read | GET /rest/api/2/myself |
| `/tools/jira_data_center/get_create_issue_fields` | read | GET /rest/api/2/issue/createmeta/{project}/issuetypes and .../issuetypes/{id} |
| `/tools/jira_data_center/create_issue` | write | POST /rest/api/2/issue |
| `/tools/jira_data_center/update_issue` | write | PUT /rest/api/2/issue/{key}; POST /rest/api/2/issue/{key}/transitions |
| `/tools/jira_data_center/get_projects` | read | GET /rest/api/2/project |
| `/tools/jira_data_center/get_project` | read | GET /rest/api/2/project/{key} |
| `/tools/jira_data_center/get_issue` | read | GET /rest/api/2/issue/{key} + GET /rest/api/2/field |
| `/tools/jira_data_center/search_issues` | read | POST /rest/api/2/search (read-only JQL search) + GET /rest/api/2/field |
| `/tools/jira_data_center/add_comment` | write | POST /rest/api/2/issue/{key}/comment |
| `/tools/jira_data_center/get_comments` | read | GET /rest/api/2/issue/{key}/comment |
| `/tools/jira_data_center/search_users` | read | GET /rest/api/2/user/picker |
| `/tools/jira_data_center/get_project_metadata` | read | GET /rest/api/2/project/{key} |
| `/tools/jira_data_center/add_attachments` | write | main: POST /rest/api/2/issue/{key}/attachments (files the user shared), and with inline display PUT the issue description |

## `knowledge_graph/knowledge_graph.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/knowledgegraph/navigate` | read | GraphNavigator/RecordResolver: graph_provider get_* reads + knowledge hub get_nodes; only in-memory tool_state (remember_record_ids) mutated |
| `/tools/knowledgegraph/lookup_record` | read | RecordResolver.resolve_many: graph_provider get_record_by_* reads; only in-memory tool_state updated |
| `/tools/knowledgegraph/search` | read | execute_search: retrieval_service.search_with_filters + get_flattened_results (blob get_record_from_storage, graph reads) |
| `/tools/knowledgegraph/list_files` | read | execute_list_files: knowledge hub service.get_nodes (graph read) |
| `/tools/knowledgegraph/search_entities` | read | main #3308/#3770: execute_search_entities: entity vector store search + permission-checked graph query; only in-memory tool_state (entity index cache) updated |
| `/tools/knowledgegraph/find_records_by_entity` | read | main #3308/#3770: permission-checked graph query for one entity's records, paged by cursor |
| `/tools/knowledgegraph/find_records_by_value` | read | main #3975: execute_find_records_by_value: permission-checked graph query over typed values (dates, amounts, quantities) |
| `/tools/knowledgegraph/search_by_entities` | read | main #4145: execute_search_by_entities: entity linking lookups + retrieval_service.search_with_filters; offered only under ENTITY_SEARCH_TOOL |
| `/tools/knowledgegraph/search_timeline` | read | main #4296/#4349: execute_timeline_search: one retrieval_service.search_with_filters per period over a span, in date order; offered under TIMELINE_SUMMARIES (on by default) |
| `/tools/knowledgegraph/summarize` | read | WP-41 (G-58): execute_summarize: the run's own KnowledgeService search (same ACL, records in the citation state), its passages sent to the quarantined summarizer on the run's metered model; no write anywhere |
| `/tools/storage_pattern_match/run_command` | read | main #3568/#3894 (merged in PR1): an allowlisted read-only command (grep, find, ls, wc, head, tail, cat, …; no shell operators but pipes; `find` primaries allowlisted, no `-exec`/`-delete`) in the connector's record directory of blob storage |
| `/tools/storage_pattern_match/find_records` | read | main (merged in PR1): the same validated command, then graph reads of the matched records' metadata |
| `/tools/knowledgegraph/find_co_mentioned_entities` | read | main KG-13: permission-checked graph query for the entities mentioned with one entity |
| `/tools/knowledgegraph/find_records_by_person` | read | main KG-13: permission-checked graph query for the records a person is linked to (assigned, reported, authored) |

## `knowledge_hub/knowledge_hub.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/knowledgehub/list_files` | read | KnowledgeHubService.get_nodes (graph read) after resolve_scope |

## `linear/linear.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/linear/get_viewer` | read | GraphQL query viewer |
| `/tools/linear/get_user` | read | GraphQL QUERIES['user'] lookup (hard-coded 'query' type; key missing so raises ValueError before any request) |
| `/tools/linear/get_teams` | read | GraphQL query teams |
| `/tools/linear/get_team` | read | GraphQL QUERIES['team'] lookup (hard-coded 'query' type; key missing so raises ValueError before any request) |
| `/tools/linear/get_issues` | read | GraphQL query issues |
| `/tools/linear/get_issue` | read | GraphQL query issue |
| `/tools/linear/create_issue` | write | GraphQL mutation issueCreate |
| `/tools/linear/update_issue` | write | GraphQL mutation issueUpdate |
| `/tools/linear/delete_issue` | write | GraphQL mutation issueDelete |

## `linkedin/linkedin.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/linkedin/get_userinfo` | read | LinkedIn Rest.li GET /userinfo |
| `/tools/linkedin/create_post` | write | LinkedIn Rest.li CREATE (POST) /posts |
| `/tools/linkedin/get_post` | read | LinkedIn Rest.li GET /posts/{id} |
| `/tools/linkedin/update_post` | write | LinkedIn Rest.li PARTIAL_UPDATE /posts/{id} |
| `/tools/linkedin/delete_post` | write | LinkedIn Rest.li DELETE /posts/{id} |
| `/tools/linkedin/search_people` | read | LinkedIn Rest.li FINDER (GET) /people?q=keywords |

## `lumos/lumos.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/lumos/list_platforms` | read | GET /apps |
| `/tools/lumos/get_platform` | read | GET /apps/{app_id} |
| `/tools/lumos/list_users` | read | GET /users |
| `/tools/lumos/get_user` | read | GET /users/{user_id} |
| `/tools/lumos/get_user_accounts` | read | GET /users/{user_id}/accounts |
| `/tools/lumos/get_user_roles` | read | GET /users/{user_id}/roles |
| `/tools/lumos/list_groups` | read | GET /groups |
| `/tools/lumos/get_group` | read | GET /groups/{group_id} |
| `/tools/lumos/get_group_members` | read | GET /groups/{group_id}/users |
| `/tools/lumos/list_permissions` | read | GET /appstore/requestable_permissions |
| `/tools/lumos/list_app_permissions` | read | GET /appstore/apps/{app_id}/requestable_permissions |
| `/tools/lumos/list_access_requests` | read | GET /appstore/access_requests |
| `/tools/lumos/get_access_request` | read | GET /appstore/access_requests/{id} |
| `/tools/lumos/create_access_request` | write | POST /appstore/access_request |
| `/tools/lumos/cancel_access_request` | write | DELETE /appstore/access_requests/{id} |
| `/tools/lumos/list_access_policies` | read | GET /access_policies |
| `/tools/lumos/get_access_policy` | read | GET /access_policies/{access_policy_id} |
| `/tools/lumos/create_access_policy` | write | POST /access_policies |
| `/tools/lumos/update_access_policy` | write | PUT /access_policies/{access_policy_id} |
| `/tools/lumos/delete_access_policy` | write | DELETE /access_policies/{access_policy_id} |
| `/tools/lumos/create_requestable_permission` | write | POST /appstore/requestable_permissions |
| `/tools/lumos/get_requestable_permission` | read | GET /appstore/requestable_permissions/{permission_id} |
| `/tools/lumos/update_requestable_permission` | write | PATCH /appstore/requestable_permissions/{permission_id} |
| `/tools/lumos/delete_requestable_permission` | write | DELETE /appstore/requestable_permissions/{permission_id} |
| `/tools/lumos/add_user_role` | write | POST /users/{user_id}/roles/{role_name} |
| `/tools/lumos/remove_user_role` | write | DELETE /users/{user_id}/roles/{role_name} |

## `mariadb/mariadb.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/mariadb/list_tables` | read | MariaDBDataSource.list_tables: SELECT from information_schema.TABLES |
| `/tools/mariadb/get_table_ddl` | read | MariaDBDataSource.get_table_ddl: SHOW CREATE TABLE |
| `/tools/mariadb/get_tables_schema` | read | get_table_info/get_primary_keys/get_foreign_keys: SELECTs on information_schema |
| `/tools/mariadb/fetch_db_schema` | read | list_tables/get_table_info/keys/list_views: SELECTs on information_schema |
| `/tools/mariadb/execute_query` | write | Executes arbitrary model-written SQL (no read-only guard) and saves result CSV to blob store via register_task |

## `microsoft/one_drive/one_drive.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/onedrive/get_drives` | read | Graph GET /me/drives (me.drives.get) |
| `/tools/onedrive/get_drive` | read | Graph GET /drives/{id} (drives.by_drive_id.get) |
| `/tools/onedrive/get_files` | read | Graph GET /drives/{id}/items/{id}/children |
| `/tools/onedrive/get_folder_children` | read | Graph GET /drives/{id}/items/{id}/children |
| `/tools/onedrive/get_file` | read | Graph GET /drives/{id}/items/{id} |
| `/tools/onedrive/search_files` | read | Graph GET /drives/{id}/items/root/search(q=) |
| `/tools/onedrive/get_shared_with_me` | read | Graph GET /me/insights/shared, /me/insights/shared/{id}/resource, /me, /drives/{id} |
| `/tools/onedrive/search_shared_with_me` | read | Graph GET /me/insights/shared(+resource), /me, /drives/{id}/items/root/search(q=) |
| `/tools/onedrive/create_folder` | write | Graph POST /drives/{id}/items/{id}/children |
| `/tools/onedrive/rename_item` | write | Graph GET then PATCH /drives/{id}/items/{id} |
| `/tools/onedrive/move_item` | write | Graph PATCH /drives/{id}/items/{id} (parentReference) |
| `/tools/onedrive/get_root_folder` | read | Graph GET /drives/{id}/root |
| `/tools/onedrive/copy_item` | write | Graph POST /drives/{id}/items/{id}/copy |
| `/tools/onedrive/share_item` | write | Graph POST /drives/{id}/items/{id}/invite |
| `/tools/onedrive/get_download_url` | read | Graph GET /drives/{id}/items/{id} (reads downloadUrl) |
| `/tools/onedrive/get_file_content` | read | Graph GET /drives/{id}/items/{id} and /content; in-memory FileContentParser (LLM calls, no persisted state) |
| `/tools/onedrive/create_word_file` | write | Graph PUT /drives/{id}/items/{id}/content (upload) |
| `/tools/onedrive/create_onenote_notebook` | write | Graph POST /me/onenote/notebooks |
| `/tools/onedrive/create_onenote_section` | write | Graph POST /me/onenote/notebooks/{id}/sections (+ POST getNotebookFromWebUrl) |
| `/tools/onedrive/create_onenote_page` | write | Graph POST onenote pages via request_adapter.send_async |
| `/tools/onedrive/get_onenote_sections` | read | Graph POST /me/onenote/notebooks/getNotebookFromWebUrl (documented read-only lookup) + GET /me/onenote/notebooks/{id}/sections |

## `microsoft/one_note/one_note.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/onenote/get_notebooks` | read | MS Graph me.onenote.notebooks.by_notebook_id(...).get() (GET) |
| `/tools/onenote/get_notebook` | read | MS Graph me.onenote.notebooks.by_notebook_id(...).get() (GET) |
| `/tools/onenote/get_sections` | read | MS Graph me.onenote.notebooks/{id}/sections/{id}.get() (GET) |
| `/tools/onenote/get_pages` | read | MS Graph me.onenote.notebooks/{id}/sections/{id}/pages/{id}.get() (GET) |
| `/tools/onenote/get_page` | read | Stub: makes no API call, returns 'not implemented' error |
| `/tools/onenote/create_page` | write | MS Graph POST me/onenote/notebooks/{id}/sections/{id}/pages |
| `/tools/onenote/update_page` | write | Stub today (no API call, returns 'not implemented'), but intended PATCH page content; kept write so implementing it cannot silently land ungated |
| `/tools/onenote/delete_page` | write | MS Graph DELETE me/onenote/notebooks/{id}/sections/{id}/pages/{id} |

## `microsoft/outlook/outlook.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/outlook/send_email` | write | Graph POST /me/messages (draft) + POST /me/messages/{id}/send; attachment upload |
| `/tools/outlook/reply_to_message` | write | Graph POST /me/messages/{id}/reply |
| `/tools/outlook/reply_all_to_message` | write | Graph POST /me/messages/{id}/replyAll |
| `/tools/outlook/forward_message` | write | Graph POST /me/messages/{id}/forward |
| `/tools/outlook/search_messages` | read | Graph GET /me/messages |
| `/tools/outlook/get_message` | read | Graph GET /me/messages/{id} |
| `/tools/outlook/list_message_attachments` | read | Graph GET /me/messages/{id}/attachments |
| `/tools/outlook/stage_attachment_to_blob` | write | Graph GET attachment then registry.register -> writes to PipesHub blob storage |
| `/tools/outlook/get_mail_folders` | read | Graph GET /me/mailFolders (+nextLink) |
| `/tools/outlook/get_calendar_events` | read | Graph GET /me/calendar/calendarView |
| `/tools/outlook/search_calendar_events` | read | Graph GET /me/events |
| `/tools/outlook/create_calendar_event` | write | Graph POST /me/calendar/events |
| `/tools/outlook/get_calendar_event` | read | Graph GET /me/calendar/events/{id} |
| `/tools/outlook/update_calendar_event` | write | Graph PATCH /me/calendar/events/{id} |
| `/tools/outlook/delete_calendar_event` | write | Graph DELETE /me/calendar/events/{id} |
| `/tools/outlook/get_recurring_events` | read | Graph GET /me/calendar/calendarView |
| `/tools/outlook/get_recurring_events_ending` | read | Graph GET /me/events (seriesMaster filter) |
| `/tools/outlook/delete_recurring_event_occurrence` | write | Graph GET instances, PATCH/DELETE /me/calendar/events/{id} |
| `/tools/outlook/get_meeting_transcripts` | read | Graph GET /me/onlineMeetings, /me/calendar/events/{id}, /me/onlineMeetings/{id}/transcripts(+metadataContent) |

## `microsoft/sharepoint/sharepoint.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/sharepoint/get_sites` | read | Graph POST /search/query (read-only search) |
| `/tools/sharepoint/get_site` | read | Graph GET /sites/{id} |
| `/tools/sharepoint/get_pages` | read | Graph GET /sites/{id}/pages |
| `/tools/sharepoint/get_page` | read | Graph GET /sites/{id}/pages/{id}/microsoft.graph.sitePage (Method.GET) |
| `/tools/sharepoint/search_pages` | read | Graph POST /search/query (read-only search) |
| `/tools/sharepoint/list_drives` | read | Graph GET /sites/{id}/drives |
| `/tools/sharepoint/list_files` | read | Graph GET /drives/{id}/items/{id}/children or /root/children (Method.GET) |
| `/tools/sharepoint/search_files` | read | Graph POST /search/query (read-only search) |
| `/tools/sharepoint/get_file_metadata` | read | Graph GET /drives/{id}/items/{id} (Method.GET) |
| `/tools/sharepoint/get_file_content` | read | Graph GET /drives/{id}/items/{id} and /content; in-memory FileContentParser |
| `/tools/sharepoint/create_page` | write | Graph POST /sites/{id}/pages + publish |
| `/tools/sharepoint/update_page` | write | Graph PATCH site page via request_adapter + publish |
| `/tools/sharepoint/create_folder` | write | Graph create driveItem folder via request_adapter.send_async (POST children) |
| `/tools/sharepoint/create_word_document` | write | Graph upload driveItem via request_adapter.send_async |
| `/tools/sharepoint/move_item` | write | Graph PATCH driveItem parentReference via request_adapter.send_async |
| `/tools/sharepoint/find_notebook` | read | Graph GET /sites/{id}/onenote/notebooks (Method.GET) |
| `/tools/sharepoint/list_notebook_pages` | read | Graph GET /sites/{id}/onenote/notebooks/{id}/sections, /sections/{id}/pages |
| `/tools/sharepoint/get_notebook_page_content` | read | Graph GET /sites/{id}/onenote/pages/{id} and /content |
| `/tools/sharepoint/create_onenote_notebook` | write | Graph POST site onenote notebook + sections |

## `microsoft/teams/teams.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/teams/get_user_info` | read | Graph GET /users/{id} or GET /users?$filter=mail eq ... |
| `/tools/teams/get_users_list` | read | Graph GET /users?$select&$top=100 (paginated via nextLink) |
| `/tools/teams/get_user_conversations` | read | Graph GET /users (lookup), GET /me/chats, GET /chats/{id}/members, GET /chats/{id}/messages |
| `/tools/teams/get_user_channels` | read | Graph GET /me/joinedTeams + GET /teams/{id}/channels |
| `/tools/teams/get_meetings` | read | Graph GET /me/calendarView or GET /me/events |
| `/tools/teams/search_calendar_events_in_range` | read | Graph GET /me/calendar/calendarView (subject filtered client-side) |
| `/tools/teams/get_my_meeting_transcripts` | read | Graph GET /me/calendar/events/{id}, GET /me/onlineMeetings, GET /me/onlineMeetings/{id}/transcripts[/metadataContent] |
| `/tools/teams/get_people_attended` | read | Graph GET /me/onlineMeetings/{id}/attendanceReports (+ attendanceRecords); GET /me/calendar/events/{id} for id resolution |
| `/tools/teams/get_people_invited` | read | Graph GET /me/onlineMeetings/{id} or GET /me/events |
| `/tools/teams/create_event` | write | Graph POST /me/calendar/events |
| `/tools/teams/create_channel_meeting` | write | Graph POST /me/events + POST /teams/{id}/channels/{id}/messages |
| `/tools/teams/edit_event` | write | Graph PATCH /me/events/{id} |
| `/tools/teams/get_teams` | read | Graph GET /me/joinedTeams |
| `/tools/teams/get_team` | read | Graph GET /teams/{id} |
| `/tools/teams/create_team` | write | Graph POST /teams (then GET /me/joinedTeams to poll) |
| `/tools/teams/get_members` | read | Graph GET /teams/{id}/members or /teams/{id}/channels/{id}/members, GET /teams/{id}/channels |
| `/tools/teams/add_member` | write | Graph POST /teams/{id}/members or POST /teams/{id}/channels/{id}/members |
| `/tools/teams/get_channels` | read | Graph GET /teams/{id}/channels |
| `/tools/teams/create_channel` | write | Graph POST /teams/{id}/channels (DELETE on rollback) |
| `/tools/teams/update_channel` | write | Graph PATCH /teams/{id}/channels/{id} |
| `/tools/teams/send_channel_message` | write | Graph POST /teams/{id}/channels/{id}/messages |
| `/tools/teams/send_user_message` | write | Graph POST /chats (if needed) + POST /chats/{id}/messages |
| `/tools/teams/reply_to_message` | write | Graph POST /teams/{id}/channels/{id}/messages/{id}/replies |
| `/tools/teams/send_message_to_multiple_channels` | write | Graph POST /teams/{id}/channels/{id}/messages per channel |
| `/tools/teams/search_messages` | read | Graph GET /me/joinedTeams, GET /teams/{id}/channels, GET .../messages (filtered client-side) |
| `/tools/teams/add_reaction` | write | Graph POST .../messages/{id}/setReaction |
| `/tools/teams/get_reactions` | read | Graph GET /teams/{id}/channels/{id}/messages/{id} |
| `/tools/teams/remove_reaction` | write | Graph POST .../messages/{id}/unsetReaction |
| `/tools/teams/get_channel_messages` | read | Graph GET /teams/{id}/channels/{id}/messages |
| `/tools/teams/get_thread_replies` | read | Graph GET /teams/{id}/channels/{id}/messages/{id}/replies |
| `/tools/teams/update_message` | write | Graph PATCH /teams/{id}/channels/{id}/messages/{id} |
| `/tools/teams/get_message_permalink` | read | Graph GET /teams/{id}/channels/{id}/messages/{id} (webUrl) |
| `/tools/teams/create_chat` | write | Graph POST /chats |
| `/tools/teams/get_chat` | read | Graph GET /me/chats/{id} |

## `notion/notion.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/notion/create_page` | write | POST /v1/pages |
| `/tools/notion/get_page` | read | GET /v1/pages/{page_id} |
| `/tools/notion/update_page` | write | PATCH /v1/pages/{page_id} |
| `/tools/notion/delete_page` | write | DELETE /v1/blocks/{block_id} (archives page) |
| `/tools/notion/search` | read | POST /v1/search (Notion documents as read-only search) |
| `/tools/notion/list_users` | read | GET /v1/users |
| `/tools/notion/retrieve_user` | read | GET /v1/users/{user_id} |
| `/tools/notion/create_database` | write | POST /v1/databases |
| `/tools/notion/query_database` | read | POST /v1/databases/{id}/query (Notion documents as read-only query) |
| `/tools/notion/get_database` | read | GET /v1/databases/{database_id} |
| `/tools/notion/get_page_content` | read | main: GET /v1/blocks/{page_id}/children (the page's blocks, paged) |

## `posthog/posthog.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/posthog/capture_event` | write | PostHog GraphQL mutation CaptureEvent |
| `/tools/posthog/get_event` | read | PostHog GraphQL query Event |
| `/tools/posthog/get_person` | read | PostHog GraphQL query Person |
| `/tools/posthog/update_person` | write | PostHog GraphQL mutation PersonUpdate |
| `/tools/posthog/delete_person` | write | PostHog GraphQL mutation PersonDelete |
| `/tools/posthog/search_events` | read | PostHog GraphQL query Events |

## `redshift/redshift.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/redshift/list_schemas` | read | Fixed parameterized SELECT on pg_namespace/pg_user via RedshiftClient.execute_query (commit after SELECT is a no-op) |
| `/tools/redshift/list_schemas_and_tables` | read | Fixed SELECTs: pg_namespace + information_schema.tables (parameterized) |
| `/tools/redshift/get_table_ddl` | read | Fixed parameterized SELECTs on information_schema.columns/table_constraints/check_constraints; DDL string built in Python |
| `/tools/redshift/get_schema_ddl` | read | Fixed parameterized SELECTs on information_schema.tables + per-table information_schema DDL queries |
| `/tools/redshift/get_tables_schema` | read | Fixed parameterized SELECTs on information_schema (get_table_info/get_primary_keys/get_foreign_keys) |
| `/tools/redshift/fetch_db_schema` | read | Fixed parameterized SELECTs on pg_namespace/information_schema tables/columns/constraints/views |
| `/tools/redshift/execute_query` | write | cursor.execute(<model-written SQL>) then connection.commit(); no SELECT-only check or read-only txn; also uploads CSV of results to PipesHub blob storage (save_conversation_file_to_storage) |
| `/tools/redshift/list_tables` | read | Fixed parameterized SELECT on information_schema.tables |

## `retrieval/retrieval.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/retrieval/search_internal_knowledge` | read | retrieval_service.search_with_filters + get_flattened_results/enrich_records_with_graph_context (blob and graph reads only) |

## `s3/s3.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/s3/list_buckets` | read | aioboto3 s3 list_buckets |
| `/tools/s3/create_bucket` | write | aioboto3 s3 create_bucket |
| `/tools/s3/delete_bucket` | write | aioboto3 s3 delete_bucket |
| `/tools/s3/list_objects` | read | aioboto3 s3 list_objects_v2 |
| `/tools/s3/get_object` | read | aioboto3 s3 get_object |
| `/tools/s3/put_object` | write | aioboto3 s3 put_object |
| `/tools/s3/delete_object` | write | aioboto3 s3 delete_object |
| `/tools/s3/copy_object` | write | aioboto3 s3 copy_object |

## `salesforce/salesforce.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/salesforce/soql_query` | write | GET /services/data/vXX/query with model-written SOQL; FOR VIEW/FOR REFERENCE clauses update last-viewed dates, so fail closed. Classified per call since C-3 (see "Per-call classification") |
| `/tools/salesforce/sosl_search` | read | GET /services/data/v{ver}/search (SOSL, read-only) |
| `/tools/salesforce/get_record` | read | GET /services/data/v{ver}/sobjects/{sobject}/{id} |
| `/tools/salesforce/create_record` | write | POST /services/data/v{ver}/sobjects/{sobject} |
| `/tools/salesforce/update_record` | write | PATCH /services/data/v{ver}/sobjects/{sobject}/{id} |
| `/tools/salesforce/upload_file_to_salesforce` | write | resolve_attachments (PipesHub read) then POST /services/data/v{ver}/sobjects/ContentVersion (+ContentDocumentLink) via SalesforceAttachmentUploader; emits audit |
| `/tools/salesforce/attach_file_to_record` | write | POST /services/data/v{ver}/sobjects/ContentDocumentLink |
| `/tools/salesforce/describe_object` | read | GET /services/data/v{ver}/sobjects/{sobject}/describe |
| `/tools/salesforce/list_recent_records` | read | GET /services/data/v{ver}/query (fixed SELECT ... ORDER BY LastModifiedDate, no FOR VIEW) |
| `/tools/salesforce/search_accounts` | read | GET /services/data/v{ver}/query (fixed SOQL SELECT) |
| `/tools/salesforce/create_account` | write | POST /services/data/v{ver}/sobjects/Account |
| `/tools/salesforce/search_contacts` | read | GET /services/data/v{ver}/query (fixed SOQL SELECT) |
| `/tools/salesforce/create_contact` | write | POST /services/data/v{ver}/sobjects/Contact |
| `/tools/salesforce/search_leads` | read | GET /services/data/v{ver}/query (fixed SOQL SELECT) |
| `/tools/salesforce/create_lead` | write | POST /services/data/v{ver}/sobjects/Lead |
| `/tools/salesforce/search_opportunities` | read | GET /services/data/v{ver}/query (fixed SOQL SELECT) |
| `/tools/salesforce/create_opportunity` | write | POST /services/data/v{ver}/sobjects/Opportunity |
| `/tools/salesforce/search_cases` | read | GET /services/data/v{ver}/query (fixed SOQL SELECT) |
| `/tools/salesforce/create_case` | write | POST /services/data/v{ver}/sobjects/Case |
| `/tools/salesforce/search_products` | read | GET /services/data/v{ver}/query (fixed SOQL SELECT) |
| `/tools/salesforce/create_product` | write | POST /services/data/v{ver}/sobjects/Product2 |
| `/tools/salesforce/list_pricebooks` | read | GET /services/data/v{ver}/query (fixed SOQL SELECT) |
| `/tools/salesforce/create_pricebook_entry` | write | POST /services/data/v{ver}/sobjects/PricebookEntry |
| `/tools/salesforce/add_product_to_opportunity` | write | GET /services/data/v{ver}/query lookups then POST /services/data/v{ver}/sobjects/OpportunityLineItem |
| `/tools/salesforce/search_tasks` | read | GET /services/data/v{ver}/query (fixed SOQL SELECT) |
| `/tools/salesforce/create_task` | write | POST /services/data/v{ver}/sobjects/Task |
| `/tools/salesforce/get_record_chatter` | read | GET /services/data/v{ver}/chatter/feeds/record/{id}/feed-elements |
| `/tools/salesforce/post_chatter_comment` | write | POST /services/data/v{ver}/chatter/feed-elements/{id}/capabilities/comments/items |
| `/tools/salesforce/post_chatter_to_record` | write | POST /services/data/v{ver}/chatter/feed-elements |
| `/tools/salesforce/get_current_user` | read | GET /services/oauth2/userinfo |

## `slack/slack.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/slack/send_message` | write | POST chat.postMessage; files_upload_v2 for attachments |
| `/tools/slack/get_channel_history` | read | GET conversations.history (+ users.info/conversations.info/conversations.list lookups) |
| `/tools/slack/get_channel_info` | read | GET conversations.info (+ conversations.list for name resolution) |
| `/tools/slack/get_user_info` | read | GET users.info (+ users.lookupByEmail/users.list for resolution) |
| `/tools/slack/fetch_channels` | read | GET conversations.list (paginated) + users.info enrichment |
| `/tools/slack/search_all` | read | GET search.messages (+ users.info/conversations.info enrichment) |
| `/tools/slack/get_channel_members` | read | GET conversations.members + users.info |
| `/tools/slack/get_channel_members_by_id` | read | GET conversations.members + users.info |
| `/tools/slack/resolve_user` | read | GET users.info |
| `/tools/slack/search_users` | read | GET users.list (paginated) |
| `/tools/slack/check_token_info` | read | auth.test via SDK auth_test (read-only identity check) |
| `/tools/slack/send_direct_message` | write | POST conversations.open + POST chat.postMessage; files_upload_v2 for attachments |
| `/tools/slack/get_dm_history` | write | POST conversations.open (opens/creates DM channel if none exists) then GET conversations.history |
| `/tools/slack/reply_to_message` | write | GET conversations.history then POST chat.postMessage (thread_ts); files_upload_v2 for attachments |
| `/tools/slack/send_message_to_multiple_channels` | write | POST chat.postMessage per channel |
| `/tools/slack/add_reaction` | write | POST reactions.add |
| `/tools/slack/search_messages` | read | GET search.messages (+ users.info/users.list for handle resolution) |
| `/tools/slack/set_user_status` | write | POST users.profile.set |
| `/tools/slack/schedule_message` | write | POST chat.scheduleMessage |
| `/tools/slack/pin_message` | write | POST pins.add |
| `/tools/slack/get_unread_messages` | read | GET conversations.info + GET conversations.history (no conversations.mark) |
| `/tools/slack/get_scheduled_messages` | read | GET chat.scheduledMessages.list |
| `/tools/slack/send_message_with_mentions` | write | POST chat.postMessage |
| `/tools/slack/get_users_list` | read | GET users.list (paginated) |
| `/tools/slack/get_user_conversations` | read | auth.test + GET users.conversations (paginated) |
| `/tools/slack/get_user_groups` | read | GET usergroups.list |
| `/tools/slack/get_user_group_info` | read | GET usergroups.list |
| `/tools/slack/get_user_channels` | read | auth.test + GET users.conversations (paginated) |
| `/tools/slack/delete_message` | write | POST chat.delete |
| `/tools/slack/update_message` | write | POST chat.update |
| `/tools/slack/get_message_permalink` | read | GET chat.getPermalink |
| `/tools/slack/get_reactions` | read | GET reactions.get |
| `/tools/slack/remove_reaction` | write | POST reactions.remove |
| `/tools/slack/get_pinned_messages` | read | GET pins.list |
| `/tools/slack/unpin_message` | write | POST pins.remove |
| `/tools/slack/get_thread_replies` | read | GET conversations.replies |
| `/tools/slack/upload_file_to_channel` | write | files_upload_v2 (files.getUploadURLExternal + completeUploadExternal) |

## `zendesk/zendesk.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/zendesk/get_current_user` | read | GET /api/v2/users/me.json |
| `/tools/zendesk/list_tickets` | read | GET /api/v2/tickets.json |
| `/tools/zendesk/get_ticket` | read | GET /api/v2/tickets/{id}.json |
| `/tools/zendesk/create_ticket` | write | POST /api/v2/tickets.json |
| `/tools/zendesk/update_ticket` | write | PUT /api/v2/tickets/{id}.json |
| `/tools/zendesk/delete_ticket` | write | DELETE /api/v2/tickets/{id}.json |
| `/tools/zendesk/list_users` | read | GET /api/v2/users.json |
| `/tools/zendesk/get_user` | read | GET /api/v2/users/{id}.json |
| `/tools/zendesk/search_tickets` | read | GET /api/v2/search.json |

## `zoom/zoom.py`

| Tool | Effect | Evidence |
|---|---|---|
| `/tools/zoom/get_my_profile` | read | GET /users/{userId} |
| `/tools/zoom/list_meetings` | read | GET /users/{userId}/meetings + GET /meetings/{id} |
| `/tools/zoom/get_meeting` | read | GET /meetings/{meetingId} |
| `/tools/zoom/create_meeting` | write | POST /users/{userId}/meetings |
| `/tools/zoom/update_meeting` | write | PATCH /meetings/{meetingId} |
| `/tools/zoom/delete_meeting` | write | DELETE /meetings/{meetingId} |
| `/tools/zoom/list_upcoming_meetings` | read | GET /users/{userId}/upcoming_meetings |
| `/tools/zoom/get_meeting_invitation` | read | GET /meetings/{meetingId}/invitation |
| `/tools/zoom/list_recurring_meetings_ending_in_range` | read | GET /users/{userId}/meetings + GET /meetings/{id} |
| `/tools/zoom/get_meeting_transcript` | read | GET /past_meetings/{id}/instances, GET /meetings/{id}/transcript, GET transcript download URL |
| `/tools/zoom/list_contacts` | read | GET /chat/users/me/contacts |
| `/tools/zoom/get_contact` | read | GET /chat/users/me/contacts/{identifier} |
| `/tools/zoom/list_folder_children` | read | GET /docs/files/{fileId}/children |

## Who provides a tool (KN2)

Whether a tool is the platform's or a connector's is declared on its registration, never read from its name or path
(decided 2026-10-10): `@Toolset(..., provider=…)` / `ToolsetBuilder(name, provider=…)` (`ToolProvider`,
required), and `provider=` on a per-request tool's adapter. Each tool carries one `provider` tag
(`harness_core.tools.tags`). `ToolsetSource` pins a `platform` tool `pipeshub@<group>/<tool>`, allowed by the run's
other fields; a `connector` tool is granted by its pinned instance (without a binding, `<type>@unknown/<tool>`,
granted to no one); a tool that declares none is granted to no run and logged `tools.provider_undeclared`. The release
catalog's tool descriptors carry it (`provider`, not in the descriptor digest).
`tests/unit/harness/test_kn2_tool_provider.py` fails on a toolset or a per-request tool that does not declare one.

