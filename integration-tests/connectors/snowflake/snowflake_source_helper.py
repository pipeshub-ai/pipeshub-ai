# pyright: ignore-file

"""Creates, changes and drops the objects the Snowflake connector syncs from.

Snowflake has no container to run in the stack, so this suite needs a real
account. Two users share one key pair: the connector's, which may only read,
and this helper's, which owns the test database and changes it between syncs.
Each run works in a schema of its own and drops it afterwards.

Statements go through the connector's own statement runner, so the key-pair
sign-in the suite relies on is the one the connector uses.
"""

from __future__ import annotations

import os
from pathlib import Path

from app.sources.client.snowflake.snowflake import (  # type: ignore[import-not-found]
    parse_connection_settings,
)
from app.sources.external.snowflake.snowflake_ import (  # type: ignore[import-not-found]
    SnowflakeDataSource,
)
from app.sources.external.snowflake.statements import (  # type: ignore[import-not-found]
    SnowflakeStatementRunner,
)

# The connector's read-only user, and the helper's owner user.
CONNECTOR_ENV = ("SNOWFLAKE_TEST_ACCOUNT", "SNOWFLAKE_TEST_WAREHOUSE", "SNOWFLAKE_TEST_DATABASE",
                 "SNOWFLAKE_TEST_USER", "SNOWFLAKE_TEST_ROLE")
OWNER_ENV = ("SNOWFLAKE_TEST_ADMIN_USER", "SNOWFLAKE_TEST_ADMIN_ROLE")
# The key: its PEM, or a file holding it.
KEY_ENV = ("SNOWFLAKE_TEST_PRIVATE_KEY", "SNOWFLAKE_TEST_PRIVATE_KEY_FILE")


def private_key_pem() -> str:
    pem = os.getenv("SNOWFLAKE_TEST_PRIVATE_KEY", "")
    if pem:
        return pem
    path = os.getenv("SNOWFLAKE_TEST_PRIVATE_KEY_FILE", "")
    return Path(path).read_text(encoding="utf-8") if path else ""


def key_pair_auth(*, owner: bool = False) -> dict[str, str]:
    """The connector's auth settings, for the reader or for the owner."""
    return {
        "authType": "KEY_PAIR",
        "accountIdentifier": os.environ["SNOWFLAKE_TEST_ACCOUNT"],
        "warehouse": os.environ["SNOWFLAKE_TEST_WAREHOUSE"],
        "user": os.environ["SNOWFLAKE_TEST_ADMIN_USER" if owner else "SNOWFLAKE_TEST_USER"],
        "role": os.environ["SNOWFLAKE_TEST_ADMIN_ROLE" if owner else "SNOWFLAKE_TEST_ROLE"],
        "privateKey": private_key_pem(),
        "privateKeyPassphrase": os.getenv("SNOWFLAKE_TEST_PRIVATE_KEY_PASSPHRASE", ""),
    }


class SnowflakeSourceHelper:
    """The test database's objects under one schema, changed as the owner."""

    def __init__(self, database: str, schema: str, reader_role: str) -> None:
        self.database = database
        self.schema = schema
        self.reader_role = reader_role
        settings = parse_connection_settings({"auth": key_pair_auth(owner=True)})
        self._data_source = SnowflakeDataSource(settings.create_client())
        self._runner = SnowflakeStatementRunner(
            self._data_source, warehouse=settings.warehouse, role=settings.role,
        )

    async def close(self) -> None:
        await self._data_source.http.close()

    def qualified(self, name: str, schema: str | None = None) -> str:
        return f"{self.database}.{schema or self.schema}.{name}"

    async def run(self, *statements: str) -> list[list[object]]:
        rows: list[list[object]] = []
        for statement in statements:
            rows = (await self._runner.run(statement, timeout_s=120)).rows
        return rows

    async def whoami(self) -> list[object]:
        return (await self.run("SELECT CURRENT_USER(), CURRENT_ROLE()"))[0]

    async def create_schema(self, schema: str | None = None) -> None:
        name = f"{self.database}.{schema or self.schema}"
        await self.run(f"CREATE OR REPLACE SCHEMA {name}")
        await self.grant_reads(schema)

    async def grant_reads(self, schema: str | None = None) -> None:
        """What the reader may see: every table and view in the schema, and its stages."""
        name = f"{self.database}.{schema or self.schema}"
        await self.run(
            f"GRANT USAGE ON SCHEMA {name} TO ROLE {self.reader_role}",
            f"GRANT SELECT ON ALL TABLES IN SCHEMA {name} TO ROLE {self.reader_role}",
            f"GRANT SELECT ON ALL VIEWS IN SCHEMA {name} TO ROLE {self.reader_role}",
        )

    async def revoke_schema(self, schema: str) -> None:
        await self.run(f"REVOKE USAGE ON SCHEMA {self.database}.{schema} FROM ROLE {self.reader_role}")

    async def drop_schema(self, schema: str | None = None) -> None:
        await self.run(f"DROP SCHEMA IF EXISTS {self.database}.{schema or self.schema}")

    async def row_count(self, table: str) -> int:
        return int((await self.run(f"SELECT COUNT(*) FROM {self.qualified(table)}"))[0][0])
