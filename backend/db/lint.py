"""Migration SQL lint: ``python -m db.lint [file.sql ...]`` (default: every .sql under the module locations).

Rejects, per statement (comments and string/dollar-quoted bodies are not searched, except that
SECURITY DEFINER is looked for in function definitions):
  - DROP / RENAME / ALTER ... TYPE unless the paired .py revision sets ``min_code_epoch`` (a contract);
  - changes that break older code (a rollback runs it against the newer schema) unless the paired .py revision sets
    ``min_code_epoch`` or ``older_code_compatible = "<why older code still works>"``: ADD COLUMN ... NOT NULL without
    a default, SET NOT NULL, a new CHECK / FOREIGN KEY / UNIQUE / PRIMARY KEY (also NOT VALID) or unique index on an
    existing table, policy changes, ENABLE / FORCE ROW LEVEL SECURITY or kernel.apply_rls on an existing table, and
    CREATE OR REPLACE / DROP / ALTER FUNCTION on a function this file did not create (a vetted function is changed by
    dropping and re-creating it). A unique index built in a .py revision (ensure_index, ensure_partitioned_index) counts
    too, unless the paired .sql created the table. Not parsed: functions named only through a variable;
  - CREATE INDEX CONCURRENTLY written by hand (use lib.ddl.ensure_index);
  - CREATE INDEX on a table this file did not create (use ensure_index / ensure_partitioned_index);
  - ADD FOREIGN KEY / CHECK on an existing table without NOT VALID;
  - CREATE TABLE with an org_id column and no kernel.apply_rls for it in the same file;
  - GRANT ... TO PUBLIC and GRANT CREATE/ALL ON SCHEMA;
  - NOTIFY, LISTEN, pg_notify (channels are shared across orgs);
  - SECURITY DEFINER outside a ``vetted`` directory (generated function files are linted there too, and may
    GRANT CREATE ON SCHEMA to the schema's vetted role for their own transaction); BYPASSRLS; MATERIALIZED VIEW;
    ALTER DEFAULT PRIVILEGES.
Pure-Python revisions (no .sql) are not parsed by these rules. ``lint_revision`` reads every unreleased revision's .py for its index rules:
a blocking index build on a table the revision does not create, and a DROP INDEX (also built at run time) without ``min_code_epoch``.
Not covered: PG18-only syntax (guard with kernel.pg_major()).
"""
import ast
import json
import re
import sys
from pathlib import Path

from db.locations import version_locations

_DOLLAR = re.compile(r"\$([A-Za-z_]*)\$")


def statements(sql: str) -> list[str]:
    """Split on ';', skipping comments, quoted strings and dollar-quoted bodies (kept in the text)."""
    out, buf, i, n = [], [], 0, len(sql)
    while i < n:
        c = sql[i]
        if sql.startswith("--", i):
            i = sql.find("\n", i)
            i = n if i < 0 else i
        elif sql.startswith("/*", i):
            i = sql.find("*/", i)
            i = n if i < 0 else i + 2
        elif c == "'":
            j = i + 1
            while j < n and not (sql[j] == "'" and sql[j + 1:j + 2] != "'"):
                j += 2 if sql[j] == "'" else 1
            buf.append(sql[i:j + 1])
            i = j + 1
        elif c == "$" and (m := _DOLLAR.match(sql, i)):
            end = sql.find(m.group(0), m.end())
            end = n if end < 0 else end + len(m.group(0))
            buf.append(sql[i:end])
            i = end
        elif c == ";":
            out.append("".join(buf).strip())
            buf = []
            i += 1
        else:
            buf.append(c)
            i += 1
    out.append("".join(buf).strip())
    return [s for s in out if s]


def _strip_bodies(stmt: str) -> str:
    return re.sub(r"\$([A-Za-z_]*)\$.*?\$\1\$", "$$", stmt, flags=re.S)


def _strip_literals(stmt: str) -> str:
    return re.sub(r"'(?:[^']|'')*'", "''", stmt)


_N = r'(?:"[^"]+"|[\w$]+)(?:\.(?:"[^"]+"|[\w$]+))?'          # a possibly schema-qualified, possibly quoted name
_KEYWORD = r"(?=ADD |DROP |ALTER |ENABLE |DISABLE |FORCE |NO |SET |VALIDATE |OWNER |RENAME |DETACH |ATTACH )"
_BREAKING = (r"^(?:ADD (?:COLUMN )?(?:IF NOT EXISTS )?\S+ .*\b(?:CHECK|REFERENCES)\b"
             r"|ADD (?:CONSTRAINT (?:\"[^\"]+\"|\S+) )?(?:CHECK|FOREIGN KEY|UNIQUE|PRIMARY KEY|EXCLUDE)\b"
             r"|ALTER (?:COLUMN )?(?:\"[^\"]+\"|\S+) (?:SET NOT NULL|DROP DEFAULT)"
             r"|DROP CONSTRAINT\b|DETACH PARTITION\b|(?:ENABLE|FORCE) ROW LEVEL SECURITY)")
_EXEC_CALLS = ("execute", "exec_driver_sql", "text")


def _key(name: str) -> str:
    return name.replace('"', "").lower()


def _module_assign(py_text: str, name: str) -> ast.expr | None:
    for n in ast.parse(py_text).body:
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets):
            return n.value
        if isinstance(n, ast.AnnAssign) and n.value is not None and isinstance(n.target, ast.Name) and n.target.id == name:
            return n.value
    return None


def _marked(py_text: str) -> bool:
    """A real ``min_code_epoch = <int > 0>`` or ``older_code_compatible = "<reason of 4+ words>"`` at module level."""
    mark = _module_assign(py_text, "min_code_epoch")
    if isinstance(mark, ast.Constant) and type(mark.value) is int and mark.value > 0:
        return True
    why = _module_assign(py_text, "older_code_compatible")
    return isinstance(why, ast.Constant) and isinstance(why.value, str) and len(why.value.split()) >= 4


def _contract(py_text: str) -> bool:
    mark = _module_assign(py_text, "min_code_epoch")
    return isinstance(mark, ast.Constant) and type(mark.value) is int and mark.value > 0


def _const_str(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a, b = _const_str(node.left), _const_str(node.right)
        return None if a is None or b is None else a + b
    return None


def _arg(call: ast.Call, i: int, kw: str) -> ast.expr | None:
    return call.args[i] if len(call.args) > i else next((k.value for k in call.keywords if k.arg == kw), None)


def _op_sql(name: str, call: ast.Call) -> str | None:
    """Alembic schema API call as SQL the rules understand; None when a name is not a constant."""
    def s(i: int, kw: str) -> str | None:
        n = _arg(call, i, kw)
        return None if n is None else _const_str(n)

    def table(i: int, schema_kw: str = "schema") -> str | None:
        t, sch = s(i, "table_name"), _arg(call, 99, schema_kw)
        if t is None or (sch is not None and _const_str(sch) is None):
            return None
        return f"{_const_str(sch)}.{t}" if sch is not None else t

    kws = {k.arg: k.value for k in call.keywords}
    if name == "create_check_constraint":
        t, n = table(1), s(0, "constraint_name")
        return t and n and f"ALTER TABLE {t} ADD CONSTRAINT {n} CHECK (true)"
    if name in ("create_unique_constraint", "create_primary_key"):
        t, n = table(1), s(0, "constraint_name")
        return t and n and f"ALTER TABLE {t} ADD CONSTRAINT {n} {'UNIQUE' if name == 'create_unique_constraint' else 'PRIMARY KEY'} (c)"
    if name == "create_foreign_key":
        sch = kws.get("source_schema")
        t, n = s(1, "source_table"), s(0, "constraint_name")
        if t is None or n is None or (sch is not None and _const_str(sch) is None):
            return None
        return f"ALTER TABLE {_const_str(sch) + '.' if sch is not None else ''}{t} ADD CONSTRAINT {n} FOREIGN KEY (c) REFERENCES r (c)"
    if name == "drop_constraint":
        t, n = table(1), s(0, "constraint_name")
        return t and n and f"ALTER TABLE {t} DROP CONSTRAINT {n}"
    if name == "alter_column":
        t, c = table(0), s(1, "column_name")
        if t is None or c is None:
            return None
        out = []
        if isinstance(kws.get("nullable"), ast.Constant) and kws["nullable"].value is False:
            out.append(f"ALTER TABLE {t} ALTER COLUMN {c} SET NOT NULL")
        if "server_default" in kws and isinstance(kws["server_default"], ast.Constant) and kws["server_default"].value is None:
            out.append(f"ALTER TABLE {t} ALTER COLUMN {c} DROP DEFAULT")
        if "new_column_name" in kws:
            out.append(f"ALTER TABLE {t} RENAME COLUMN {c} TO n")
        if "type_" in kws:
            out.append(f"ALTER TABLE {t} ALTER COLUMN {c} TYPE t")
        return ";".join(out)
    if name == "add_column":
        t, col = table(0), _arg(call, 1, "column")
        if t is None or not isinstance(col, ast.Call):
            return None
        ck = {k.arg: k.value for k in col.keywords}
        notnull = isinstance(ck.get("nullable"), ast.Constant) and ck["nullable"].value is False and not {"server_default", "default"} & ck.keys()
        return f"ALTER TABLE {t} ADD COLUMN c int{' NOT NULL' if notnull else ''}"
    if name == "drop_column":
        t = table(0)
        return t and f"ALTER TABLE {t} DROP COLUMN c"
    if name == "drop_table":
        t = table(0)
        return t and f"DROP TABLE {t}"
    if name == "rename_table":
        t = table(0)
        return t and f"ALTER TABLE {t} RENAME TO n"
    if name == "create_index":
        t = table(1)
        return t and ("CREATE UNIQUE INDEX" if isinstance(kws.get("unique"), ast.Constant) and kws["unique"].value is True else "CREATE INDEX") + f" i ON {t} (c)"
    if name == "drop_index":
        return "DROP INDEX i"
    return ""


_OP_API = ("create_check_constraint", "create_unique_constraint", "create_primary_key", "create_foreign_key", "drop_constraint",
           "alter_column", "add_column", "drop_column", "drop_table", "rename_table", "create_index", "drop_index")


def _call_name(f: ast.expr) -> str:
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Call) and getattr(f.func, "id", "") == "getattr" and len(f.args) > 1 and isinstance(f.args[1], ast.Constant):
        return str(f.args[1].value)            # getattr(op, "execute")(...)
    return ""


def _static(node: ast.expr, env: dict[str, list[ast.expr]], depth: int = 0) -> str:
    """The text of a SQL argument with each part built at run time as ``\x00``: f-strings, ``+``, ``%`` and ``.format`` on a literal, and a name
    assigned in the same function or at module level (every value it is assigned, each as a statement of its own: which one a call sees is not followed)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "\x00" for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _static(node.left, env, depth) + _static(node.right, env, depth)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return re.sub(r"%[sdr]", "\x00", _static(node.left, env, depth))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        return re.sub(r"\{[^}]*\}", "\x00", _static(node.func.value, env, depth))
    if isinstance(node, ast.Name) and node.id in env and depth < 5:
        return ";\n".join(_static(v, env, depth + 1) for v in env[node.id])
    return "\x00"


def _upgrade_sql(py_text: str, runtime: list[str] | None = None) -> tuple[list[str], bool]:
    """SQL a revision runs: string constants passed to execute / exec_driver_sql / text / ensure_index and Alembic's schema API
    calls, in ``upgrade()`` and every module function it calls (``downgrade()`` is not read). True when some argument is not a
    constant (not parsed); ``runtime`` collects such an argument's text as far as it is written in the file (``_static``).
    shortcut: calls through objects other than module functions are not followed."""
    body = ast.parse(py_text).body
    funcs = {f.name: f for f in body if isinstance(f, ast.FunctionDef) and f.name != "downgrade"}
    module_env: dict[str, list[ast.expr]] = {}          # module constants (UPGRADE = """...""") that a function passes by name
    for n in body:
        for t in (n.targets if isinstance(n, ast.Assign) else [n.target] if isinstance(n, ast.AnnAssign) and n.value else []):
            if isinstance(t, ast.Name):
                module_env.setdefault(t.id, []).append(n.value)
    sql, dynamic, seen, todo = [], False, set(), ["upgrade"] if "upgrade" in funcs else []
    while todo:
        fn = funcs[todo.pop()]
        if fn.name in seen:
            continue
        seen.add(fn.name)
        nodes = list(ast.walk(fn))
        alias = {t.id for n in nodes if isinstance(n, ast.Assign) and _call_name(n.value) in _EXEC_CALLS
                 for t in n.targets if isinstance(t, ast.Name)}                   # run = conn.exec_driver_sql
        env = {k: list(v) for k, v in module_env.items()}
        for n in (n for n in nodes if isinstance(n, ast.Assign)):
            for t in (t for t in n.targets if isinstance(t, ast.Name)):
                env.setdefault(t.id, []).append(n.value)
        for call in (n for n in nodes if isinstance(n, ast.Call)):
            name = _call_name(call.func)
            if name in funcs and isinstance(call.func, ast.Name):
                todo.append(name)
            if name in alias:
                name = "execute"
            if name == "ensure_index":
                args = call.args[2:3] or [k.value for k in call.keywords if k.arg == "ddl"]
            elif name in _EXEC_CALLS:
                args = call.args[:1]
                args = [a.args[0] if isinstance(a, ast.Call) and _call_name(a.func) == "text" and a.args else a for a in args]
            elif name in _OP_API:
                r = _op_sql(name, call)
                if r is None:
                    dynamic = True
                elif r:
                    sql.append(r)
                continue
            else:
                continue
            for a in args:
                v = _const_str(a)
                if v is None:
                    dynamic = True
                    if runtime is not None:
                        runtime.append(_static(a, env))
                else:
                    sql.append(v)
    return sql, dynamic


def _earlier_tables(path: Path) -> set[str]:
    """Tables other revision files (this directory and every module location) create: a CREATE TABLE IF NOT EXISTS of one is a no-op."""
    found = set()
    for d in {path.parent, *map(Path, version_locations())}:
        for f in d.glob("*.sql"):
            if f.resolve() != path.resolve():
                found |= {_key(m.group(1)) for m in re.finditer(rf"CREATE\s+(?:UNLOGGED\s+)?TABLE\s+(?:IF NOT EXISTS\s+)?({_N})", f.read_text(encoding="utf-8"), re.I)}
    return found


_CTRL = re.compile(r"^\s*(?:DECLARE\b.*?\bBEGIN\b|BEGIN\b|END(?:\s+IF|\s+LOOP)?\b|(?:ELS)?IF\b.*?\bTHEN\b|ELSE\b|LOOP\b|FOR\b.*?\bLOOP\b|PERFORM\b|EXCEPTION\b|WHEN\b.*?\bTHEN\b)\s*", re.I | re.S)


def _do_statements(stmt: str) -> list[str]:
    """The statements of a DO block's body, control words (IF ... THEN, PERFORM, BEGIN) removed."""
    m = re.match(r"\s*DO\b.*?\$([A-Za-z_]*)\$(.*)\$\1\$\s*$", stmt, re.S | re.I)
    out = []
    for raw in statements(m.group(2)) if m else []:
        sub = raw
        while (c := _CTRL.match(sub)):
            sub = sub[c.end():]
        if sub.strip():
            out.append(sub.strip())
    return out


def lint_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    py = path.with_suffix(".py")
    py_text = py.read_text(encoding="utf-8") if py.is_file() else ""
    contract, compat_ok = _contract(py_text) if py_text else False, _marked(py_text) if py_text else False
    vetted = "vetted" in path.parts
    if vetted:   # a generated function file is changed by the revision(s) that run it
        revs = [t for r in path.parent.parent.glob("*.py") if f'"{path.stem}"' in (t := r.read_text(encoding="utf-8"))]
        compat_ok = any(_marked(t) for t in revs)
    py_sql, py_dynamic = _upgrade_sql(py_text) if py_text else ([], False)
    items = [(s, True) for s in statements(text)] + [(s, False) for q in py_sql for s in statements(q)]   # (statement, full lint)
    items += [(sub, False) for s, _ in items for sub in _do_statements(s)]                            # DDL inside DO bodies
    stmts = [s for s, _ in items]
    earlier = _earlier_tables(path)
    created = set()
    for s in stmts:
        m = re.match(rf"CREATE\s+(?:UNLOGGED\s+)?TABLE\s+(IF NOT EXISTS\s+)?({_N})", s, re.I)
        if m and not (m.group(1) and _key(m.group(2)) in earlier):
            created.add(_key(m.group(2)))
    made = {m.group(2) and _key(m.group(2)) for s in stmts if (m := re.match(rf"CREATE\s+(?:OR\s+REPLACE\s+)?(FUNCTION|PROCEDURE|VIEW|DOMAIN)\s+({_N})", re.sub(r"\s+", " ", s), re.I))}
    fns = {_key(m.group(1)) for s in stmts if (m := re.match(rf"CREATE\s+(?:FUNCTION|PROCEDURE|VIEW)\s+({_N})", re.sub(r"\s+", " ", s), re.I))}
    rls = " ".join(s for s in stmts if re.search(r"apply_rls", s, re.I)).lower()
    bad = []

    def flag(msg: str) -> None:
        bad.append(f"{path.name}: {msg}")

    def breaks(what: str) -> None:
        if not compat_ok:
            flag(f"{what} can break older code: set min_code_epoch or older_code_compatible = \"<reason>\"")

    if py_dynamic:
        breaks("SQL built at run time in upgrade() (not parsed)")
    for raw, full in items:
        s = re.sub(r"\s+", " ", _strip_literals(_strip_bodies(raw))).strip()
        u = s.upper()
        if full:
            if re.search(r"SECURITY\s+DEFINER", _strip_literals(raw), re.I) and not vetted:
                flag("SECURITY DEFINER outside a vetted directory")
            if re.search(r"\bBYPASSRLS\b", u):
                flag("BYPASSRLS")
            if u.startswith("CREATE MATERIALIZED VIEW"):
                flag("materialized view")
            if re.match(r"GRANT .* TO (.*, ?)?PUBLIC\b", u):
                flag("GRANT to PUBLIC (grants are explicit per role)")
            if re.match(r"GRANT (.*, )?(ALL|CREATE)\b.* ON SCHEMA", u) and not (vetted and re.match(r"GRANT CREATE ON SCHEMA \w+ TO PIPESHUB_VETTED_\w+$", u)):
                flag("GRANT CREATE on a schema")
            if re.search(r"\bPG_NOTIFY\s*\(|^NOTIFY\b|^LISTEN\b", u):
                flag("NOTIFY / LISTEN (a channel is shared by the whole database; payloads must not carry org data)")
            if u.startswith("ALTER DEFAULT PRIVILEGES"):
                flag("ALTER DEFAULT PRIVILEGES (grants are explicit per table)")
        dv = re.match(rf"DROP VIEW (?:IF EXISTS )?({_N})", s, re.I)
        if dv and re.search(rf"CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+{re.escape(dv.group(1))}\b", text, re.I):
            u = ""                                          # dropped and re-created by this file
        if not contract and (re.match(r"DROP (TABLE|COLUMN|SCHEMA|INDEX|VIEW|TYPE|SEQUENCE|DOMAIN|ROLE|OWNED|EXTENSION)\b", u) or u.startswith("TRUNCATE")
                             or re.match(r"ALTER ROLE .*\bNOLOGIN\b", u) or re.search(r"\bRENAME\b", u)
                             or re.search(r"\bDROP COLUMN\b", u) or re.search(r"\bALTER COLUMN \S+ (SET DATA )?TYPE\b", u)
                             or re.search(r"^ALTER (TABLE|VIEW|SEQUENCE|TYPE|DOMAIN) .* SET SCHEMA\b", u)):
            flag(f"destructive change without min_code_epoch: {u[:60]}")
        t = re.match(rf"ALTER TABLE (?:(?:IF EXISTS|ONLY) )*({_N}) (.*)", s, re.I)
        if t and _key(t.group(1)) not in created:
            for clause in re.split(r",\s*" + _KEYWORD, t.group(2).upper()):
                if re.match(r"ADD (COLUMN )?(IF NOT EXISTS )?\S+ ", clause) and not re.match(r"ADD (CONSTRAINT|CHECK|FOREIGN|UNIQUE|PRIMARY|EXCLUDE)\b", clause) \
                        and "NOT NULL" in clause and not re.search(r"\bDEFAULT (?!NULL\b)|\bGENERATED\b|\bSERIAL\b", clause):
                    breaks(f"NOT NULL column without a default on {t.group(1)}")
                if re.match(_BREAKING, clause):
                    breaks(f"{clause[:40]} on {t.group(1)}")
        m = re.match(rf"(?:CREATE|ALTER|DROP) POLICY (?:IF EXISTS )?{_N} ON ({_N})", s, re.I)
        if m and _key(m.group(1)) not in created:
            breaks(f"policy change on {m.group(1)}")
        m = re.search(r"\bapply_rls\s*\(\s*(?:p_rel\s*=>\s*)?'([^']+)'", _strip_bodies(raw), re.I)
        if m and _key(m.group(1)) not in created:
            breaks(f"kernel.apply_rls on existing table {m.group(1)}")
        m = re.match(rf"(CREATE OR REPLACE|DROP|ALTER) (FUNCTION|PROCEDURE|VIEW|DOMAIN) (?:IF EXISTS )?({_N})", s, re.I)
        if m and not (m.group(1).upper() != "DROP" and _key(m.group(3)) in (fns if m.group(1).upper() == "CREATE OR REPLACE" else made)) \
                and not (m.group(2).upper() == "VIEW" and m.group(1).upper() == "DROP"):   # DROP VIEW is destructive, above
            breaks(f"{m.group(1).upper()} {m.group(2).upper()} {m.group(3)}")
        m = re.match(rf"(?:CREATE (?:OR REPLACE )?(?:CONSTRAINT )?TRIGGER {_N} .*? ON|DROP TRIGGER (?:IF EXISTS )?{_N} ON) ({_N})", s, re.I)
        if m and _key(m.group(1)) not in created:
            breaks(f"trigger change on {m.group(1)}")
        m = re.match(rf"CREATE UNIQUE INDEX (?:IF NOT EXISTS )?{_N} ON (?:ONLY )?({_N})", s, re.I)
        if m and _key(m.group(1)) not in created:
            breaks(f"unique index on existing table {m.group(1)}")
        m = re.match(r"REVOKE\b(.*?)(?: ON (.*?))? FROM (.*?)(?: (?:CASCADE|RESTRICT|GRANTED BY .*))?$", s, re.I)
        if m:
            roles = [r.strip().strip('"') for r in re.sub(r"(?i)^GROUP ", "", m.group(3)).split(",")]
            objs = [re.sub(r"(?i)^(TABLE|FUNCTION|PROCEDURE|SCHEMA|SEQUENCE) ", "", o.strip()).split("(")[0] for o in re.split(r",(?![^(]*\))", m.group(2) or "")]
            housekeeping = (re.fullmatch(r"(?i)CREATE", m.group(1).strip()) and (m.group(2) or "").upper().startswith("SCHEMA ")
                            and all(re.fullmatch(r"(?i)pipeshub_vetted_\w+", r) for r in roles)) \
                or (m.group(2) is None and roles == ["pipeshub_owner"]
                       and all(re.fullmatch(r"(?i)pipeshub_vetted_\w+", r.strip()) for r in m.group(1).split(",")))
            if any(r.lower() != "public" for r in roles) and not housekeeping and not all(_key(o) in created | fns for o in objs):
                breaks(f"REVOKE from {', '.join(roles)}")
        m = re.match(rf"CREATE (?:OR REPLACE )?RULE {_N} AS ON \w+ TO ({_N})", s, re.I)
        if m and _key(m.group(1)) not in created:
            breaks(f"rule on {m.group(1)}")
        if u.startswith("DO ") and re.search(r"\bEXECUTE\b(?!\s+ON\b)", raw, re.I) and not any("dynamic EXECUTE" in b for b in bad):
            breaks("dynamic EXECUTE in a DO block (not parsed)")
        if not full:
            continue
        if re.match(r"CREATE (UNIQUE )?INDEX CONCURRENTLY", u):
            flag("hand-written CREATE INDEX CONCURRENTLY (use lib.ddl.ensure_index)")
        m = re.match(rf"CREATE (?:UNIQUE )?INDEX (?:IF NOT EXISTS )?{_N} ON (?:ONLY )?({_N})", s, re.I)
        if m and _key(m.group(1)) not in created:
            flag(f"CREATE INDEX on existing table {m.group(1)} (use lib.ddl.ensure_index)")
        m = re.match(rf"ALTER TABLE (?:(?:IF EXISTS|ONLY) )*({_N})", s, re.I)
        if m and _key(m.group(1)) not in created and re.search(r"ADD (CONSTRAINT (\"[^\"]+\"|\S+) )?(FOREIGN KEY|CHECK)\b", u) \
                and "NOT VALID" not in u:
            flag(f"constraint on existing table {m.group(1)} without NOT VALID")
        m = re.match(rf"CREATE (?:UNLOGGED )?TABLE (?:IF NOT EXISTS )?({_N})(.*)", s, re.I)
        if m and re.search(r"\borg_id\b", m.group(2), re.I) and not re.search(
                rf"\bapply_rls\s*\(\s*(?:p_rel\s*=>\s*)?'{re.escape(_key(m.group(1)))}'", rls):
            flag(f"table {m.group(1)} has org_id but no kernel.apply_rls call in this file")
    if py_text and not compat_ok:
        for call in (n for n in ast.walk(ast.parse(py_text)) if isinstance(n, ast.Call)):
            f = call.func
            if (f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")) == "ensure_partitioned_index" \
                    and any(k.arg == "unique" and isinstance(k.value, ast.Constant) and k.value.value is True for k in call.keywords) \
                    and len(call.args) > 1 and (tbl := _const_str(call.args[1])) and _key(tbl) not in created:
                flag(f"unique index on existing table {tbl} can break older code: set min_code_epoch or older_code_compatible = \"<reason>\"")
    return bad


def _released() -> dict[str, int]:
    return json.loads((Path(__file__).parent / "released_heads.json").read_text())["epochs"]


def _online_indexes(text: str) -> set[tuple[str, str]]:
    """(table, name) of each entry of ``ONLINE_INDEXES`` written as literals."""
    online = _module_assign(text, "ONLINE_INDEXES")
    entries = [{k.value: _const_str(v) for k, v in zip(d.keys, d.values) if isinstance(k, ast.Constant)}
               for d in (online.elts if isinstance(online, ast.List) else []) if isinstance(d, ast.Dict)]
    return {(_key(e["table"]), e["name"]) for e in entries if e.get("table") and e.get("name")}


def lint_revision(py: Path, released: dict[str, int]) -> list[str]:
    """Index rules for a revision's .py, with or without a .sql. A revision a release shipped (epoch up to ``released``) is not read:
    it cannot change, and a database that ran it keeps what it built.
      - ``ensure_partitioned_index(..., blocking=True)`` on a table this revision does not create, unless the index is one of the
        ``ONLINE_INDEXES`` of the revision or of a later one of its branch (db.migrate builds those before the transaction) or the module
        sets ``blocking_index_ok = "<why>"`` (an index on a column the revision adds cannot be built ahead): a blocking build keeps the table's writers waiting until the upgrade commits;
      - SQL in ``upgrade()`` that is, or may be, a DROP INDEX (also built at run time: an f-string, ``+``, ``%``, ``.format``) without
        ``min_code_epoch``: a drop inside the upgrade's transaction keeps every reader of the table waiting until the commit."""
    text = py.read_text(encoding="utf-8")
    rev = _module_assign(text, "revision")
    branch, _, epoch = rev.value.rpartition("_") if isinstance(rev, ast.Constant) and isinstance(rev.value, str) else ("", "", "")
    if not epoch.isdigit() or int(epoch) <= released.get(branch, 0):
        return []
    sql_file = py.with_suffix(".sql")
    runtime: list[str] = []
    const_sql, _ = _upgrade_sql(text, runtime)
    earlier = _earlier_tables(sql_file)
    stmts = [s for q in [sql_file.read_text(encoding="utf-8") if sql_file.is_file() else "", *const_sql] for s in statements(q)]
    created = {_key(m.group(2)) for s in stmts + [sub for s in stmts for sub in _do_statements(s)]          # as lint_file: statements, not comments or strings
               if (m := re.match(rf"CREATE\s+(?:UNLOGGED\s+)?TABLE\s+(IF NOT EXISTS\s+)?({_N})", s, re.I))
               and not (m.group(1) and _key(m.group(2)) in earlier)}
    # a later revision of the branch is pending whenever this one is, so the online step builds its ONLINE_INDEXES ahead of this one's call too
    later = [f.read_text(encoding="utf-8") for f in py.parent.glob("[0-9]*.py") if f.name[:4].isdigit() and int(f.name[:4]) > int(epoch)]
    online = {ix for t in (text, *later) for ix in _online_indexes(t)}
    why = _module_assign(text, "blocking_index_ok")
    waived = isinstance(why, ast.Constant) and isinstance(why.value, str) and len(why.value.split()) >= 4
    tree = ast.parse(text)
    nodes = [n for f in tree.body if not (isinstance(f, ast.FunctionDef) and f.name == "downgrade") for n in ast.walk(f)]
    from_online = {id(c) for f in nodes if isinstance(f, ast.For) and isinstance(f.target, ast.Name) and isinstance(f.iter, ast.Name)
                   and f.iter.id == "ONLINE_INDEXES" for c in ast.walk(f) if isinstance(c, ast.Call)
                   and any(k.arg is None and isinstance(k.value, ast.Name) and k.value.id == f.target.id for k in c.keywords)}
    bad = []
    for call in (n for n in nodes if isinstance(n, ast.Call) and _call_name(n.func) == "ensure_partitioned_index"):
        if not any(k.arg == "blocking" and isinstance(k.value, ast.Constant) and k.value.value is True for k in call.keywords):
            continue
        if id(call) in from_online:
            continue
        table, name = (_const_str(n) if (n := _arg(call, i, kw)) is not None else None for i, kw in ((1, "table"), (2, "name")))
        if (table and _key(table) in created) or (table and name and (_key(table), name) in online) or waived:
            continue
        bad.append(f"{py.name}: index {name or '<built at run time>'} on {table or '<built at run time>'} is built inside the upgrade's transaction "
                   "(blocking=True): list it in ONLINE_INDEXES, or set blocking_index_ok = \"<why it cannot be built ahead>\"")
    if not _contract(text):                     # a .sql's revision has its constant SQL linted by lint_file
        bad += [f"{py.name}: upgrade() may DROP INDEX without min_code_epoch: {s.replace(chr(0), '{...}')[:60]}"
                for s in runtime + ([] if sql_file.is_file() else const_sql)
                # a statement whose verb is built at run time may be a DROP INDEX too
                if re.search(r"\bDROP\s+(?:INDEX\b|\x00)", s, re.I) or any(q.startswith("\x00") for q in statements(s))]
    return bad


def retiring_violations() -> list[str]:
    """Indexes a revision lists in ``ONLINE_DROPS`` are dropped by db.migrate, so no SQL of the revision shows the DROP. The revision says which
    it is: ``min_code_epoch`` when older code plans with an index it drops, ``older_code_compatible = "<why>"`` when nothing does (a dropped
    index changes no answer, so a rollback keeps working)."""
    return [f"{f.name}: ONLINE_DROPS drops indexes: set min_code_epoch (older code plans with them) or older_code_compatible = \"<why nothing reads them>\""
            for d in version_locations() for f in sorted(Path(d).glob("*.py"))
            if _module_assign(t := f.read_text(encoding="utf-8"), "ONLINE_DROPS") is not None and not _marked(t)]


def main(argv: list[str]) -> int:
    files = [Path(a) for a in argv] or sorted(f for d in version_locations() for g in ("*.sql", "vetted/*.sql") for f in Path(d).glob(g))
    sql_files = [f for f in files if f.suffix != ".py"]
    sql_files += [s for f in files if f.suffix == ".py" and (s := f.with_suffix(".sql")).is_file() and s not in sql_files]   # its upgrade()'s constant SQL
    bad = [msg for f in sql_files for msg in lint_file(f)]
    revisions = [f for f in files if f.suffix == ".py"] if argv else sorted(f for d in version_locations() for f in Path(d).glob("[0-9]*.py"))
    released = _released()
    bad += [msg for f in revisions for msg in lint_revision(f, released)]
    if not argv:
        bad += retiring_violations()
        from db.lib import vetted
        bad += vetted.callers_violations(vetted.load_all(vetted.manifest_files()), Path(__file__).resolve().parents[1])
    print("\n".join(bad) or "migration lint: OK")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
