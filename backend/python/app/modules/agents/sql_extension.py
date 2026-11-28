"""Edition hooks for the SQL tables and query results an agent reads.

`get_sql_extension()` returns the hooks the running edition installs (see
`app.edition_services.sql_extension`). `SqlExtension` itself is the default: a
query's full result rows are saved in the background as a CSV file of the
conversation, a fetched SQL table is given the tables its foreign keys link it
to, and no table is given concept context. An edition only overrides what it
changes.

Imports here stay lazy: `app.edition_services` imports this module early.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Mapping
    from logging import Logger

    from app.config.configuration_service import ConfigurationService
    from app.modules.transformers.blob_storage import BlobStorage
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = ["SqlExtension", "default_sql_extension", "get_sql_extension"]


class SqlExtension:
    async def keep_query_result(
        self,
        result: dict[str, Any],
        *,
        columns: list[str] | None,
        rows: list[tuple] | None,
        source_name: str,
        blob_store: BlobStorage | None,
        graph_provider: IGraphDBProvider,
        config_service: ConfigurationService,
        org_id: str | None,
        user_id: str | None,
        artifact_owner_user_id: str | None,
        conversation_id: str | None,
        run_id: str | None,
        logger: Logger,
    ) -> None:
        """What becomes of a SQL query's full result rows (`columns`, `rows`), of which
        `result`, the tool's answer, shows only the first ones. `user_id` is whose
        access the query ran with; what is kept belongs to `artifact_owner_user_id`
        when given. Default: a CSV of them, saved in the background as a file of the
        conversation and stamped with the turn's `run_id`."""
        from app.utils.execute_query import schedule_result_csv

        schedule_result_csv(
            result, columns=columns, rows=rows, blob_store=blob_store, graph_provider=graph_provider,
            config_service=config_service, org_id=org_id, user_id=artifact_owner_user_id or user_id,
            conversation_id=conversation_id, run_id=run_id,
        )

    async def enrich_sql_table(
        self,
        record: dict[str, Any],
        graph_provider: IGraphDBProvider,
        *,
        user_id: str,
        org_id: str,
        connection_sources: Mapping[str, Iterable[str]] | None,
        blob_store: Callable[[], Awaitable[BlobStorage | None]],
    ) -> dict[str, Any]:
        """A SQL table record fetched for the agent, with what it is told about the
        table besides its rows. `blob_store` gives the blob store, for an edition
        that reads one. Default: the tables its foreign keys link it to."""
        from app.utils.fetch_full_record import _enrich_sql_table_with_fk_relations

        return await _enrich_sql_table_with_fk_relations(
            record, graph_provider, user_id, org_id, connection_sources=connection_sources,
        )

    async def concept_context(
        self, record_id: str, graph_provider: IGraphDBProvider, *, org_id: str = "", user_id: str = "",
    ) -> str:
        """Text for the model naming the business concepts a SQL table shares with
        tables `user_id` may read, for the SQL tables a search turned up.
        Default: none."""
        return ""


_DEFAULT = SqlExtension()


def default_sql_extension() -> SqlExtension:
    return _DEFAULT


def get_sql_extension() -> SqlExtension:
    from app import edition_services

    return edition_services.sql_extension()
