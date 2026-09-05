# pyright: ignore-file

"""Seeds a PostgreSQL database with tables for the PostgreSQL connector to sync.

The object-store suites upload files; this one creates tables and inserts rows.
That is the only difference — everything after the data is in place (creating the
connector, waiting for the sync, checking the graph) is shared.

psycopg is used directly rather than through an ORM: the point is to control
exactly what schema the connector sees.
"""

from __future__ import annotations

from typing import List, Sequence

import psycopg


class PostgresSourceHelper:
    """Creates and tears down the tables a connector test syncs from."""

    def __init__(self, dsn: str, schema: str = "pipeshub_test") -> None:
        self._dsn = dsn
        self.schema = schema

    def _connect(self) -> "psycopg.Connection":
        return psycopg.connect(self._dsn, autocommit=True)

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')

    def list_tables(self) -> List[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = %s ORDER BY table_name",
                (self.schema,),
            ).fetchall()
        return [r[0] for r in rows]

    def create_table_with_rows(
        self, table: str, rows: Sequence[tuple[str, str]]
    ) -> None:
        """Create a two-column table and fill it.

        The shape is deliberately dull. This suite tests that the connector
        discovers a table and turns it into a record, not that it handles exotic
        column types.
        """
        with self._connect() as conn:
            conn.execute(
                f'CREATE TABLE IF NOT EXISTS "{self.schema}"."{table}" ('
                "  id serial PRIMARY KEY,"
                "  title text NOT NULL,"
                "  body  text NOT NULL"
                ")"
            )
            conn.execute(f'TRUNCATE "{self.schema}"."{table}"')
            with conn.cursor() as cur:
                cur.executemany(
                    f'INSERT INTO "{self.schema}"."{table}" (title, body) VALUES (%s, %s)',
                    list(rows),
                )

    def insert_rows(self, table: str, rows: Sequence[tuple[str, str]]) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(
                f'INSERT INTO "{self.schema}"."{table}" (title, body) VALUES (%s, %s)',
                list(rows),
            )

    def row_count(self, table: str) -> int:
        with self._connect() as conn:
            return conn.execute(
                f'SELECT count(*) FROM "{self.schema}"."{table}"'
            ).fetchone()[0]

    def clear_objects(self, resource_name: str) -> None:
        """Empty every seeded table.

        Named for the protocol the shared destructor expects: it clears the
        content of the resource the connector synced from, whichever kind of
        resource that is. Here that means truncating the tables rather than
        deleting objects from a bucket.
        """
        del resource_name  # the schema is fixed at construction
        for table in self.list_tables():
            with self._connect() as conn:
                conn.execute(f'TRUNCATE "{self.schema}"."{table}" CASCADE')

    def drop_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
