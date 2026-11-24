"""Platform events this plug-in listens to, and what each one records.

One handler per event, in ``handlers()``. Most events map straight onto one
instrument: the event's fields are its label values, in the order given here.
Every handler is non-blocking: it increments a counter, observes a histogram,
sets a gauge or appends to the bounded event buffer.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import cache
from typing import TYPE_CHECKING, Any

from app import platform_events

if TYPE_CHECKING:
    from app.plugins.telemetry.backend import (
        CounterHandle,
        GaugeHandle,
        HistogramHandle,
    )

Subscribe = Callable[[str, platform_events.Handler], Callable[[], None]]
Payload = Mapping[str, Any]
Handler = platform_events.Handler


def subscribe(on: Subscribe) -> None:
    """Subscribe every handler through ``on`` unless this process already has.

    Several services are imported into one process in tests, each loading the
    plug-ins; a second set of the same handlers would count every event twice.
    """
    for name, handler in handlers().items():
        if not platform_events.is_listening(name, handler):
            on(name, handler)


def _counter(instrument: CounterHandle, *fields: str, value: str | None = None) -> Handler:
    """Increment by one, or by the event's ``value`` field when it carries one."""

    def handle(_name: str, payload: Payload) -> None:
        amount = 1.0 if value is None else payload.get(value, 1.0)
        instrument.inc(*[payload[field] for field in fields], value=amount)

    return handle


def _histogram(instrument: HistogramHandle, *fields: str, value: str) -> Handler:
    def handle(_name: str, payload: Payload) -> None:
        instrument.observe(*[payload[field] for field in fields], value=payload[value])

    return handle


def _gauge(instrument: GaugeHandle, *fields: str, value: str) -> Handler:
    def handle(_name: str, payload: Payload) -> None:
        instrument.set(*[payload[field] for field in fields], value=float(payload[value]))

    return handle


def _call(function: Callable[..., None], **renamed: str) -> Handler:
    """Call ``function`` with the event's fields as keyword arguments; ``field="parameter"`` where the names differ."""

    def handle(_name: str, payload: Payload) -> None:
        function(**{renamed.get(field, field): fact for field, fact in payload.items()})

    return handle


def _each(*handlers_: Handler) -> Handler:
    def handle(name: str, payload: Payload) -> None:
        for handler in handlers_:
            handler(name, payload)

    return handle


@cache
def handlers() -> dict[str, Handler]:
    """Event name -> handler, built once (importing the metric modules creates their instruments)."""
    from app.plugins.telemetry import product_events
    from app.plugins.telemetry.modules import (
        activity_metrics,
        code_graph_metrics,
        connector_metrics,
        db_metrics,
        entity_index_metrics,
        entity_resolution_metrics,
        envelope_metrics,
        graph_schema_guard_metrics,
        graph_view_metrics,
        indexing_metrics,
        kg_metrics,
        knowledge_hub_metrics,
        llm_gateway_metrics,
        named_entity_metrics,
        org_context_metrics,
        paid_cache_metrics,
        permissions_metrics,
        pg_runtime_metrics,
        pgvector_search_metrics,
        record_storage_metrics,
        scheduling_metrics,
        schema_metrics,
        soft_delete_metrics,
        vector_org_metrics,
        vector_search_metrics,
        web_browser_metrics,
        web_metrics,
    )

    return {
        **product_events.handlers(),
        "db.query.completed": _histogram(db_metrics.DB_QUERY_SECONDS, "store", "operation", value="duration_seconds"),
        "connector.inventory.counted": _call(connector_metrics.set_connector_active, active_by_type="counts"),
        "graph_view.request.completed": _call(
            graph_view_metrics.record, duration_seconds="seconds", node_count="nodes", edge_count="edges", id_count="ids"
        ),
        "code_graph.build.settled": _call(code_graph_metrics.record_build),
        "named_entity.run.persisted": _call(named_entity_metrics.record_run),
        "vector_search.leg.served": _call(pgvector_search_metrics.record_served),
        "vector_search.leg.completed": _call(pgvector_search_metrics.record_leg),
        "vector_search.leg.degraded": _call(pgvector_search_metrics.record_degraded),
        "vector_search.recall.sampled": _call(pgvector_search_metrics.record_recall),
        "indexing.record_waits.changed": _call(scheduling_metrics.record_record_waits, waiting="count"),
        "indexing.consumer.stopped": _call(scheduling_metrics.forget_record_waits),
        "indexing.consumer.discarded": _call(scheduling_metrics.note_consumer_discarded),
        "indexing.consumer.polled": _call(scheduling_metrics.refresh_record_waits),
        "document.indexed": _call(activity_metrics.record_document_indexed),
        "connector.sync.start_not_recorded": _call(activity_metrics.record_sync_start_not_recorded),
        "graph.schema.not_ensured": _call(activity_metrics.record_graph_schema_not_ensured),
        "llm.call.finished": _counter(llm_gateway_metrics.LLM_CALLS, "call_site", "outcome"),
        "llm.tokens.used": _counter(llm_gateway_metrics.LLM_TOKENS, "call_site", "direction", value="count"),
        "llm.circuit.changed": _gauge(llm_gateway_metrics.LLM_BREAKER_OPEN, "provider", value="open"),
        "vector_search.leg.returned_empty": _counter(pgvector_search_metrics.EMPTY_LEG, "tier"),
        "vector_search.admission.waited": _histogram(pgvector_search_metrics.ADMISSION_WAIT, "slot", value="wait_seconds"),
        "vector_search.admission.rejected": _counter(pgvector_search_metrics.ADMISSION_REJECTED, "slot"),
        "vector_search.leg.timed_out": _counter(pgvector_search_metrics.TIMEOUT, "leg", "tier"),
        "vector_search.recall_sample.dropped": _counter(pgvector_search_metrics.SAMPLE_DROPPED, "reason"),
        "vector_search.org_recall.fell_low": _counter(pgvector_search_metrics.ORG_RECALL_LOW, "window"),
        "vector_search.quantization.validated": _histogram(pgvector_search_metrics.QUANTIZATION_RECALL, "verdict", value="recall"),
        "db.capability.downgraded": _counter(pg_runtime_metrics.DOWNGRADES, "capability", "mode"),
        "db.statement.completed": _histogram(pg_runtime_metrics.QUERY_SECONDS, "service", "site", "statement_class", value="duration_seconds"),
        "db.unit_of_work.completed": _histogram(pg_runtime_metrics.UOW_SECONDS, "service", "outcome", value="duration_seconds"),
        "db.connection.acquired": _histogram(pg_runtime_metrics.POOL_WAIT, "service", value="wait_seconds"),
        "db.unit_of_work.retried": _counter(pg_runtime_metrics.RETRIES, "error_code"),
        "db.session_role.leaked": _counter(pg_runtime_metrics.ROLE_LEAKS, "service"),
        "db.unit_of_work.started_without_org": _counter(pg_runtime_metrics.ORG_MISSING, "service"),
        "message.envelope.accepted_without_context": _counter(envelope_metrics.CTX_MISSING, "topic"),
        "message.envelope.expired": _each(_counter(envelope_metrics.STALE_BY_TYPE, "topic", "event_type"), _counter(envelope_metrics.STALE, "topic")),
        "message.envelope.processed_unverified": _counter(envelope_metrics.UNVERIFIED, "topic"),
        "message.envelope.rejected": _counter(envelope_metrics.REJECTED, "topic", "reason", "event_type"),
        "message.admission.refused": _counter(envelope_metrics.ADMISSION_REFUSED, "topic"),
        "message.envelope.stripped": _counter(envelope_metrics.STRIPPED, "topic"),
        "message.sent_unsigned": _counter(envelope_metrics.UNSIGNED, "topic", "reason"),
        "message.inbox.failed": _counter(envelope_metrics.INBOX_ERRORS, "operation"),
        "org_context.violated": _counter(org_context_metrics.VIOLATIONS, "kind", "site"),
        "indexing.keep_old_copy.refused": _counter(indexing_metrics.SWAP_REFUSALS, "reason"),
        "document.page_count.unavailable": _counter(indexing_metrics.PAGE_COUNT_UNAVAILABLE),
        "record.refresh_intent.changed": _counter(indexing_metrics.REFRESH_INTENT_EVENTS, "change"),
        "vector.blocks.rekeyed": _counter(indexing_metrics.REKEYED, "store", value="count"),
        "vector.rekey.fell_back": _counter(indexing_metrics.REKEY_FALLBACK, "reason"),
        "indexing.replay_set.failed": _counter(indexing_metrics.REPLAY_SET_ERRORS, "operation"),
        "indexing.replay.acknowledged": _counter(indexing_metrics.REPLAYS_ACKNOWLEDGED, "service"),
        "redis.eviction_policy.checked": _gauge(indexing_metrics.EVICTION_POLICY_UNSAFE, "service", value="unsafe"),
        "graph.schema.widened": _each(_counter(graph_schema_guard_metrics.REWIDEN_TOTAL, "collection", "trigger"), _histogram(graph_schema_guard_metrics.REWIDEN_WAIT_SECONDS, "collection", "trigger", value="wait_seconds")),
        "graph.schema.widening_deferred": _counter(graph_schema_guard_metrics.REWIDEN_CONTENDED_TOTAL, "collection", "trigger"),
        "knowledge_hub.cross_grants.bounded": _counter(knowledge_hub_metrics.KH_CROSS_FALLBACKS, "outcome"),
        "knowledge_graph.backfill.batch_committed": _counter(kg_metrics.BACKFILL_BATCHES, "scope"),
        "knowledge_graph.backfill.verified": _counter(kg_metrics.BACKFILL_FLIPS, "outcome"),
        "knowledge_graph.connector.bootstrapped": _counter(kg_metrics.BOOTSTRAP_PROMOTED, "outcome"),
        "knowledge_graph.counts.checked": _counter(kg_metrics.COUNTS_CHECKED, "result"),
        "knowledge_graph.changes.drained": _counter(kg_metrics.DRAINER_DRAINED, "stage", value="count"),
        "knowledge_graph.drain.failed": _counter(kg_metrics.DRAINER_LAG, "stage"),
        "knowledge_graph.drain.deferred": _counter(kg_metrics.DRAINER_RETRY_LATER, "stage"),
        "knowledge_graph.flush.failed": _counter(kg_metrics.FLUSH_FLUSH_FAILED, "stage"),
        "knowledge_graph.nodes.flushed": _counter(kg_metrics.FLUSH_FLUSHED, "stage", value="count"),
        "knowledge_graph.write.unclassified": _counter(kg_metrics.GUARD_UNCLASSIFIED, "method"),
        "knowledge_graph.hierarchy.answered": _counter(kg_metrics.HIERARCHY, "query", "answer"),
        "knowledge_graph.legacy_records.counted": _gauge(kg_metrics.LEGACY_LEGACY, "org_id", "connector_id", "reason", value="count"),
        "knowledge_graph.fence.measured": _gauge(kg_metrics.LEGACY_FENCE_AGE, "org_id", value="oldest_seconds"),
        "knowledge_graph.restamp_queue.counted": _gauge(kg_metrics.LEGACY_QUEUE, "org_id", "state", value="count"),
        "knowledge_graph.quarantine.counted": _gauge(kg_metrics.LEGACY_QUARANTINED, "org_id", value="count"),
        "knowledge_graph.change_log.counted": _gauge(kg_metrics.LEGACY_LAG, "org_id", value="count"),
        "knowledge_graph.net.checked": _counter(kg_metrics.NET_NET, "source", "result"),
        "knowledge_graph.net.verified": _gauge(kg_metrics.NET_LAST_VERIFIED, "org_id", "connector_id", value="verified_at"),
        "knowledge_graph.net.overdue_counted": _gauge(kg_metrics.NET_OVERDUE, "org_id", value="count"),
        "knowledge_graph.net.older_epoch_counted": _gauge(kg_metrics.NET_OLDER, "org_id", value="count"),
        "knowledge_graph.connector.demoted": _counter(kg_metrics.NET_DEMOTED, "source"),
        "knowledge_graph.row.refused": _counter(kg_metrics.PROJECT_REFUSED, "collection"),
        "knowledge_graph.access.decided": _counter(kg_metrics.READ_ANSWERS, "answer", value="count"),
        "knowledge_graph.access.fell_back": _counter(kg_metrics.READ_FALLBACKS, "reason"),
        "knowledge_graph.restamp.queued": _counter(kg_metrics.RESTAMP_JOBS, "kind"),
        "knowledge_graph.restamp.batch_committed": _counter(kg_metrics.RESTAMP_BATCHES, "kind"),
        "knowledge_graph.restamp.batch_failed": _counter(kg_metrics.RESTAMP_FAILED, "kind"),
        "knowledge_graph.worker.iteration_failed": _counter(kg_metrics.WORKER_LOOP_FAILED, "stage"),
        "paid_result.reuse.bumped": _counter(paid_cache_metrics.BUMP_TOTAL, "outcome"),
        "paid_result.cache.accessed": _counter(paid_cache_metrics.PAID_CACHE_TOTAL, "kind", "outcome"),
        "paid_result.existence.checked": _counter(paid_cache_metrics.ORPHAN_CHECKS, "source", "result"),
        "paid_result.delivery.checked": _histogram(paid_cache_metrics.ORPHAN_GRAPH_READS, "kind", value="graph_reads"),
        "paid_result.parse.repeated": _counter(paid_cache_metrics.ORPHAN_REPARSES, "kind"),
        "paid_result.work.stopped": _counter(paid_cache_metrics.ORPHAN_STOPPED, "site"),
        "paid_result.repurge.message_published": _counter(paid_cache_metrics.REPURGE_MESSAGES, "kind"),
        "paid_result.repurge.fell_back": _counter(paid_cache_metrics.REPURGE_FALLBACK, "reason"),
        "paid_result.repurge.window_lost": _counter(paid_cache_metrics.REPURGE_WINDOW_LOST, "reason"),
        "paid_result.repurge.seal_repaired": _counter(paid_cache_metrics.REPURGE_SEAL_REPAIRED, "kind"),
        "paid_result.repurge.ids_rejected": _counter(paid_cache_metrics.REPURGE_INVALID_IDS, "stage"),
        "paid_result.repurge.ids_failed": _counter(paid_cache_metrics.REPURGE_FAILED_IDS, "kind"),
        "paid_result.repurge.window_sealed": _histogram(paid_cache_metrics.REPURGE_WINDOW_IDS, "kind", value="id_count"),
        "paid_result.repurge.pages_published": _counter(paid_cache_metrics.REPURGE_PAGES, "kind", value="count"),
        "paid_result.sweep.objects_changed": _counter(paid_cache_metrics.SWEEP_CHANGED, value="count"),
        "paid_result.purge.requested": _counter(paid_cache_metrics.STORE_PURGE_REQUESTS, "route"),
        "paid_result.purge.completed": _counter(paid_cache_metrics.STORE_PURGE_RECORDS, "route", "outcome", value="count"),
        "permission.viewer_scopes.unavailable": _counter(permissions_metrics.SCOPES_UNAVAILABLE, "reason"),
        "permission.viewer_scope_cache.read": _counter(permissions_metrics.SCOPE_CACHE, "result"),
        "permission.scope_fields.refused": _counter(permissions_metrics.SCOPE_REFUSED, "reason"),
        "permission.scope_fields.failed": _counter(permissions_metrics.SCOPE_STAMP_FAILED, "stage"),
        "permission.scope_prefilter.dropped": _counter(permissions_metrics.SCOPE_PREFILTER_DROPPED, "store", value="count"),
        "permission.scope_prefilter.capped": _counter(permissions_metrics.SCOPE_PREFILTER_CAPPED, "store"),
        "permission.shadow.compared": _counter(permissions_metrics.SHADOW_COMPARED, "outcome", value="count"),
        "permission.shadow.skipped": _counter(permissions_metrics.SHADOW_SKIPPED, "reason"),
        "permission.shadow.completed": _histogram(permissions_metrics.SHADOW_SECONDS, "result", value="duration_seconds"),
        "permission.serving.planned": _counter(permissions_metrics.SERVING_PLAN, "plan"),
        "permission.serving.degraded": _counter(permissions_metrics.SERVING_DEGRADED, "plan"),
        "permission.unreadable_group_rows.counted": _gauge(permissions_metrics.SERVING_UNREADABLE_ROWS, "store", "org_id", value="count"),
        "permission.explanation.mismatched": _counter(permissions_metrics.EXPLAIN_MISMATCH, "graph_verdict"),
        "vector.maintenance.org_skipped": _counter(vector_org_metrics.ORG_SKIPPED, "site"),
        "code_graph.tool.called": _call(code_graph_metrics.record_tool_call),
        "code_graph.build.request_deferred": _call(code_graph_metrics.record_busy_request),
        "code_graph.builds.reconciled": _call(code_graph_metrics.record_reconcile_sweep),
        "entity_index.entities.written": _call(entity_index_metrics.record_writes),
        "entity_resolution.names.resolved": _call(entity_resolution_metrics.record_name_outcome),
        "entity_resolution.model.called": _call(entity_resolution_metrics.record_model_call),
        "entity_resolution.names.fell_back": _call(entity_resolution_metrics.record_fallback),
        "entity_resolution.lookup.failed": _call(entity_resolution_metrics.record_lookup_error),
        "entity_resolution.record.resolved": _call(entity_resolution_metrics.record_latency, duration_seconds="seconds"),
        "named_entity.extraction.fell_back": _call(named_entity_metrics.record_fallback),
        "named_entity.schema.stepped_down": _call(named_entity_metrics.record_step_down),
        "named_entity.extraction.completed": _call(named_entity_metrics.observe_extract, duration_seconds="seconds"),
        "named_entity.orphans.swept": _call(named_entity_metrics.record_sweep),
        "record.storage.rejected_too_large": _call(record_storage_metrics.record_rejected_too_large),
        "record.artifact.written": _call(record_storage_metrics.record_artifact_write),
        "record.artifact.read": _call(record_storage_metrics.record_artifact_read),
        "record.artifact.shadow_read": _call(record_storage_metrics.record_artifact_shadow_read),
        "record.retention.swept": _call(record_storage_metrics.record_retention),
        "recovery.org_pass.finished": _call(record_storage_metrics.record_recovery_org_pass),
        "record.reader.relooked_up": _call(record_storage_metrics.record_reader_relookup),
        "record.store.switched": _call(record_storage_metrics.record_store_switch),
        "record.retire_mark.set": _call(record_storage_metrics.record_retire_mark),
        "record.properties.projected": _call(record_storage_metrics.record_property_projection),
        "record.property_value.dropped": _call(record_storage_metrics.record_property_value_dropped),
        "indexing.watermark.measured": _call(scheduling_metrics.record_watermark_lag),
        "indexing.scheduler.measured": _call(scheduling_metrics.record_scheduler_depth),
        "indexing.record.dispatched": _call(scheduling_metrics.record_dispatch, org_id="org"),
        "indexing.read.deferred": _call(scheduling_metrics.record_deferred),
        "indexing.lanes.paused_counted": _call(scheduling_metrics.record_lanes_paused),
        "indexing.lane_assignment.fell_back": _call(scheduling_metrics.record_lane_assignment_fallback),
        "indexing.lane.connectors_counted": _call(scheduling_metrics.record_lane_connectors),
        "indexing.lane.oldest_waiting_measured": _call(scheduling_metrics.record_lane_oldest_waiting, age_seconds="seconds"),
        "indexing.dwell.exceeded": _call(scheduling_metrics.record_dwell_exceeded),
        "indexing.fairness_key.missed": _call(scheduling_metrics.record_missing_key, key_field="field"),
        "indexing.gate.waiters_counted": _call(scheduling_metrics.record_gate_waiters),
        "indexing.connector_off.settled": _call(scheduling_metrics.record_connector_off_settled),
        "graph.write.rejected": _call(schema_metrics.record_schema_rejection),
        "record.soft_deleted": _call(soft_delete_metrics.record_soft_deleted),
        "record.restored": _call(soft_delete_metrics.record_restored),
        "trash.purge.finished": _call(soft_delete_metrics.record_purge_run),
        "trash.records.purged": _call(soft_delete_metrics.record_purged),
        "trash.backlog.measured": _call(soft_delete_metrics.set_trash_backlog),
        "vector_search.retried": _call(vector_search_metrics.record_search_retry),
        "web_browser.restarted": _call(web_browser_metrics.record_restart),
        "web_browser.fetch.rejected": _call(web_browser_metrics.record_rejected_fetch),
        "web.search.completed": _call(web_metrics.record_search, duration_seconds="seconds"),
        "web.fetch.completed": _call(web_metrics.record_fetch, duration_seconds="seconds"),
    }
