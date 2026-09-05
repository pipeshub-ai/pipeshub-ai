# pyright: ignore-file

"""Seeds a MariaDB database with tables for the MariaDB connector to sync.

pymysql is used here rather than the `mariadb` package the product depends on.
The product's driver needs the MariaDB Connector/C system library; this helper
only has to create tables and insert rows, and a pure-Python driver keeps the
integration-test environment free of a system dependency. What the connector
uses to read the data is unaffected — that is the code under test.
"""

from __future__ import annotations

from collections.abc import Sequence

import pymysql


class MariaDBSourceHelper:
    """Creates and tears down the tables a connector test syncs from."""

    def __init__(
        self, host: str, port: int, user: str, password: str, database: str
    ) -> None:
        self._conn_args = dict(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            autocommit=True,
        )
        self.database = database

    def _connect(self):
        return pymysql.connect(**self._conn_args)

    def ping(self) -> None:
        """Raise if the server is not reachable."""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()

    def list_tables(self) -> list[str]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = %s ORDER BY table_name",
                (self.database,),
            )
            return [r[0] for r in cur.fetchall()]

    def create_table_with_rows(
        self, table: str, rows: Sequence[tuple[str, str]]
    ) -> None:
        """Create a two-column table and fill it.

        Deliberately dull: this suite tests that the connector discovers a table
        and turns it into a record, not that it handles exotic column types.
        """
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS `{table}` ("
                "  id INT AUTO_INCREMENT PRIMARY KEY,"
                "  title VARCHAR(255) NOT NULL,"
                "  body  TEXT NOT NULL"
                ") ENGINE=InnoDB"
            )
            cur.execute(f"TRUNCATE TABLE `{table}`")
            cur.executemany(
                f"INSERT INTO `{table}` (title, body) VALUES (%s, %s)", list(rows)
            )

    def insert_rows(self, table: str, rows: Sequence[tuple[str, str]]) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(
                f"INSERT INTO `{table}` (title, body) VALUES (%s, %s)", list(rows)
            )

    def row_count(self, table: str) -> int:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM `{table}`")
            return cur.fetchone()[0]

    def clear_objects(self, resource_name: str) -> None:
        """Empty every seeded table.

        Named for the protocol the shared destructor expects: clear the content
        of the resource the connector synced from. Here that is the tables.
        """
        del resource_name  # the database is fixed at construction
        for table in self.list_tables():
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(f"TRUNCATE TABLE `{table}`")

    def drop_tables(self) -> None:
        for table in self.list_tables():
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS `{table}`")
