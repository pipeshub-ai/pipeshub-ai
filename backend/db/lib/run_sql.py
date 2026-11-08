"""Run the SQL file that sits next to a revision (same name, ``.sql``)."""
from pathlib import Path

from alembic import op


def run_sql(revision_file: str) -> None:
    sql = Path(revision_file).with_suffix(".sql").read_text(encoding="utf-8")
    # The raw DBAPI cursor, not op.execute(): SQLAlchemy would read ":name" in the file as a bind
    # parameter, and psycopg would read "%" as a placeholder whenever a parameter tuple is passed.
    # Without parameters psycopg runs a multi-statement script in one round trip.
    op.get_bind().connection.cursor().execute(sql)
