"""Every NOTIFY of app code goes through ``app.core.db.notify``, which marks the unit so that its COMMIT waits for the notification-queue
lock up to its statement timeout (``uow.COMMIT_LOCK_WAIT``). SQL that notifies by itself would hide that from the unit, so none may:
an app string that sends ``pg_notify`` or ``NOTIFY`` outside the helper fails here, and so does a migration or vetted function that does
(``db.lint`` refuses them in a revision too). A SQL function that has to notify goes in ``NOTIFYING_SQL_FUNCTIONS`` and its callers mark
their unit with ``notify``'s flag."""

import ast
import re

from tests.support.app_tree import APP, sources

BACKEND = APP.parents[1]
# pg_notify(...) anywhere, or a NOTIFY statement (NOTIFY channel [, payload]) where a statement can start; prose that starts with the word does not match.
NOTIFY_SQL = re.compile(r"\bpg_notify\s*\(|(?:^|;|\$\$)\s*notify\s+\"?\w+\"?\s*(?:,|;|$)", re.I | re.M)
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
            and NOTIFY_SQL.search(n.value)]


def test_the_detector_finds_a_notify_however_it_is_written() -> None:
    assert notifying_strings('X = "SELECT pg_notify($1, $2)"\n') == [1]
    assert notifying_strings('X = ("SELECT 1; " "NOTIFY ch")\n') == [1]
    assert notifying_strings('X = """DO $$ BEGIN\nNOTIFY ch;\nEND $$"""\n') == [1]
    assert notifying_strings('X = "SELECT 1; NOTIFY ch, \'x\'"\n') == [1]
    assert notifying_strings('X = "DO $$ BEGIN PERFORM pg_notify(\'ch\', \'\'); END $$"\n') == [1]
    assert notifying_strings('X = "SELECT \'--\' AS a; NOTIFY ch"\n') == [1]
    assert notifying_strings('def f():\n    """Woken by NOTIFY, with a poll."""\n') == []
    assert notifying_strings('X = "Notify all assignees."\nY = "notify skipped: no loop"\n') == []


def test_app_code_notifies_only_through_the_helper() -> None:
    found = [f"{p.relative_to(BACKEND / 'python')}:{line}" for p, text in sources()
             if str(p.relative_to(BACKEND / "python")) not in ALLOWED and ("notify" in text.lower())
             for line in notifying_strings(text)]
    assert found == [], f"send it with app.core.db.notify(tx, channel, payload) instead: {found}"


def test_no_sql_function_notifies_by_itself() -> None:
    found = sorted(str(p.relative_to(BACKEND)) for p in (BACKEND / "db").rglob("*")
                   if p.suffix in (".sql", ".yaml") and "tests" not in p.parts and NOTIFY_SQL.search(p.read_text(encoding="utf-8")))
    assert tuple(found) == NOTIFYING_SQL_FUNCTIONS, found
