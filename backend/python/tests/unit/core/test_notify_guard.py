"""Every NOTIFY of app code goes through ``app.core.db.notify``, which marks the unit so that its COMMIT waits for the notification-queue
lock up to its statement timeout (``uow.COMMIT_LOCK_WAIT``). SQL that notifies by itself would hide that from the unit, so none may:
an app string that sends ``pg_notify`` or ``NOTIFY`` outside the helper fails here, and so does a migration or vetted function that does
(``db.lint`` refuses them in a revision too). A SQL function that has to notify goes in ``NOTIFYING_SQL_FUNCTIONS`` and its callers mark
their unit with ``notify``'s flag."""

import ast
import re

from tests.support.app_tree import APP, sources

BACKEND = APP.parents[1]
# A backstop, broad rather than precise (the helper is the contract): pg_notify( anywhere, a string that is a NOTIFY statement, or the word
# NOTIFY followed by a name in a string that looks like SQL (it holds a statement keyword, ``;`` or ``$$``). Prose such as "Notify all
# assignees." is none of those; the helper is in the allow-list.
PG_NOTIFY = re.compile(r"\bpg_notify\s*\(", re.I)
NOTIFY_WORD = re.compile(r"\bnotify\s+\"?[a-z_]\w*", re.I)
NOTIFY_START = re.compile(r"notify\s+\"?[a-z_]\w*\"?\s*(?:,|;|$)", re.I)      # the whole statement: NOTIFY channel [, payload]
SQL_LOOKING = re.compile(r"\b(?:select|insert\s+into|delete\s+from|update\s+\S+\s+set|begin|perform|commit)\b|;|\$\$", re.I)


def sends_notify(sql: str) -> bool:
    return bool(PG_NOTIFY.search(sql) or NOTIFY_START.match(sql.strip()) or (NOTIFY_WORD.search(sql) and SQL_LOOKING.search(sql)))


# The helper itself, and the read-only SQL tool's list of functions it refuses (a pattern, never sent).
ALLOWED = {"app/core/db/uow.py", "app/agents/actions/util/read_only_sql.py"}
NOTIFYING_SQL_FUNCTIONS: tuple[str, ...] = ()


def notifying_strings(text: str) -> list[int]:
    """Lines of the string constants (docstrings aside) that send a NOTIFY."""
    tree = ast.parse(text)
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.body
            and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    return [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs
            and sends_notify(n.value)]


def test_the_detector_finds_a_notify_however_it_is_written() -> None:
    assert notifying_strings('X = "SELECT pg_notify($1, $2)"\n') == [1]
    assert notifying_strings('X = ("SELECT 1; " "NOTIFY ch")\n') == [1]
    assert notifying_strings('X = """DO $$ BEGIN\nNOTIFY ch;\nEND $$"""\n') == [1]
    assert notifying_strings('X = "SELECT 1; NOTIFY ch, \'x\'"\n') == [1]
    assert notifying_strings('X = "DO $$ BEGIN PERFORM pg_notify(\'ch\', \'\'); END $$"\n') == [1]
    assert notifying_strings('X = "SELECT \'--\' AS a; NOTIFY ch"\n') == [1]
    assert notifying_strings('X = "DO $$ BEGIN NOTIFY ch; END $$"\n') == [1]
    assert notifying_strings('X = "DO $$ BEGIN IF true THEN NOTIFY ch; END IF; END $$"\n') == [1]
    assert notifying_strings('X = "begin notify ch"\n') == [1]
    assert notifying_strings('X = "NOTIFY ch"\n') == [1]
    assert notifying_strings('X = "  notify ch, \'x\'"\n') == [1]
    assert notifying_strings('def f():\n    """Woken by NOTIFY, with a poll."""\n') == []
    assert notifying_strings('X = "Notify all assignees."\nY = "notify skipped: no loop"\n') == []
    assert notifying_strings('X = "notify skipped: no service associated with connector: %s"\n') == []


def test_app_code_notifies_only_through_the_helper() -> None:
    found = [f"{p.relative_to(BACKEND / 'python')}:{line}" for p, text in sources()
             if str(p.relative_to(BACKEND / "python")) not in ALLOWED and ("notify" in text.lower())
             for line in notifying_strings(text)]
    assert found == [], f"send it with app.core.db.notify(tx, channel, payload) instead: {found}"


def test_no_sql_function_notifies_by_itself() -> None:
    found = sorted(str(p.relative_to(BACKEND)) for p in (BACKEND / "db").rglob("*")
                   if p.suffix in (".sql", ".yaml") and "tests" not in p.parts and sends_notify(p.read_text(encoding="utf-8")))
    assert tuple(found) == NOTIFYING_SQL_FUNCTIONS, found
