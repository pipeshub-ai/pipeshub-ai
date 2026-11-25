"""The platform events the application emits: one entry per event, with the facts it carries.

``tests/unit/platform_events/test_catalog.py`` checks every ``platform_events.emit``
in the application against this file, so a typo in an event name or a field fails
a test instead of going unheard.

Names are product terms, dot-separated, ending in what happened. A payload carries
product facts only: ids, kinds, outcomes, counts and durations in seconds. Never
content (a query, a document, a name found in one), and never anything about how a
listener might record it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Event:
    name: str
    summary: str
    fields: tuple[str, ...]


EVENTS: dict[str, Event] = {}


def _event(name: str, summary: str, *fields: str) -> None:
    if name in EVENTS:
        raise ValueError(f"event {name!r} is catalogued twice")
    EVENTS[name] = Event(name, summary, fields)


# What each field means, wherever it appears.
FIELDS: dict[str, str] = {
    "active_by_level": "distinct fairness keys with buffered work, by hierarchy level (org, connector)",
    "active_by_type": "active connector instances by connector type, or by (type, email domain) where the domain is known",
    "age_seconds": "age of the oldest item, in seconds",
    "ambiguous_references": "references with several candidates, skipped rather than guessed",
    "answer": "who answered: kg, walk, or why the walk did",
    "broker": "message broker of the consumer: kafka or redis",
    "call_site": "which indexing step made the model call",
    "capability": "the database capability concerned",
    "change": "what happened to the kept request: written, cleared, resent, expired, ...",
    "chat_mode": "chat mode of the request",
    "citation_count": "citations the answer carries",
    "collection": "graph collection name",
    "complete": "whether everything was covered; false when a cap or a failed read left part out",
    "connector": "connector type name",
    "connector_id": "connector instance id",
    "connectors": "connector type names the citations came from, sorted",
    "consumer_id": "process-local number of an indexing consumer, never reused",
    "count": "how many",
    "demo_sources": "whether a citation came from a demo connector",
    "direction": "input or output",
    "dropped_cap": "entities dropped because the record hit its cap",
    "due": "repos owed a build that are due now",
    "duration_seconds": "wall-clock time it took, in seconds",
    "edge_count": "edges returned",
    "edges_by_relation": "edges written, by relation",
    "email_domain": "domain part of the person's email address, or unknown",
    "entities_by_kind": "entities written, by kind",
    "error_code": "class name of the error",
    "event_type": "event type of the broker message",
    "external_references": "references to names no file in the repo defines",
    "files_without_facts": "files whose stored facts could not be read",
    "graph_reads": "graph reads made",
    "graph_verdict": "what the graph check said: admit or deny",
    "has_tools": "whether the request named tools",
    "id_count": "ids in it",
    "job": "which recovery pass",
    "kb_id": "knowledge base id, or none for a record that is not from one",
    "key_field": "name of the message field that was absent",
    "kind": "which kind, from a closed set named in the event's summary",
    "lag": "offsets read but not yet committed past",
    "lane": "lane name",
    "layout": "stored-record layout of the write",
    "leg": "which leg of the search",
    "leg_size": "number of results the search leg was asked for",
    "lookup": "which lookup",
    "mentions_by_grounding": "mentions written, by grounding status",
    "method": "provider method name",
    "mime_type": "the record's MIME type, or none",
    "mode": "which mode, from a closed set named in the event's summary",
    "node_count": "nodes returned",
    "ok": "whether it succeeded",
    "oldest_pending_age_seconds": "how long the oldest pending item has waited, in seconds",
    "oldest_seconds": "age of the oldest queued job, in seconds",
    "open": "whether calls to the provider now fail fast",
    "operation": "operation name, from a closed set fixed by code",
    "org_id": "organisation id",
    "outcome": "how it ended, from a closed set named in the event's summary",
    "partition": "broker partition number",
    "pending": "items waiting",
    "plan": "how the search was served",
    "provider": "provider name",
    "query": "which read: descendants or levels",
    "query_count": "number of queries in the request",
    "reason": "why, from a closed set fixed by code",
    "records_by_connector_status": "records held, by (connector type, indexing status)",
    "recall": "recall measured, 0 to 1",
    "rejected": "entities the validator rejected",
    "result": "what was found, from a closed set named in the event's summary",
    "route": "storage-service route used: batch or record",
    "scope": "scope concerned (personal or team for a connector; the backfill's scope for a batch)",
    "search_type": "kind of search",
    "service": "name of the service process",
    "share_band": "band of the candidate share",
    "site": "name of the code site, fixed by code",
    "size": "size class: large or small",
    "size_band": "band of the candidate count",
    "slot": "admission slot class",
    "source": "where it came from, from a closed set fixed by code",
    "stage": "which step of the work, fixed by code",
    "started": "builds started",
    "state": "which state",
    "statement_class": "class of the SQL statement",
    "status": "status of the result",
    "status_code": "HTTP status of the response",
    "stopped": "whether the person stopped it",
    "store": "which store",
    "strategy": "extraction strategy",
    "streaming": "whether the response is streamed",
    "stuck": "items that failed too many times to be retried",
    "termination_reason": "why the extraction run ended",
    "tier": "search tier or index tier",
    "to_strategy": "strategy fallen back to",
    "tokens_in": "model tokens sent",
    "tokens_out": "model tokens received",
    "tool": "tool name",
    "tool_calls": "tool calls the run made",
    "topic": "broker topic name",
    "trigger": "what triggered it",
    "turns": "model turns the run took",
    "unresolved_references": "references that could not be bound",
    "unsafe": "whether the policy can evict keys silently",
    "user_email": "the person's email address",
    "user_id": "user id",
    "verdict": "ok or below_floor",
    "verified_at": "Unix time the verify finished",
    "wait_seconds": "time spent waiting, in seconds",
    "waiting": "items waiting",
    "window": "which window of samples",
}

_event(
    "code_graph.build.request_deferred",
    "files_without_facts: files a build kept old edges for (stored facts unreadable); busy_request: requests left to a build already running; reconciler_start: owed builds the reconciler started",
)

_event(
    "code_graph.builds.reconciled",
    "Repos owed a build at the last reconciler sweep, due now or backing off",
    "due",
    "waiting",
    "started",
)

_event(
    "code_graph.tool.called",
    "Calls that reach a code-graph tool, from in-product agents and from MCP (every pipeshub_code_* tool but pipeshub_code_repos), by tool and outcome (ok, or error: refused arguments, a failed lookup or an exception). A request refused before it reaches a tool (an unknown or unreadable repo, invalid MCP input) is not counted here",
    "tool",
    "ok",
)

_event(
    "db.capability.downgraded",
    "Capabilities downgraded in non-strict mode",
    "capability",
    "mode",
)

_event(
    "db.connection.acquired",
    "Wait for a pooled connection",
    "service",
    "wait_seconds",
)

_event(
    "db.session_role.leaked",
    "Session-level SET ROLE found on a pooled connection",
    "service",
)

_event(
    "db.statement.completed",
    "Statement duration",
    "service",
    "site",
    "statement_class",
    "duration_seconds",
)

_event(
    "db.unit_of_work.completed",
    "Unit of work duration",
    "service",
    "outcome",
    "duration_seconds",
)

_event(
    "db.unit_of_work.retried",
    "Unit-of-work retries",
    "error_code",
)

_event(
    "db.unit_of_work.started_without_org",
    "Unit of work started without a usable org context",
    "service",
)

_event(
    "document.page_count.unavailable",
    "PDFs whose page count could not be taken (a timeout, a dead worker or a closed pool)",
)

_event(
    "entity_index.entities.written",
    "Entities handled by entity index writes, by operation and outcome",
    "operation",
    "outcome",
    "count",
)

_event(
    "entity_resolution.lookup.failed",
    "Entity lookups that raised: each failed attempt, and each resolution deferred after the last one",
    "lookup",
    "outcome",
)

_event(
    "entity_resolution.model.called",
    "Merge-decision model calls by result",
    "result",
)

_event(
    "entity_resolution.names.fell_back",
    "Names that fell back to a safe default, by reason",
    "reason",
    "count",
)

_event(
    "entity_resolution.names.resolved",
    "Extracted taxonomy names by how they were resolved",
    "kind",
    "outcome",
    "count",
)

_event(
    "entity_resolution.record.resolved",
    "Wall-clock time of one record's resolution",
    "mode",
    "duration_seconds",
)

_event(
    "graph.schema.widened",
    "Times this process widened a live collection schema; above 0 in steady state means an older image is running",
    "collection",
    "trigger",
    "wait_seconds",
)

_event(
    "graph.schema.widening_deferred",
    "Widenings not sent because a stream transaction was still open after the quiet-point wait; the poll tries again",
    "collection",
    "trigger",
)

_event(
    "graph.write.rejected",
    "Graph writes refused by schema validation, by collection",
    "collection",
)

_event(
    "indexing.connector_off.settled",
    "Queued events of turned-off or removed connectors acknowledged as they were read, without being buffered or dispatched",
    "broker",
    "count",
)

_event(
    "indexing.dwell.exceeded",
    "Buffered items force-resolved after exceeding the dwell budget",
    "broker",
    "count",
)

_event(
    "indexing.fairness_key.missed",
    "Messages whose fairness key field was absent, grouped under the default",
    "broker",
    "key_field",
)

_event(
    "indexing.gate.waiters_counted",
    "Tasks spawned but not yet admitted through their index gate, by tier",
    "broker",
    "tier",
    "count",
)

_event(
    "indexing.keep_old_copy.refused",
    "Runs that used the order they always had because the fleet could not be confirmed for keeping the old copy",
    "reason",
)

_event(
    "indexing.lane.connectors_counted",
    "Connectors assigned to each Redis Streams lane, by size",
    "lane",
    "size",
    "count",
)

_event(
    "indexing.lane.oldest_waiting_measured",
    "Age of the oldest event each lane has not finished with; 0 when it has none",
    "lane",
    "age_seconds",
)

_event(
    "indexing.lane_assignment.fell_back",
    "Record events sent to their connector's hashed lane because the lane map could not be read",
    "reason",
)

_event(
    "indexing.lanes.paused_counted",
    "Lanes currently not being read because a key on them is at its cap",
    "broker",
    "count",
)

_event(
    "indexing.read.deferred",
    "Reads stopped because the buffer had no room",
    "broker",
    "reason",
)

_event(
    "indexing.record.dispatched",
    "Records dispatched by the fair scheduler",
    "broker",
    "org_id",
)

_event(
    "indexing.replay.acknowledged",
    "Redelivered events of an already completed delivery acknowledged without work",
    "service",
)

_event(
    "indexing.replay_set.failed",
    "Replay-set reads or writes that failed; the replay guard is weaker for that record, nothing else changes",
    "operation",
)

_event(
    "indexing.scheduler.measured",
    "Messages currently held in the fair-scheduling buffer",
    "broker",
    "pending",
    "active_by_level",
)

_event(
    "indexing.watermark.measured",
    "Offsets read but not yet committed past, per partition",
    "topic",
    "partition",
    "lag",
)

_event(
    "knowledge_graph.access.decided",
    "Nodes an access check decided, by who answered",
    "answer",
    "count",
)

_event(
    "knowledge_graph.access.fell_back",
    "Nodes (or whole calls) that kg handed to the walk, by reason",
    "reason",
)

_event(
    "knowledge_graph.backfill.batch_committed",
    "Backfill batches committed",
    "scope",
)

_event(
    "knowledge_graph.backfill.verified",
    "Backfills that ended in a verify, by outcome",
    "outcome",
)

_event(
    "knowledge_graph.change_log.counted",
    "Committed change-log rows not yet projected",
    "org_id",
    "count",
)

_event(
    "knowledge_graph.changes.drained",
    "Change-log rows projected by the drainer",
    "stage",
    "count",
)

_event(
    "knowledge_graph.connector.bootstrapped",
    "Connectors the worker took to enforce, or left on the walk, by outcome",
    "outcome",
)

_event(
    "knowledge_graph.connector.demoted",
    "Connectors taken back to the walk because kg differs from it",
    "source",
)

_event(
    "knowledge_graph.counts.checked",
    "Checks of a connector's counters against a recount of its rows, by what they found (same; differs: the totals read from them are wrong; unseeded: not counted whole yet, nothing compared; error: the check did not run)",
    "result",
)

_event(
    "knowledge_graph.drain.deferred",
    "Drainer passes whose projection gave up reaching connectors it had not locked (nothing written, retried)",
    "stage",
)

_event(
    "knowledge_graph.drain.failed",
    "Drainer passes that failed and will be retried",
    "stage",
)

_event(
    "knowledge_graph.fence.measured",
    "Age of the oldest queued restamp job (a fence hides until it is done)",
    "org_id",
    "oldest_seconds",
)

_event(
    "knowledge_graph.flush.failed",
    "Projections in a writer's transaction that failed and left the change log rows",
    "stage",
)

_event(
    "knowledge_graph.hierarchy.answered",
    "Descendant and level reads, by who answered and why the walk did",
    "query",
    "answer",
)

_event(
    "knowledge_graph.legacy_records.counted",
    "Records of a connector that keep today's walk, by the compiler's reason",
    "org_id",
    "connector_id",
    "reason",
    "count",
)

_event(
    "knowledge_graph.net.checked",
    "Checks of an enforced connector against the walk, by where they ran and what they found (over: kg admitted what the walk denies; under: kg denied what the walk admits; same; skipped; busy: a sample found the process at its limit; error)",
    "source",
    "result",
)

_event(
    "knowledge_graph.net.older_epoch_counted",
    "Enforced connectors whose copy is of an older projection code epoch: not served or verified until the bootstrap brings them up to date (alert when it stays above 0)",
    "org_id",
    "count",
)

_event(
    "knowledge_graph.net.overdue_counted",
    "Enforced connectors whose full verify has not completed within 24 verify intervals",
    "org_id",
    "count",
)

_event(
    "knowledge_graph.net.verified",
    "Unix time a connector's last complete verify against the graph finished (alert when it is older than a few verify intervals)",
    "org_id",
    "connector_id",
    "verified_at",
)

_event(
    "knowledge_graph.nodes.flushed",
    "Nodes projected by a writer's own transaction",
    "stage",
    "count",
)

_event(
    "knowledge_graph.quarantine.counted",
    "Change-log rows the drain cannot read (alert on > 0)",
    "org_id",
    "count",
)

_event(
    "knowledge_graph.restamp.batch_committed",
    "Restamp batches committed",
    "kind",
)

_event(
    "knowledge_graph.restamp.batch_failed",
    "Restamp batches that failed and will be retried",
    "kind",
)

_event(
    "knowledge_graph.restamp.queued",
    "Restamp jobs queued",
    "kind",
)

_event(
    "knowledge_graph.restamp_queue.counted",
    "Queued restamp jobs, by whether a worker holds them",
    "org_id",
    "state",
    "count",
)

_event(
    "knowledge_graph.row.refused",
    "Rows with a value a kg writer refuses (a node written without it, a grant left out), and their connector taken off the readers",
    "collection",
)

_event(
    "knowledge_graph.worker.iteration_failed",
    "Iterations of the projection worker's loop that raised and were continued",
    "stage",
)

_event(
    "knowledge_graph.write.unclassified",
    "Graph writes by a provider method the kg registry does not list",
    "method",
)

_event(
    "knowledge_hub.cross_grants.bounded",
    "Knowledge Hub requests past the bound of one list of cross-connector grants: probe (tested on each node) or cut (no user to probe for)",
    "outcome",
)

_event(
    "llm.call.finished",
    "Indexing-time LLM calls by call site and outcome",
    "call_site",
    "outcome",
)

_event(
    "llm.circuit.changed",
    "1 while calls to a model provider fail fast",
    "provider",
    "open",
)

_event(
    "llm.tokens.used",
    "Tokens sent to and received from indexing-time LLM calls",
    "call_site",
    "direction",
    "count",
)

_event(
    "message.admission.refused",
    "Stored admissions of a queue delivery that did not verify (forged, copied, edited, expired or made with a key no longer configured)",
    "topic",
)

_event(
    "message.envelope.accepted_without_context",
    "Broker messages accepted without a signed ctx (upgrade window)",
    "topic",
)

_event(
    "message.envelope.expired",
    "Validly signed broker messages older than the accepted signature age, by event type (a self-scheduled control message dropped for lag ends its chain)",
    "topic",
    "event_type",
)

_event(
    "message.envelope.processed_unverified",
    "Signed broker messages processed without verification because no key is configured",
    "topic",
)

_event(
    "message.envelope.rejected",
    "Broker messages dropped because their envelope was rejected, by reason and (only once the signature verified) event type: a dropped self-scheduled control message ends its chain, so this is the place to see one go missing",
    "topic",
    "reason",
    "event_type",
)

_event(
    "message.envelope.stripped",
    "Broker messages without a ctx while signing is enabled (stripped or unsigned producer)",
    "topic",
)

_event(
    "message.inbox.failed",
    "Consumer inbox reads or writes that failed; the message was processed as if new (at-least-once)",
    "operation",
)

_event(
    "message.sent_unsigned",
    "Messages sent unsigned while signing is enabled",
    "topic",
    "reason",
)

_event(
    "named_entity.extraction.completed",
    "Wall-clock time of extraction, the part that holds the index permit with classify",
    "strategy",
    "duration_seconds",
)

_event(
    "named_entity.extraction.fell_back",
    "Agent runs that fell back to another strategy",
    "to_strategy",
    "reason",
)

_event(
    "named_entity.orphans.swept",
    "Orphan sweep results: marked, cleared, deleted, relinked, dangling_removed, dangling_failed, values_removed, vector_failed",
    "outcome",
    "count",
)

_event(
    "named_entity.schema.stepped_down",
    "Structured-output step-downs, by the mode stepped down to",
    "mode",
)

_event(
    "org_context.violated",
    "Org-scoped accesses with no ambient org (missing) or a different one (mismatch)",
    "kind",
    "site",
)

_event(
    "paid_result.cache.accessed",
    "Paid-result cache lookups and stores by kind and outcome",
    "kind",
    "outcome",
)

_event(
    "paid_result.delivery.checked",
    "Graph reads one delivery's existence checks made",
    "kind",
    "graph_reads",
)

_event(
    "paid_result.existence.checked",
    "Existence checks of paid work, by what decided and the answer",
    "source",
    "result",
)

_event(
    "paid_result.parse.repeated",
    "Parses repeated because the parsing service refused with 410 and the record proved live",
    "kind",
)

_event(
    "paid_result.purge.completed",
    "Paid-result folders purged, by the route that purged them and the outcome",
    "route",
    "outcome",
    "count",
)

_event(
    "paid_result.purge.requested",
    "Storage-service requests made to purge paid-result folders, by route (batch carries up to 100 records, record one)",
    "route",
)

_event(
    "paid_result.repurge.fell_back",
    "Deleted records whose later hops went per record instead of into a window",
    "reason",
)

_event(
    "paid_result.repurge.ids_failed",
    "Ids whose purge still failed after the tail attempts",
    "kind",
)

_event(
    "paid_result.repurge.ids_rejected",
    "Ids a window or page dropped because no result can be stored under them",
    "stage",
)

_event(
    "paid_result.repurge.message_published",
    "Re-purge messages published, by kind",
    "kind",
)

_event(
    "paid_result.repurge.pages_published",
    "Pages published by seals",
    "kind",
    "count",
)

_event(
    "paid_result.repurge.seal_repaired",
    "Overdue seals published again",
    "kind",
)

_event(
    "paid_result.repurge.window_lost",
    "Windows whose ids were gone or replaced when their seal ran",
    "reason",
)

_event(
    "paid_result.repurge.window_sealed",
    "Ids per sealed window",
    "kind",
    "id_count",
)

_event(
    "paid_result.reuse.bumped",
    "Reuse bumps of stored paid results by outcome",
    "outcome",
)

_event(
    "paid_result.sweep.objects_changed",
    "Objects a retention sweep left in place because they changed after it listed them",
    "count",
)

_event(
    "paid_result.work.stopped",
    "Paid calls and stores refused for a deleted record",
    "site",
)

_event(
    "permission.explanation.mismatched",
    "Admin access explanations whose reference walk disagreed with the graph check (the answer keeps the graph's verdict and says unexplained), by the graph's verdict",
    "graph_verdict",
)

_event(
    "permission.scope_fields.failed",
    "Scope fields that could not be computed (the point is left without them and keeps the legacy filter), by where",
    "stage",
)

_event(
    "permission.scope_fields.refused",
    "Records the scope-field stamper refused (they keep the legacy filter), by reason",
    "reason",
)

_event(
    "permission.scope_prefilter.capped",
    "Scoped searches that asked for the most candidates a store allows and still found fewer valid points than the limit (the candidates the query let through were refused by the exact rule), by store",
    "store",
)

_event(
    "permission.scope_prefilter.dropped",
    "Points a store's scope pre-filter returned that the exact rule refuses (Redis compares tags without case and reads the connectors it excludes just before searching), dropped before the answer is returned, by store",
    "store",
    "count",
)

_event(
    "permission.serving.degraded",
    "Searches that a provider answered with less than its full search (searchDegraded), by how they were read: scopes (the scope filter was carried) or legacy",
    "plan",
)

_event(
    "permission.serving.planned",
    "Searches by how the scope filter served them: scopes (stamps decide the current connectors), or the reason the legacy check answered alone (no_user, miss, broad, no_served, timeout, too_large, error)",
    "plan",
)

_event(
    "permission.shadow.compared",
    "Records compared by the shadow scope filter, by outcome: agree (the filtered search and the post-check both admit), over_admit (the filtered search returned a record the post-check refuses: an alert), recall_loss (the post-check admits a record the filtered search did not return: expected for groups without a usable stamp)",
    "outcome",
    "count",
)

_event(
    "permission.shadow.completed",
    "Time the shadow comparison of one search took (it runs after the answer is built), by whether it compared (done) or gave up (skipped)",
    "result",
    "duration_seconds",
)

_event(
    "permission.shadow.skipped",
    "Searches the shadow filter did not compare, by reason (sampled_out, served, busy, scopes_unavailable, no_current_connector, unsupported, too_large, timeout, error)",
    "reason",
)

_event(
    "permission.unreadable_group_rows.counted",
    "pgvector group rows that carry a trusted stamp but a NULL list the stamp's rule needs: a served search hides their points (a data fault; the operator view reports it)",
    "store",
    "org_id",
    "count",
)

_event(
    "permission.viewer_scope_cache.read",
    "Viewer-scope cache lookups by result (hit, miss, bypass when the version is unreadable)",
    "result",
)

_event(
    "permission.viewer_scopes.unavailable",
    "Searches that fell back to the legacy permission filter because viewer scopes could not be computed",
    "reason",
)

_event(
    "record.artifact.read",
    "Record reads from artifact sets, by outcome",
    "outcome",
)

_event(
    "record.artifact.shadow_read",
    "Stored-record reads compared with their artifact set, by outcome",
    "outcome",
    "reason",
)

_event(
    "record.artifact.written",
    "Content revisions indexing tried to store as artifact sets, and their enrichment sidecars, by outcome",
    "outcome",
)

_event(
    "record.properties.projected",
    "Record property projections written at index time, by outcome",
    "outcome",
)

_event(
    "record.property_value.dropped",
    "Property values not projected because they do not fit their property's stored form",
)

_event(
    "record.reader.relooked_up",
    "Stored-record reads that took one fresh mapping lookup after the document read failed, by outcome",
    "outcome",
    "trigger",
)

_event(
    "record.refresh_intent.changed",
    "Force reindex requests kept on a record, by what happened to them",
    "change",
)

_event(
    "record.restored",
    "Records brought back from the trash",
    "source",
    "count",
)

_event(
    "record.retention.swept",
    "What the revision retention sweep removed or kept, by kind and outcome",
    "kind",
    "outcome",
    "count",
)

_event(
    "record.retire_mark.set",
    "Retire marks set on replaced stored documents, by outcome",
    "outcome",
)

_event(
    "record.soft_deleted",
    "Records moved to the trash",
    "source",
    "count",
)

_event(
    "record.storage.rejected_too_large",
    "Records that failed to index because the storage service refused their content as too large",
    "status",
)

_event(
    "record.store.switched",
    "Stored-record writes switching the virtual record's mapping, by layout and outcome",
    "layout",
    "outcome",
    "count",
)

_event(
    "recovery.org_pass.finished",
    "Recovery passes that failed for one organisation, and retry rows with no organisation that were cleared, by job and outcome",
    "job",
    "outcome",
    "count",
)

_event(
    "redis.eviction_policy.checked",
    "1 when the Redis maxmemory-policy can evict the replay set, pause counters and budgets silently",
    "service",
    "unsafe",
)

_event(
    "trash.backlog.measured",
    "Records in the trash: pending the purge, or stuck after too many failed purges",
    "pending",
    "stuck",
    "oldest_pending_age_seconds",
)

_event(
    "trash.purge.finished",
    "Trash purge passes, by how they ended",
    "status",
    "duration_seconds",
)

_event(
    "trash.records.purged",
    "Records the trash purge looked at, by outcome",
    "result",
    "count",
)

_event(
    "vector.blocks.rekeyed",
    "Blocks whose stored points were moved to a content-derived id with their stored vectors, not embedded again",
    "store",
    "count",
)

_event(
    "vector.maintenance.org_skipped",
    "Virtual records a maintenance pass skipped because none of their records names an organisation, by site",
    "site",
)

_event(
    "vector.rekey.fell_back",
    "Blocks embedded again because their stored points could not be moved to a content-derived id",
    "reason",
)

_event(
    "vector_search.admission.rejected",
    "Searches that found no free slot within the wait",
    "slot",
)

_event(
    "vector_search.admission.waited",
    "Time a search waited for an admission slot",
    "slot",
    "wait_seconds",
)

_event(
    "vector_search.leg.returned_empty",
    "Dense legs that returned nothing because even the bounded exact path ran out of time",
    "tier",
)

_event(
    "vector_search.leg.timed_out",
    "Search legs that ran past their budget",
    "leg",
    "tier",
)

_event(
    "vector_search.org_recall.fell_low",
    "Times an organisation's recent sampled recall was below the floor",
    "window",
)

_event(
    "vector_search.quantization.validated",
    "Recall of bit HNSW plus rerank against exact search, per embedding-space check",
    "verdict",
    "recall",
)

_event(
    "vector_search.recall_sample.dropped",
    "Recall samples dropped (queue full, no slot, rate limit, timeout)",
    "reason",
)

_event(
    "vector_search.retried",
    "Vector searches that hit a transient store error, by provider and retry outcome",
    "provider",
    "outcome",
)

_event(
    "web.fetch.completed",
    "fetch_url tool calls, by outcome",
    "outcome",
    "duration_seconds",
)

_event(
    "web.search.completed",
    "web_search tool calls, by provider and outcome",
    "provider",
    "outcome",
    "duration_seconds",
)

_event(
    "web_browser.fetch.rejected",
    "Fetches refused without touching the browser because it could not be relaunched",
    "reason",
)

_event(
    "web_browser.restarted",
    "Relaunches of the shared headless browser, by why it was relaunched and how that went",
    "reason",
    "ok",
)
_event(
    "resource_governor.started",
    "A service's resource governor started; the service keeps it on its FastAPI app state",
    "service",
)
_event(
    "resource_governor.stopped",
    "A service's resource governor stopped and was closed, at shutdown",
    "service",
)
_event(
    "db.query.completed",
    "A storage port call (graph, vector or key-value store) returned or raised",
    "store",
    "operation",
    "duration_seconds",
)
_event(
    "connector.inventory.counted",
    "The active connector instances were counted, by connector type",
    "active_by_type",
)
_event(
    "records.snapshot.taken",
    "The records one org holds were counted, by connector type and indexing status",
    "org_id",
    "email_domain",
    "records_by_connector_status",
    "complete",
)
_event(
    "connector.added",
    "A person added a connector instance",
    "org_id",
    "user_id",
    "user_email",
    "email_domain",
    "connector",
    "scope",
)
_event(
    "connector.sync.start_not_recorded",
    "A sync could not write its start to the connector's document and stopped there",
    "connector",
)
_event(
    "graph.schema.not_ensured",
    "The connector service could not ensure the graph schema at start, after its retries",
)
_event(
    "document.indexed",
    "A record's vector indexing finished (ok) or the vector store did not index it (failed)",
    "status",
    "connector",
    "org_id",
    "kb_id",
    "mime_type",
)
_event(
    "search.performed",
    "A person ran a search",
    "org_id",
    "user_id",
    "user_email",
    "email_domain",
    "status_code",
    "query_count",
    "search_type",
)
_event(
    "retrieval.performed",
    "A retrieval (search results for an agent or an integration) was served",
    "org_id",
    "user_id",
    "email_domain",
    "status_code",
    "query_count",
)
_event(
    "chat.session.started",
    "A person sent a chat message that starts a run",
    "org_id",
    "user_id",
    "user_email",
    "email_domain",
    "chat_mode",
    "search_type",
    "streaming",
)
_event(
    "agent.run.started",
    "A person started an agent run",
    "org_id",
    "user_id",
    "user_email",
    "email_domain",
    "has_tools",
    "streaming",
)
_event(
    "answer.generated",
    "An answer was produced for a chat or agent run: how many citations it carries and from which connectors",
    "org_id",
    "user_id",
    "email_domain",
    "chat_mode",
    "citation_count",
    "connectors",
    "demo_sources",
    "stopped",
)
_event(
    "graph_view.request.completed",
    "A graph view request (expand or revalidate) ended, whatever its outcome, and how much it returned",
    "operation",
    "outcome",
    "duration_seconds",
    "node_count",
    "edge_count",
    "id_count",
)
_event(
    "code_graph.build.settled",
    "A code-graph edge build settled: ready, waiting_on_indexing, too_large, error or lease_lost; the counts only for a build that ran to the end",
    "outcome",
    "mode",
    "duration_seconds",
    "edges_by_relation",
    "external_references",
    "unresolved_references",
    "ambiguous_references",
    "files_without_facts",
)
_event(
    "named_entity.run.persisted",
    "One record's named-entity extraction went through the persist stage: how it ended, how long the stage took and what the run used",
    "strategy",
    "termination_reason",
    "outcome",
    "duration_seconds",
    "entities_by_kind",
    "mentions_by_grounding",
    "dropped_cap",
    "rejected",
    "turns",
    "tool_calls",
    "tokens_in",
    "tokens_out",
)
_event(
    "vector_search.leg.served",
    "A dense search leg was assigned to a tier (e0, e0x, h, h_deep)",
    "tier",
    "leg_size",
)
_event(
    "vector_search.leg.completed",
    "A search leg was answered, by the tier that answered it (degraded: the bounded exact path; lexical)",
    "tier",
    "leg_size",
    "duration_seconds",
)
_event(
    "vector_search.leg.degraded",
    "A search leg was answered by the bounded exact path (admission, timeout) or with rows missing (overload, partial)",
    "reason",
    "tier",
    "leg_size",
)
_event(
    "vector_search.recall.sampled",
    "A sampled dense leg was compared with an exact scan of the same candidates",
    "tier",
    "size_band",
    "share_band",
    "leg_size",
    "recall",
)
_event(
    "indexing.record_waits.changed",
    "The number of pending entries one consumer keeps back, because another delivery holds their record, changed",
    "broker",
    "consumer_id",
    "waiting",
)
_event(
    "indexing.consumer.stopped",
    "An indexing consumer stopped and keeps nothing back any more",
    "broker",
    "consumer_id",
)
_event(
    "indexing.consumer.discarded",
    "An indexing consumer was dropped without a stop. Emitted from a finalizer, on any thread and possibly inside a lock: a listener only notes it",
    "broker",
    "consumer_id",
)
_event(
    "indexing.consumer.polled",
    "An indexing consumer began a consume pass",
    "broker",
)
