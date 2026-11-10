"""Quoting for SQL sent to Snowflake.

Catalog names are stored case-exact, and Snowflake upper-cases an unquoted
identifier, so every name taken from the catalog is sent quoted.
"""


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def qualified_identifier(*parts: str) -> str:
    return ".".join(quote_identifier(part) for part in parts)


def quote_literal(value: str) -> str:
    # Snowflake reads backslash as an escape inside single-quoted strings.
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def stage_reference(database: str, schema: str, stage: str) -> str:
    return "@" + qualified_identifier(database, schema, stage)
