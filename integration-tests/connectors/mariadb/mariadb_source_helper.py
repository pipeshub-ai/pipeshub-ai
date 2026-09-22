# pyright: ignore-file

"""Seeds a MariaDB database with tables for the MariaDB connector to sync.

The PostgreSQL suite does the same with psycopg. MariaDB has no schemas inside
a database, so everything here lives directly in the one database the
connector is configured for, and the helper owns only the tables it creates.

PyMySQL is used rather than the connector's own driver: it is pure Python, so
the test process needs no MariaDB client library, and the point is to control
exactly what the connector sees, not to exercise the driver twice.
"""

from __future__ import annotations

from collections.abc import Sequence

import pymysql


class MariaDBSourceHelper:
    """Creates, changes and tears down the tables a connector test syncs from."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        connect_timeout: int = 10,
    ) -> None:
        self.database = database
        self._params = {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "database": database,
            "autocommit": True,
            # Bounded for the same reason as the PostgreSQL helper: the fixture
            # decides between skip and fail on a connection error, and a host
            # that accepts TCP but never answers would hang instead.
            "connect_timeout": connect_timeout,
        }
        self._created: list[str] = []

    def _execute(self, statement: str, args: Sequence | None = None) -> list[tuple]:
        conn = pymysql.connect(**self._params)
        try:
            with conn.cursor() as cur:
                cur.execute(statement, args)
                return list(cur.fetchall())
        finally:
            conn.close()

    def _executemany(self, statement: str, rows: Sequence[Sequence]) -> None:
        conn = pymysql.connect(**self._params)
        try:
            with conn.cursor() as cur:
                cur.executemany(statement, list(rows))
        finally:
            conn.close()

    def ping(self) -> None:
        self._execute("SELECT 1")

    def fqn(self, table: str) -> str:
        """The id the connector gives a table's record."""
        return f"{self.database}.{table}"

    def _track(self, name: str) -> None:
        if name not in self._created:
            self._created.append(name)

    def create_table_with_rows(
        self,
        table: str,
        rows: Sequence[tuple[str, str]],
        *,
        primary_key: bool = True,
    ) -> None:
        """Create a two-column table and fill it.

        ``primary_key=False`` leaves out the id column: change detection must
        still notice an update to a table with no key and no auto-increment.
        """
        key = "id INT AUTO_INCREMENT PRIMARY KEY, " if primary_key else ""
        self._execute(
            f"CREATE TABLE IF NOT EXISTS `{table}` ("
            f"{key}title VARCHAR(200) NOT NULL, body TEXT NOT NULL)"
        )
        self._track(table)
        self._execute(f"DELETE FROM `{table}`")
        self.insert_rows(table, rows)

    def create_child_table(self, table: str, parent: str) -> None:
        """A table with a foreign key to ``parent``'s id."""
        self._execute(
            f"CREATE TABLE IF NOT EXISTS `{table}` ("
            "id INT AUTO_INCREMENT PRIMARY KEY, "
            "parent_id INT NOT NULL, note VARCHAR(200) NOT NULL, "
            f"FOREIGN KEY (parent_id) REFERENCES `{parent}`(id))"
        )
        self._track(table)

    def create_view(self, view: str, table: str) -> None:
        self._execute(f"CREATE OR REPLACE VIEW `{view}` AS SELECT title FROM `{table}`")
        self._track(view)

    def insert_rows(self, table: str, rows: Sequence[tuple[str, str]]) -> None:
        self._executemany(
            f"INSERT INTO `{table}` (title, body) VALUES (%s, %s)", rows
        )

    def update_body(self, table: str, title: str, body: str) -> int:
        """Change one row's body in place; returns the rows changed."""
        conn = pymysql.connect(**self._params)
        try:
            with conn.cursor() as cur:
                return cur.execute(
                    f"UPDATE `{table}` SET body = %s WHERE title = %s", (body, title)
                )
        finally:
            conn.close()

    def drop_table(self, table: str) -> None:
        self._execute(f"DROP TABLE IF EXISTS `{table}`")

    def row_count(self, table: str) -> int:
        return self._execute(f"SELECT COUNT(*) FROM `{table}`")[0][0]

    def list_tables(self) -> list[str]:
        rows = self._execute(
            "SELECT TABLE_NAME FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME",
            (self.database,),
        )
        return [r[0] for r in rows]

    def clear_objects(self, resource_name: str) -> None:
        """Empty every table this helper created.

        Named for the protocol the shared destructor expects, as in the
        PostgreSQL helper: it clears the content of the synced resource.
        """
        del resource_name  # the database is fixed at construction
        existing = set(self.list_tables())
        views = set(self._views())
        for name in reversed(self._created):
            if name in existing and name not in views:
                self._execute(f"DELETE FROM `{name}`")

    def _views(self) -> list[str]:
        rows = self._execute(
            "SELECT TABLE_NAME FROM information_schema.VIEWS WHERE TABLE_SCHEMA = %s",
            (self.database,),
        )
        return [r[0] for r in rows]

    def reset(self, *, tables: Sequence[str], views: Sequence[str] = ()) -> None:
        """Drop leftovers of an earlier run that stopped before its teardown.

        ``tables`` are dropped in the order given, so list children first.
        """
        for view in views:
            self._execute(f"DROP VIEW IF EXISTS `{view}`")
        for table in tables:
            self._execute(f"DROP TABLE IF EXISTS `{table}`")

    def drop_created(self) -> None:
        """Drop what this helper created, children and views first."""
        views = set(self._views())
        for name in reversed(self._created):
            kind = "VIEW" if name in views else "TABLE"
            self._execute(f"DROP {kind} IF EXISTS `{name}`")
        self._created.clear()
