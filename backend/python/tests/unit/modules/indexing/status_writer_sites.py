"""Every call site of a method that writes a record's status, and every transactional write of records, with what the
site means for the run (``record_diagnostics``) and, for a transactional writer, who runs it again when the records
schema refuses it and the provider aborts the transaction (``RecordSchemaNarrowError``).

``test_status_writer_sites.py`` walks ``backend/python/app`` and fails when a site is missing here, or listed and gone.
A site is keyed ``(file, enclosing function, called method)``.

Known limits of the scan (none occurs in the code today):

* a status key that is not the literal ``"indexingStatus"`` (a constant holding the name);
* raw query text sent from application code (that already breaks the rule against store clients in feature code);
* an entity's ``indexing_status`` assigned before a save: the save goes through ``batch_upsert_records``, which is a listed
  transactional writer, and ``_stamp_queued_at`` stamps the run fields from the stored record.
"""

from __future__ import annotations

# Classes
QUEUE = "QUEUE"  # becomes NOT_STARTED/QUEUED: continues an open run or starts one (the fragment, or the reference on the record held)
STATUS_VAR = "STATUS_VAR"  # the status is a variable: queue rule, failure stamp, or nothing, decided in the statement
ATTEMPT_START = "ATTEMPT_START"  # IN_PROGRESS for a new attempt (write_attempt_start)
FAILED = "FAILED"  # a terminal failure: failedAt
CAS = "CAS"  # a compare-and-set that passes through to a provider method that is itself a listed writer
START = "START"  # a run start whatever the stored status is
SINK_END = "SINK_END"  # the run ends with NOT_STARTED as the outcome: firstQueuedAt cleared
NO_RUN_FIELDS = "NO_RUN_FIELDS"  # changes a status field that is not the run's (within the same attempt, display, extraction)
QUEUE_NO_FILL = "QUEUE_NO_FILL"  # QUEUED without a fill (stale recovery, left unchanged here); readers fall back
DELEGATE = "DELEGATE"  # forwards to a listed provider method
DEAD = "DEAD"  # no caller

CLASSES = {QUEUE, STATUS_VAR, ATTEMPT_START, FAILED, CAS, START, SINK_END, NO_RUN_FIELDS, QUEUE_NO_FILL, DELEGATE, DEAD}

SITES: dict[tuple[str, str, str], str] = {
    ("events/events.py", "EventProcessor._persist_parsing_completed", "update_node_if_unchanged"): NO_RUN_FIELDS,
    ("events/events.py", "EventProcessor._write_page_count", "update_node_fields_if_match"): NO_RUN_FIELDS,
    ("events/events.py", "EventProcessor._restore_identity_after_failed_attach", "update_node"): NO_RUN_FIELDS,
    ("events/events.py", "EventProcessor._reuse_finished_duplicate", "update_record_fields"): NO_RUN_FIELDS,
    ("events/processor.py", "Processor.process_image", "update_node"): NO_RUN_FIELDS,
    ("indexing_main.py", "_republish_stranded_records", "update_node"): NO_RUN_FIELDS,
    ("indexing_main.py", "recover_in_progress_records", "update_node"): QUEUE_NO_FILL,
    ("modules/indexing/duplicate_reconcile.py", "DuplicateReconciler.reconcile", "update_node_fields_if_match"): NO_RUN_FIELDS,
    ("modules/indexing/duplicate_reconcile.py", "_retry_one", "update_node_fields_if_match"): NO_RUN_FIELDS,
    ("modules/transformers/pipeline.py", "IndexingPipeline.apply", "update_node"): NO_RUN_FIELDS,
    ("modules/transformers/sink_orchestrator.py", "SinkOrchestrator._update_indexing_status", "batch_update_nodes"): NO_RUN_FIELDS,
    ("services/graph_db/arango/arango_http_provider.py", "ArangoHTTPProvider.update_queued_duplicates_status", "batch_update_nodes"): STATUS_VAR,
    ("services/graph_db/neo4j/neo4j_provider.py", "Neo4jProvider.update_queued_duplicates_status", "batch_update_nodes"): STATUS_VAR,
    ("connectors/api/router.py", "reindex_single_record", "reindex_single_record"): DELEGATE,
    ("connectors/api/router.py", "reindex_single_record", "compare_and_set_indexing_status"): CAS,
    ("connectors/core/base/data_processor/data_source_entities_processor.py", "DataSourceEntitiesProcessor._process_record", "restore_records"): START,
    ("connectors/core/base/data_processor/data_source_entities_processor.py", "DataSourceEntitiesProcessor._reset_indexing_status_to_queued", "batch_update_nodes"): DEAD,
    ("connectors/core/base/data_processor/data_source_entities_processor.py", "DataSourceEntitiesProcessor._mark_queued_after_publish", "compare_and_set_indexing_status"): CAS,
    ("connectors/core/base/data_processor/data_source_entities_processor.py", "DataSourceEntitiesProcessor.restore_trashed_records", "restore_records"): START,
    ("connectors/core/base/data_processor/storage_cleanup.py", "StorageCleanupHelper.reindex_one_holder", "compare_and_set_indexing_status"): CAS,
    ("connectors/core/base/data_store/graph_data_store.py", "GraphTransactionStore.restore_records", "restore_records"): DELEGATE,
    ("connectors/core/base/data_store/graph_data_store.py", "GraphDataStore.compare_and_set_indexing_status", "compare_and_set_indexing_status"): DELEGATE,
    ("connectors/services/event_service.py", "EventService._reindex_batches", "update_indexing_status_for_record_ids"): STATUS_VAR,
    ("connectors/services/vector_store_rebuild.py", "_reset_app", "reset_indexing_status_for_connector"): STATUS_VAR,
    ("connectors/sources/google/drive/team/connector.py", "GoogleDriveTeamConnector.reindex_records", "compare_and_set_indexing_status"): CAS,
    ("connectors/sources/localKB/api/kb_router.py", "restore_records", "restore_records"): START,
    ("connectors/sources/network_share/operations.py", "requeue_parked_records", "compare_and_set_indexing_status"): CAS,
    ("events/events.py", "EventProcessor._orchestrate_via_services", "update_record_fields"): NO_RUN_FIELDS,
    ("events/events.py", "EventProcessor._cut_writer.write", "update_node_fields_if_match"): QUEUE,
    ("events/events.py", "EventProcessor._display_writer.write", "update_node_fields_if_match"): NO_RUN_FIELDS,
    ("events/events.py", "EventProcessor.update_record_fields", "write_attempt_start"): ATTEMPT_START,
    ("events/events.py", "EventProcessor._check_duplicate_by_md5", "update_record_fields"): QUEUE,
    ("events/events.py", "EventProcessor._attach_to_finished_twin", "update_record_fields"): NO_RUN_FIELDS,
    ("events/events.py", "EventProcessor.on_event", "update_record_fields"): ATTEMPT_START,
    ("indexing_main.py", "_sweep_queued_records_for_inactive_connectors", "update_node"): NO_RUN_FIELDS,
    ("migrations/file_type_requeue/migration.py", "_walk_connector", "transition_unsupported"): STATUS_VAR,
    ("migrations/file_type_requeue/migration.py", "_move_and_send", "transition_unsupported"): STATUS_VAR,
    ("modules/pipeline/duplicates.py", "QueuedCopies._release_one", "update_queued_duplicates_status"): STATUS_VAR,
    ("modules/pipeline/state.py", "GraphHeadlineStatusWriter.set_headline", "compare_and_set_record_fields"): STATUS_VAR,
    ("modules/transformers/sink_orchestrator.py", "SinkOrchestrator.index", "batch_update_nodes"): SINK_END,
    ("services/graph_db/arango/arango_http_provider.py", "ArangoHTTPProvider.reindex_single_record", "update_indexing_status_for_record_ids"): STATUS_VAR,
    ("services/graph_db/neo4j/neo4j_provider.py", "Neo4jProvider.reindex_single_record", "update_indexing_status_for_record_ids"): STATUS_VAR,
    ("services/graph_db/postgres/trash.py", "TrashMixin.reindex_single_record", "update_indexing_status_for_record_ids"): STATUS_VAR,
    ("services/messaging/kafka/handlers/entity.py", "EntityEventService._handle_app_disabled", "reset_indexing_status_for_connector"): STATUS_VAR,
    ("services/messaging/kafka/handlers/record.py", "RecordEventHandler.on_message_abandoned", "compare_and_set_indexing_status"): CAS,
    ("services/messaging/kafka/handlers/record.py", "RecordEventHandler._defer_rate_limited_record", "__update_document_status"): FAILED,
    ("services/messaging/kafka/handlers/record.py", "RecordEventHandler._index_from_blob._fail", "__update_document_status"): FAILED,
    ("services/messaging/kafka/handlers/record.py", "RecordEventHandler._refuse_too_large", "__update_document_status"): FAILED,
    ("services/messaging/kafka/handlers/record.py", "RecordEventHandler.on_message_abandoned", "__update_document_status"): FAILED,
    ("services/messaging/kafka/handlers/record.py", "RecordEventHandler.on_recovery_exhausted", "__update_document_status"): FAILED,
    ("services/messaging/kafka/handlers/record.py", "RecordEventHandler.process_event", "__update_document_status"): FAILED,
    ("services/messaging/kafka/handlers/record.py", "RecordEventHandler._propagate_primary_failure_to_queued_duplicates", "update_queued_duplicates_status"): STATUS_VAR,
    ("services/messaging/kafka/handlers/record.py", "RecordEventHandler._release_queued_copies", "update_queued_duplicates_status"): STATUS_VAR,
    ("services/messaging/kafka/handlers/record.py", "RecordEventHandler._trigger_next_queued_duplicate", "update_queued_duplicates_status"): STATUS_VAR,
}

# Writers of status fields that pass their status in a keyword or a variable through a private helper, by name.
# (enclosing function suffix) -> class; the helper body carries the rule.
HELPER_WRITERS: dict[tuple[str, str], str] = {
    # builds the record the upload route writes (a new record: it starts its run); tests/unit/events/test_record_diagnostics_call_sites.py
    ("api/routes/chatbot.py", "attachment_record_doc"): START,
    ("modules/transformers/sink_orchestrator.py", "sink_only_end_fields"): SINK_END,
    ("services/messaging/kafka/handlers/record.py", "requeue_updates"): QUEUE,
    ("services/messaging/kafka/handlers/record.py", "RecordEventHandler.__update_document_status"): FAILED,
    ("events/processor.py", "Processor._mark_record"): FAILED,
    ("events/events.py", "EventProcessor.mark_record_status"): ATTEMPT_START,
    ("modules/indexing/rate_limit_deferral.py", "deferral_updates"): QUEUE,
    ("services/messaging/kafka/handlers/entity.py", "EntityEventService._requeued_fields"): QUEUE,
}

# Transactional writers of records: who runs the whole transaction again after a RecordSchemaNarrowError.
RETRY = "retry_on_deadlock"  # the public entry point is decorated; the helper below runs inside it
EXECUTE = "execute_in_transaction"  # the block is passed to GraphDataStore.execute_in_transaction
RUN = "run_again"  # a plain transaction block whose error propagates to its caller (sync run or message consumer), which runs the unit again

TX_OWNERS: dict[tuple[str, str], tuple[str, str]] = {
    # data_source_entities_processor: every helper is reached from a public entry decorated with retry_on_deadlock
    ("connectors/core/base/data_processor/data_source_entities_processor.py", "DataSourceEntitiesProcessor._handle_parent_record"): (RETRY, "on_new_records / on_updated_record entries"),
    ("connectors/core/base/data_processor/data_source_entities_processor.py", "DataSourceEntitiesProcessor._handle_related_external_records"): (RETRY, "on_new_records"),
    ("connectors/core/base/data_processor/data_source_entities_processor.py", "DataSourceEntitiesProcessor._reparent_orphans"): (RETRY, "on_new_records"),
    ("connectors/core/base/data_processor/data_source_entities_processor.py", "DataSourceEntitiesProcessor._collect_orphans"): (RETRY, "on_new_records"),
    ("connectors/core/base/data_processor/data_source_entities_processor.py", "DataSourceEntitiesProcessor._handle_new_record"): (RETRY, "on_new_records"),
    ("connectors/core/base/data_processor/data_source_entities_processor.py", "DataSourceEntitiesProcessor._handle_updated_record"): (RETRY, "on_new_records"),
    ("connectors/core/base/data_processor/data_source_entities_processor.py", "DataSourceEntitiesProcessor.on_updated_record_permissions"): (RETRY, "itself"),
    ("connectors/core/base/data_processor/data_source_entities_processor.py", "DataSourceEntitiesProcessor._process_record"): (RETRY, "on_new_records"),
    ("connectors/core/base/data_processor/data_source_entities_processor.py", "DataSourceEntitiesProcessor._reset_indexing_status_to_queued"): (RUN, "dead: no caller"),
    ("connectors/core/base/data_processor/data_source_entities_processor.py", "DataSourceEntitiesProcessor.on_pushed_record"): (RUN, "the push request: the error propagates to the API caller, which sends it again"),
    ("connectors/core/base/data_processor/data_source_entities_processor.py", "DataSourceEntitiesProcessor.on_record_metadata_update"): (RETRY, "itself"),
    ("connectors/core/base/data_processor/data_source_entities_processor.py", "DataSourceEntitiesProcessor.on_records_moved"): (RETRY, "itself"),
    ("connectors/core/base/data_processor/data_source_entities_processor.py", "DataSourceEntitiesProcessor.restore_trashed_records"): (RETRY, "itself"),
    ("connectors/core/base/data_processor/data_source_entities_processor.py", "DataSourceEntitiesProcessor.on_records_detached_from_parent"): (RETRY, "itself"),
    ("connectors/core/base/data_processor/data_source_entities_processor.py", "DataSourceEntitiesProcessor.batch_upsert_records"): (RETRY, "itself"),
    ("connectors/core/base/data_store/graph_data_store.py", "GraphTransactionStore.restore_records"): (DELEGATE, "the caller of the transaction store"),
    ("connectors/core/base/data_store/graph_data_store.py", "GraphTransactionStore.batch_upsert_records"): (DELEGATE, "the caller of the transaction store"),
    ("connectors/core/sql/sync_lifecycle.py", "SqlSyncLifecycleMixin._move_renamed_tables"): (EXECUTE, "sync_lifecycle.py execute_in_transaction"),
    ("connectors/core/sql/sync_lifecycle.py", "SqlSyncLifecycleMixin._move_renamed_columns"): (EXECUTE, "sync_lifecycle.py execute_in_transaction"),
    ("connectors/sources/gitlab/repos.py", "ReposSync._run_code_file_timestamp_backfill"): (EXECUTE, "repos.py execute_in_transaction"),
    ("connectors/sources/notion/connector.py", "NotionConnector._detach_placeholder_children"): (EXECUTE, "notion connector execute_in_transaction"),
    ("connectors/sources/notion/connector.py", "NotionConnector._rehome_children.rehome"): (EXECUTE, "notion connector execute_in_transaction"),
    ("connectors/sources/postgres/connector.py", "PostgreSQLConnector._rename_tables"): (EXECUTE, "postgres connector execute_in_transaction"),
    ("connectors/sources/postgres/connector.py", "PostgreSQLConnector._rename_column_records"): (EXECUTE, "postgres connector execute_in_transaction"),
    ("modules/transformers/graphdb.py", "GraphDBTransformer.apply"): (RUN, "the indexing message: the error is classified transient (services/messaging/error_classifier.py) and the handler reverts the record to QUEUED and the consumer redelivers; tests/unit/services/messaging/test_record_handler.py"),
    ("modules/transformers/graphdb.py", "GraphDBTransformer._write_record_metadata"): (RUN, "the indexing message: the error is classified transient (services/messaging/error_classifier.py) and the handler reverts the record to QUEUED and the consumer redelivers; tests/unit/services/messaging/test_record_handler.py"),
}
OWNERS = {RETRY, EXECUTE, RUN, DELEGATE}
