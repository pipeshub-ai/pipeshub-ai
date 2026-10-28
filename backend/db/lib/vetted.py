"""Vetted SECURITY DEFINER functions: one YAML manifest entry per function, the SQL generated from it.

    python -m db.lib.vetted render <manifest.yaml> [--out DIR]   # writes DIR/<name>.sql (default: <manifest dir>)
    python -m db.lib.vetted check  <manifest.yaml> [--out DIR]   # exit 1 when a checked-in file differs from the manifest

A manifest is ``functions:`` and a list of entries::

    - name: schema.function            # the owner is pipeshub_vetted_<schema> (one vetted role per schema)
      class: read | write | jobs       # STABLE for read, VOLATILE otherwise
      scope: org | global | lineage | helper | person
      writer: pipeshub_node            # write class only: the one service role that may call it
      grants: [pipeshub_app]           # default: app group (read), the writer (write), the jobs role (jobs); none for a helper
      args: [{name: p_x, type: text, default: "'a'", nullable: false, length: [1, 64], range: [1, 100],
              enum: [a, b], pattern: '^[0-9a-f]{24}$', max_items: 10}]
      returns: "TABLE (id text, n int)" | "text[]" | boolean
      no_org: "false"                  # scalar functions: what a caller with no org gets (table functions return no rows)
      org_arg: p_org                   # lineage scope: the argument that must equal app.org_id
      ext: true                        # adds schema ext to the search_path
      custom_plan: true                # SET plan_cache_mode = force_custom_plan
      content_ok: true                 # may return a content column
      reads: [schema.table]            # tables it reads; each must be in the function's schema and opted into vetted_read
      writes: [schema.table]           # person, jobs and dedupe functions: tables it writes (own schema, opted into vetted_write)
      effect: read | self | admin | preorg   # person scope. read: pre-org, or with an org only the acting person's own email;
                                       #   self: write about the acting person; admin: one of the closed admin effects; preorg: no org yet
      person_arg: p_email              # person scope: the argument that names the person
      actor: "SELECT ..."              # person read/self: the acting person's email from v_org and v_user (app.user_id)
      admin_check: "SELECT ..."        # person admin: is v_user an admin of v_org
      member: "SELECT ..."             # person self/admin: is the person a live member of v_org (the membership proof)
      audit: schema.person_effects     # person admin: table receiving (org_id, actor_id, target_email, effect)
      audit_in_body: true              # person admin: the body writes the audit row itself (only when it changed something)
      admin_effect: unblock | role_change | admin_signout | clear_if_last
      fanout: [schema.table]           # person self/admin: org-scoped tables of other orgs it may write (needs vetted_write)
      requires_job: [kind]             # jobs import_*: refuses unless platform.jobs has a job with status 'running' of one of these kinds
      dedupe: schema.table             # write: may answer "already processed" for this external-issuer table (ON CONFLICT DO NOTHING)
      callers: [path]                  # dedupe: the only source paths (below backend/) that may call it
      body: |                          # PL/pgSQL statements; v_org is bound; static SQL, schema-qualified, ends with RETURN
      test: {...}                      # see db/README.md ("Vetted functions"): needle, args, own_result, other_result, ...

``render`` produces the template of the design checklist: validation of every argument (SQLSTATE 22023), the org from
``app.org_id`` only, no org means no rows, the owner hand-over, REVOKE from PUBLIC, the grants and the registry row.
A revision runs the generated file with ``run_vetted``; an extra migration location replaces a body the same way
(``extends: true`` in its own manifest: the signature, class and scope must equal the core entry's).
"""
import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
import yaml

OWNER = "pipeshub_owner"
APP_GROUP = "pipeshub_app"
JOBS = "pipeshub_jobs"
VETTED_PREFIX = "pipeshub_vetted_"
_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
_T = r"(?:text|int|integer|bigint|smallint|real|double precision|boolean|uuid|date|timestamptz|numeric|bytea)(?:\[\])?"
_TYPE = re.compile(rf"^{_T}$")
_RETURNS = re.compile(rf"^(?:void|{_T}|SETOF {_T}|TABLE \([a-z_][a-z0-9_]* {_T}(?:, [a-z_][a-z0-9_]* {_T})*\))$")
_LITERAL = re.compile(r"^(?:-?\d+(?:\.\d+)?|NULL|true|false|'[^'$\\;\x00]*')$")         # a number, NULL, a boolean or a plain quoted string
_RESERVED = frozenset("""all analyse analyze and any array as asc both case cast check collate column constraint create default desc distinct do else
    end except false for foreign from grant group having in initially intersect into leading limit not null offset on only or order placing primary
    references select some table then to trailing true union unique user using when where window with""".split())
# Functions an extra migration location may extend (replace the body of). The signature, class and scope stay frozen.
EXTENSION_POINTS = frozenset({"kernel.org_parent_ids"})
# RAISE in a vetted body: only a constant EXCEPTION with an SQLSTATE. A notice, a format argument or DETAIL could carry another org's value.
_RAISE = re.compile(r"\braise\b[^;]*", re.I)
_RAISE_OK = re.compile(r"^raise\s+exception\s+'[^'%]*'\s+using\s+errcode\s*=\s*'[0-9A-Z]{5}'\s*$", re.I)


def bad_raises(body: str) -> list[str]:
    return [m.strip() for m in _RAISE.findall(body) if not _RAISE_OK.match(m.strip())] + (["ASSERT"] if re.search(r"\bassert\b", body, re.I) else [])


def qi(name: str) -> str:
    """Quote an identifier the way quote_ident does: only when it must be (all names here already match the strict pattern)."""
    return name if _IDENT.match(name) and name not in _RESERVED else '"' + name.replace('"', '""') + '"'
_CLASSES = ("read", "write", "jobs")
_SCOPES = ("org", "global", "lineage", "helper", "person")
_EFFECTS = {"read": ("read",), "write": ("self", "admin", "preorg")}
ADMIN_EFFECTS = ("unblock", "role_change", "admin_signout", "clear_if_last")      # the closed list of person effects an admin may cause
_THRESHOLD = re.compile(r"(^|_)(limit|threshold|cap|max|min|count|size|top|offset|page)($|_)")
_TABLE_REF = re.compile(r"\b(?:from|join|into|update)\s+([a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*)\b(?!\s*\()", re.I)
_STMT = re.compile(r"^\s*select\b[^;$\x00]*$", re.I | re.S)
PERSON_LOCK_CLASS = 7350                                                            # kernel.lock_keys: person_email



class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class Arg:
    name: str
    type: str
    default: str | None = None
    nullable: bool = False
    length: tuple[int, int] | None = None
    range: tuple[int, int] | None = None
    enum: tuple[str, ...] | None = None
    pattern: str | None = None
    max_items: int | None = None


@dataclass(frozen=True)
class Fn:
    schema: str
    name: str
    cls: str
    scope: str
    args: tuple[Arg, ...]
    returns: str
    body: str
    writer: str | None = None
    grants: tuple[str, ...] = ()
    no_org: str | None = None
    org_arg: str | None = None
    ext: bool = False
    custom_plan: bool = False
    content_ok: bool = False
    extends: bool = False
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    effect: str | None = None
    person_arg: str | None = None
    actor: str | None = None
    admin_check: str | None = None
    member: str | None = None
    audit: str | None = None
    audit_in_body: bool = False
    admin_effect: str | None = None
    fanout: tuple[str, ...] = ()
    requires_job: tuple[str, ...] = ()
    dedupe: str | None = None
    callers: tuple[str, ...] = ()
    test: dict = field(default_factory=dict, compare=False)
    source: str = field(default="", compare=False)

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.name}"

    @property
    def owner(self) -> str:
        return VETTED_PREFIX + self.schema

    @property
    def signature(self) -> str:
        return f"{self.qualified}({', '.join(a.type for a in self.args)})"

    @property
    def registry_scope(self) -> str:
        return "global" if self.scope == "person" else self.scope

    @property
    def returns_set(self) -> bool:
        return self.returns.upper().startswith(("TABLE", "SETOF"))

    @property
    def shape(self) -> tuple:
        """What an ``extends`` entry must keep: everything except body, reads and test."""
        return (self.signature, [(a.name, a.type, a.default) for a in self.args], self.returns, self.cls, self.scope, self.writer, self.org_arg)


def _pair(v: object, what: str) -> tuple[int, int] | None:
    if v is None:
        return None
    if not (isinstance(v, list) and len(v) == 2 and all(isinstance(x, int) for x in v)):
        raise ManifestError(f"{what}: expected [min, max]")
    return (v[0], v[1])


def _lit(v: str) -> str:
    return "'" + v.replace("'", "''") + "'"


def _ident(v: object, what: str, where: str) -> str:
    if not (isinstance(v, str) and _IDENT.match(v) and v not in _RESERVED):
        raise ManifestError(f"{where}: {what} must be an identifier matching ^[a-z_][a-z0-9_]*$ (and not a reserved word), got {v!r}")
    return v


def _literal(v: object, what: str, where: str) -> str | None:
    if v is None:
        return None
    if not (isinstance(v, str) and _LITERAL.match(v)):
        raise ManifestError(f"{where}: {what} must be a number, NULL, true, false or a plain quoted string, got {v!r}")
    return v


def _flag(entry: dict, key: str, where: str) -> bool:
    v = entry.get(key, False)
    if not isinstance(v, bool):
        raise ManifestError(f"{where}: {key} must be true or false")
    return v


def _tables(v: object, what: str, where: str) -> tuple[str, ...]:
    if v is None:
        return ()
    if not isinstance(v, list):
        raise ManifestError(f"{where}: {what} must be a list of schema.table")
    return tuple(_one_table(t, what, where) for t in v)


def _one_table(v: object, what: str, where: str) -> str | None:
    if v is None:
        return None
    sch, _, tbl = str(v).partition(".")
    if not (isinstance(v, str) and _IDENT.match(sch) and _IDENT.match(tbl)):
        raise ManifestError(f"{where}: {what} must be schema.table, got {v!r}")
    return v


def _select(v: object, what: str, where: str) -> str | None:
    """A statement a template wraps in parentheses: one SELECT, no ';', '$', or comment."""
    if v is None:
        return None
    if not (isinstance(v, str) and _STMT.match(v) and "--" not in v and "/*" not in v):
        raise ManifestError(f"{where}: {what} must be one SELECT without ';', '$' or comments")
    return v.strip()


def _callers(v: object, where: str) -> tuple[str, ...]:
    if v is None:
        return ()
    if not (isinstance(v, list) and all(isinstance(c, str) and re.fullmatch(r"[A-Za-z0-9_./-]+", c) and ".." not in c for c in v)):
        raise ManifestError(f"{where}: callers must be a list of paths below backend/")
    return tuple(v)


def _check_identity(fn: Fn, where: str) -> None:
    """The rules of the person-level class, the jobs-only import/sweep functions and the dedupe exception."""
    plain = (fn.effect, fn.person_arg, fn.actor, fn.admin_check, fn.member, fn.audit, fn.admin_effect, fn.fanout)
    if fn.scope != "person":
        if any(plain):
            raise ManifestError(f"{where}: effect, person_arg, actor, admin_check, member, audit, admin_effect and fanout belong to scope person")
    else:
        if fn.cls == "jobs" or fn.effect not in _EFFECTS.get(fn.cls, ()):
            raise ManifestError(f"{where}: a person function of class {fn.cls} has effect {_EFFECTS.get(fn.cls)}, got {fn.effect!r}")
        arg = next((a for a in fn.args if a.name == fn.person_arg), None)
        if arg is None or arg.type != "text" or arg.nullable:
            raise ManifestError(f"{where}: person_arg must name a required text argument")
        if bad := [a.name for a in fn.args if _THRESHOLD.search(a.name)]:
            raise ManifestError(f"{where}: {bad} is a caller-chosen limit; server settings and constants only")
        need = {"read": ("actor",), "self": ("actor", "member"), "admin": ("admin_check", "member", "audit", "admin_effect"), "preorg": ()}[fn.effect]
        for k in need:
            if not getattr(fn, k):
                raise ManifestError(f"{where}: a person function with effect {fn.effect} needs {k}")
        for k in {"actor", "admin_check", "member", "audit", "admin_effect"} - set(need):
            if getattr(fn, k):
                raise ManifestError(f"{where}: {k} does not apply to effect {fn.effect}")
        if fn.admin_effect is not None and fn.admin_effect not in ADMIN_EFFECTS:
            raise ManifestError(f"{where}: admin_effect must be one of {ADMIN_EFFECTS}")
        if fn.audit_in_body and (fn.effect != "admin" or not re.search(
                rf"insert\s+into\s+{re.escape(fn.audit or '')}\s*\(\s*org_id,\s*actor_id,\s*target_email,\s*effect\s*\)", normalise(fn.body), re.I)):
            raise ManifestError(f"{where}: audit_in_body is for an admin effect whose body inserts (org_id, actor_id, target_email, effect) into the audit table")
        if fn.fanout and fn.effect not in ("self", "admin"):
            raise ManifestError(f"{where}: only a person write with a membership proof may write other orgs' rows (fanout)")
        if fn.cls == "write" and fn.returns != "void" and fn.no_org is None:
            raise ManifestError(f"{where}: a scalar person write needs no_org (what a refused caller gets)")
        if fn.cls == "read" and not fn.returns_set and fn.no_org is None:
            raise ManifestError(f"{where}: a scalar person read needs no_org")
        if fn.effect == "preorg" and fn.returns != "void" and fn.name != "consume_otp":
            raise ManifestError(f"{where}: a pre-org person function returns nothing (only consume_otp answers)")
    if fn.cls == "jobs":
        if re.search(r"\b(delete from|truncate)\b", normalise(fn.body), re.I) and not fn.name.startswith("sweep_"):
            raise ManifestError(f"{where}: a jobs function that deletes is a sweep_* function")
        if fn.scope != "global":
            raise ManifestError(f"{where}: a jobs function is global scope")
        if fn.name.startswith("import_") != bool(fn.requires_job):
            raise ManifestError(f"{where}: exactly an import_* function names requires_job (the job kinds that allow it to run)")
        if fn.name.startswith("sweep_") and fn.args:
            raise ManifestError(f"{where}: a sweep takes no arguments (it deletes by its own fixed expiry predicate)")
    elif fn.requires_job:
        raise ManifestError(f"{where}: requires_job belongs to a jobs function")
    if fn.dedupe:
        if fn.cls != "write" or fn.scope != "global" or fn.returns != "boolean" or not fn.callers or fn.dedupe.partition(".")[0] != fn.schema:
            raise ManifestError(f"{where}: a dedupe function is a global write returning boolean, on a table of its schema, with callers")
    elif fn.callers:
        raise ManifestError(f"{where}: callers belong to a dedupe function")
    if fn.dedupe and set(fn.writes) != {fn.dedupe}:
        raise ManifestError(f"{where}: a dedupe function writes its dedupe table and nothing else")
    if fn.writes and fn.cls == "read":
        raise ManifestError(f"{where}: a read function writes nothing")
    for t in (*fn.writes, *fn.fanout, *([fn.audit] if fn.audit else [])):
        if t.partition(".")[0] != fn.schema:
            raise ManifestError(f"{where}: {t} is outside the function's schema (one vetted owner per schema)")
    if fn.scope == "person" or fn.cls == "jobs" or fn.dedupe:      # the tables a body names are the tables the manifest declares
        listed = {*fn.reads, *fn.writes, *fn.fanout, *([fn.audit] if fn.audit else []), *([fn.dedupe] if fn.dedupe else [])}
        for text in (fn.body, fn.actor, fn.admin_check, fn.member):
            for t in _TABLE_REF.findall(normalise(text)):
                if t.lower() not in listed and not (fn.requires_job and t.lower() == "platform.jobs"):
                    raise ManifestError(f"{where}: the body names {t}, which reads/writes/fanout/audit does not list")


_TOKENS = re.compile(r"('(?:[^']|'')*')|--[^\n\r]*|/\*(?:[^*]|\*+[^*/])*\*+/")
_DOLLAR = re.compile(r"\$(?:[^\x01-\x7f]|[A-Za-z_$])")      # a dollar quote starts with $ and a tag (letters, any non-ASCII, or none); $1 and a regex's $' do not
_UNCLASSIFIABLE = re.compile(r"\b(merge|only|fetch|offset|call|execute)\b|\bformat\s*\(|\"|\bu&", re.I)
_ESTRING = re.compile(r"(^|[^A-Za-z0-9_'.$])e'", re.I)       # an E'..' string: a backslash escape the checks do not read


def normalise(text: str | None, *, keep_strings: bool = False) -> str:
    """The one reader for every text check (the SQL function ``kernel.src_text`` is the same function): comments removed, string literals
    blanked (kept when ``keep_strings``), whitespace collapsed, spaces around dots removed."""
    s = _TOKENS.sub(lambda m: " " + (m.group(1) or ""), text or "")
    if not keep_strings:
        s = re.sub(r"'(?:[^']|'')*'", "''", s)
    return re.sub(r"[ \t\n\r\f\v]+", " ", re.sub(r"[ \t\n\r\f\v]*\.[ \t\n\r\f\v]*", ".", s))      # ASCII whitespace only, as in SQL


def deny_unclassifiable(text: str | None, where: str) -> None:
    """Deny by default: the catalog checks read plain spellings (``INSERT INTO schema.table``, ``UPDATE schema.table``, ``LIMIT 5``).
    A construct they cannot classify is refused here instead of being taught to every check: dollar-quoted strings, E'' and U& strings,
    quoted identifiers, MERGE, ONLY, FETCH, OFFSET, CALL, EXECUTE and format(). Rewrite the statement in the plain form."""
    if _DOLLAR.search(text or ""):
        raise ManifestError(f"{where}: a dollar-quoted string hides text from the checks; use ordinary '...' literals")
    if _ESTRING.search(text or ""):
        raise ManifestError(f"{where}: an E'...' string hides text from the checks; use an ordinary '...' literal")
    if m := _UNCLASSIFIABLE.search(normalise(text)):
        raise ManifestError(f"{where}: {m.group(0)!r} is a construct the vetted-function checks cannot classify; write the plain form "
                            f"(INSERT INTO / UPDATE / DELETE FROM schema.table, unquoted lower-case names, LIMIT <integer>, no MERGE, ONLY, FETCH, "
                            f"OFFSET, CALL, EXECUTE, format())")


def parse(entry: dict, source: str = "") -> Fn:
    where = f"{source}: {entry.get('name', '?')}"
    unknown = set(entry) - {"name", "class", "scope", "writer", "grants", "args", "returns", "no_org", "org_arg", "ext", "custom_plan",
                            "content_ok", "extends", "reads", "body", "test", "writes", "effect", "person_arg", "actor", "admin_check", "member", "audit",
                            "admin_effect", "fanout", "requires_job", "dedupe", "callers", "audit_in_body"}
    if unknown:
        raise ManifestError(f"{where}: unknown keys {sorted(unknown)}")
    schema, _, name = str(entry.get("name", "")).partition(".")
    if not (_IDENT.match(schema) and _IDENT.match(name)):
        raise ManifestError(f"{where}: name must be schema.function in lower case")
    cls, scope = entry.get("class"), entry.get("scope", "org")
    if cls not in _CLASSES:
        raise ManifestError(f"{where}: class must be one of {_CLASSES}")
    if scope not in _SCOPES:
        raise ManifestError(f"{where}: scope must be one of {_SCOPES}")
    if (cls == "write") != bool(entry.get("writer")):
        raise ManifestError(f"{where}: exactly the write class names a writer")
    writer = _ident(entry["writer"], "writer", where) if entry.get("writer") else None
    grants = entry.get("grants", [])
    if not isinstance(grants, list):
        raise ManifestError(f"{where}: grants must be a list of role names")
    grants = tuple(_ident(g, "a grant", where) for g in grants)
    if scope == "helper" and (cls != "read" or grants):
        raise ManifestError(f"{where}: a helper is a read-class function with no grants")
    args = []
    for a in entry.get("args", []):
        bad = set(a) - {"name", "type", "default", "nullable", "length", "range", "enum", "pattern", "max_items"}
        if bad or not isinstance(a.get("type"), str) or not _TYPE.match(a["type"]):
            raise ManifestError(f"{where}: bad argument {a}")
        enum = a.get("enum")
        if enum is not None and not (isinstance(enum, list) and all(isinstance(v, str) and "\x00" not in v for v in enum)):
            raise ManifestError(f"{where}: enum must be a list of strings")
        pattern = a.get("pattern")
        if pattern is not None and not (isinstance(pattern, str) and "\x00" not in pattern):
            raise ManifestError(f"{where}: pattern must be a string")
        items = a.get("max_items")
        if items is not None and not isinstance(items, int):
            raise ManifestError(f"{where}: max_items must be a number")
        nullable = a.get("nullable", False)
        if not isinstance(nullable, bool):
            raise ManifestError(f"{where}: nullable must be true or false")
        args.append(Arg(_ident(a.get("name"), "an argument name", where), a["type"], _literal(a.get("default"), "a default", where), nullable,
                        _pair(a.get("length"), f"{where} length"), _pair(a.get("range"), f"{where} range"),
                        tuple(enum) if enum else None, pattern, items))
    returns = str(entry.get("returns", "")).strip()
    if not _RETURNS.match(returns):
        raise ManifestError(f"{where}: returns must be a type, SETOF type or TABLE (name type, ...) of the allowed types, got {returns!r}")
    body = entry.get("body", "")
    if not isinstance(body, str):
        raise ManifestError(f"{where}: body must be text")
    fn = Fn(schema, name, cls, scope, tuple(args), returns, body, writer, grants, _literal(entry.get("no_org"), "no_org", where),
            _ident(entry["org_arg"], "org_arg", where) if entry.get("org_arg") else None, _flag(entry, "ext", where),
            _flag(entry, "custom_plan", where), _flag(entry, "content_ok", where), _flag(entry, "extends", where),
            tuple(_ident(r.partition(".")[2], "a table name", where) and r for r in entry.get("reads", ())), test=dict(entry.get("test") or {}), source=source,
            writes=_tables(entry.get("writes"), "writes", where), effect=entry.get("effect"), person_arg=entry.get("person_arg"),
            actor=_select(entry.get("actor"), "actor", where), admin_check=_select(entry.get("admin_check"), "admin_check", where),
            member=_select(entry.get("member"), "member", where), audit=_one_table(entry.get("audit"), "audit", where),
            admin_effect=entry.get("admin_effect"), fanout=_tables(entry.get("fanout"), "fanout", where),
            requires_job=tuple(_ident(k, "a job kind", where) for k in entry.get("requires_job", ())),
            dedupe=_one_table(entry.get("dedupe"), "dedupe", where), callers=_callers(entry.get("callers"), where),
            audit_in_body=_flag(entry, "audit_in_body", where))
    if not fn.body.strip() or "$fn$" in fn.body:
        raise ManifestError(f"{where}: body is required and may not contain $fn$")
    if raised := bad_raises(fn.body):
        raise ManifestError(f"{where}: RAISE is allowed only as RAISE EXCEPTION 'constant text' USING ERRCODE = 'XXXXX' (no format arguments, "
                            f"no DETAIL or HINT, no notices): {raised[0]!r}")
    if scope in ("org", "lineage") and not fn.returns_set and fn.no_org is None:
        raise ManifestError(f"{where}: a scalar function with an org needs no_org (what a caller with no org gets)")
    if scope == "lineage" and (fn.org_arg not in [a.name for a in args]):
        raise ManifestError(f"{where}: lineage scope needs org_arg naming one of the arguments")
    if scope == "org" and fn.org_arg:
        raise ManifestError(f"{where}: an org-scoped function takes its org from app.org_id, never from an argument")
    for r in fn.reads:
        s, _, t = r.partition(".")
        if s != schema or not _IDENT.match(t):
            raise ManifestError(f"{where}: reads {r!r} is outside the function's schema (one vetted owner per schema)")
    for text in (fn.body, fn.actor, fn.admin_check, fn.member):
        deny_unclassifiable(text, where)
    _check_identity(fn, where)
    return fn


def load(path: str | Path) -> list[Fn]:
    path = Path(path)
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    fns = [parse(e, str(path)) for e in doc.get("functions", [])]
    names = [f.qualified for f in fns]
    if len(set(names)) != len(names):
        raise ManifestError(f"{path}: duplicate function names")
    return fns


def load_all(paths: list[str | Path]) -> dict[str, Fn]:
    """Core manifest first, extra locations after. A later entry replaces an earlier one only with ``extends: true`` and
    the same shape (signature, argument names and types, return type, class, scope, writer)."""
    out: dict[str, Fn] = {}
    for p in paths:
        for f in load(p):
            if f.signature in out or f.qualified in {g.qualified for g in out.values()}:
                old = next(g for g in out.values() if g.qualified == f.qualified)
                if f.qualified not in EXTENSION_POINTS and f.extends:
                    raise ManifestError(f"{p}: {f.qualified} is not an extension point (only {sorted(EXTENSION_POINTS)} may be extended)")
                if not f.extends:
                    raise ManifestError(f"{p}: {f.qualified} is already defined in {old.source}; replace its body with extends: true")
                if f.shape != old.shape:
                    raise ManifestError(f"{p}: {f.qualified} changes the frozen signature of {old.source}")
                out[old.signature] = f
            elif f.extends:
                raise ManifestError(f"{p}: {f.qualified} extends a function no manifest defines")
            else:
                out[f.signature] = f
    return out


def _validation(fn: Fn) -> list[str]:
    out = []

    def refuse(cond: str, name: str, why: str) -> None:
        out.append(f"  IF {cond} THEN RAISE EXCEPTION {_lit(f'{fn.name}: {name} {why}')} USING ERRCODE = '22023'; END IF;")

    for a in fn.args:
        if not a.nullable:         # NULL slips through every comparison below (and LIMIT NULL is no limit): refuse it first
            refuse(f"{a.name} IS NULL", a.name, "is required")
        guard = f"{a.name} IS NOT NULL AND " if a.nullable else ""
        if a.length:
            refuse(f"{guard}length({a.name}) NOT BETWEEN {a.length[0]} AND {a.length[1]}", a.name, f"length must be {a.length[0]}..{a.length[1]}")
        if a.range:
            refuse(f"{guard}{a.name} NOT BETWEEN {a.range[0]} AND {a.range[1]}", a.name, f"must be {a.range[0]}..{a.range[1]}")
        if a.enum:
            refuse(f"{guard}{a.name} NOT IN ({', '.join(_lit(v) for v in a.enum)})", a.name, "is not one of the allowed values")
        if a.pattern:
            refuse(f"{guard}{a.name} !~ {_lit(a.pattern)}", a.name, "does not match the expected format")
        if a.max_items is not None:
            refuse(f"{guard}cardinality({a.name}) > {a.max_items}", a.name, f"takes at most {a.max_items} items")
    return out


def _grants(fn: Fn) -> list[str]:
    if fn.grants:
        return list(fn.grants)
    return {"read": [] if fn.scope == "helper" else [APP_GROUP], "write": [fn.writer or ""], "jobs": [JOBS]}[fn.cls]


def _person(fn: Fn) -> tuple[str, list[str]]:
    """Declarations and the entry gates of a person-level function. Reads: before an org is chosen any exact email; with an org only
    the acting person's own. Writes: the acting principal (app.user_id) must be the person (self) or an admin of the org (admin) and
    the person a live member of the org; the per-email lock is taken before the proof that counts is read (an admin's first, unlocked
    probe only keeps a non-member's key from being waited on)."""
    p = fn.person_arg
    none = "RETURN;" if fn.returns_set or fn.returns == "void" else f"RETURN {fn.no_org};"
    declare = ["v_org text := NULLIF(current_setting('app.org_id', true), '')"]
    if fn.effect != "preorg":
        declare.append("v_user text := NULLIF(current_setting('app.user_id', true), '')")
    declare += {"read": ["v_actor text"], "self": ["v_actor text", "v_member boolean"], "admin": ["v_admin boolean", "v_member boolean"], "preorg": []}[fn.effect]
    def refuse(cond: str) -> str:
        return f"IF {cond} THEN {none} END IF;"

    lock = f"  PERFORM pg_advisory_xact_lock({PERSON_LOCK_CLASS}, hashtext({p}));"
    proof = [f"  v_member := ({fn.member});", "  " + refuse("NOT COALESCE(v_member, false)")]
    own = [f"  v_actor := ({fn.actor});", "  " + refuse(f"v_actor IS NULL OR {p} IS DISTINCT FROM v_actor")]
    if fn.effect == "read":
        lines = ["  IF v_org IS NOT NULL THEN", f"    v_actor := ({fn.actor});",
                 "    " + refuse(f"v_actor IS NULL OR {p} IS DISTINCT FROM v_actor"), "  END IF;"]
    elif fn.effect == "self":
        lines = ["  " + refuse("v_org IS NULL OR v_user IS NULL"), *own, lock, *proof]
    elif fn.effect == "admin":
        lines = ["  " + refuse("v_org IS NULL OR v_user IS NULL"), f"  v_admin := ({fn.admin_check});",
                 "  " + refuse("NOT COALESCE(v_admin, false)"), *proof, lock, *proof,      # unlocked probe first: waiting on a non-member's key would leak its activity
                 *([] if fn.audit_in_body else [f"  INSERT INTO {fn.audit} (org_id, actor_id, target_email, effect) VALUES (v_org, v_user, {p}, {_lit(fn.admin_effect)});"])]
    else:
        lines = ["  " + refuse("v_org IS NOT NULL"), lock]
    return "DECLARE " + "\n  ".join(d + ";" for d in declare) + "\n", lines


def render(fn: Fn, *, replace: bool = False) -> str:
    """The complete SQL for one function: create (or replace) as its schema's vetted role, grants, registry row.
    Run as the owner role; the vetted role is borrowed for this transaction only (SET, CREATE on the schema), then released."""
    path = f"pg_catalog, {qi(fn.schema)}" + (", ext" if fn.ext else "") + ", pg_temp"    # pg_temp LAST: otherwise a caller's temp table shadows ours
    decl = "" if fn.scope in ("global", "helper", "person") else "DECLARE v_org text := NULLIF(current_setting('app.org_id', true), '');\n"
    lines = _validation(fn)
    if fn.scope == "person":
        decl, more = _person(fn)
        lines += more
    elif fn.cls == "jobs" and fn.requires_job:
        kinds = ", ".join(_lit(k) for k in fn.requires_job)
        lines.append(f"  BEGIN\n    IF NOT EXISTS (SELECT 1 FROM platform.jobs j WHERE j.kind IN ({kinds}) AND j.status = 'running') THEN\n"
                     f"      RAISE EXCEPTION 'no running job allows this import' USING ERRCODE = '55000';\n    END IF;\n"
                     f"  EXCEPTION WHEN undefined_table OR undefined_column OR insufficient_privilege THEN\n"
                     f"    RAISE EXCEPTION 'no running job allows this import' USING ERRCODE = '55000';\n  END;")
    if fn.scope in ("org", "lineage"):
        none = "RETURN;" if fn.returns_set else f"RETURN {fn.no_org};"
        cond = "v_org IS NULL" + (f" OR {fn.org_arg} IS DISTINCT FROM v_org" if fn.scope == "lineage" else "")
        lines.append(f"  IF {cond} THEN {none} END IF;       -- no org (or someone else's): no rows, the same answer")
    body = "\n".join("  " + ln if ln.strip() else ln for ln in fn.body.strip("\n").splitlines())
    sig = f"{qi(fn.schema)}.{qi(fn.name)}({', '.join(a.type for a in fn.args)})"
    qual, schema, owner = f"{qi(fn.schema)}.{qi(fn.name)}", qi(fn.schema), qi(fn.owner)
    args = ", ".join(f"{qi(a.name)} {a.type}" + (f" DEFAULT {a.default}" if a.default is not None else "") for a in fn.args)
    volatility = "STABLE" if fn.cls == "read" else "VOLATILE"
    grants = [qi(g) for g in _grants(fn) if g]
    annotate = (f"SELECT kernel.annotate_function({_lit(sig)}, {_lit(fn.effect) if fn.effect else 'NULL'}, {_lit(fn.dedupe) if fn.dedupe else 'NULL'});\n"
                if fn.effect or fn.dedupe else "")
    return f"""-- generated from the manifest by python -m db.lib.vetted render: edit the manifest, not this file
GRANT {owner} TO {OWNER} WITH SET TRUE;
GRANT USAGE ON SCHEMA {schema}{", ext" if fn.ext else ""} TO {owner};           -- the body names objects of its own schema
GRANT CREATE ON SCHEMA {schema} TO {owner};
SET LOCAL ROLE {owner};
CREATE {"OR REPLACE " if replace else ""}FUNCTION {qual}({args})
RETURNS {fn.returns} LANGUAGE plpgsql {volatility} SECURITY DEFINER
SET search_path = {path}
SET standard_conforming_strings = on{chr(10) + "SET plan_cache_mode = force_custom_plan" if fn.custom_plan else ""}
AS $fn$
{decl}BEGIN
{chr(10).join(lines)}
{body}
END $fn$;
REVOKE ALL ON FUNCTION {sig} FROM PUBLIC;
{"".join(f"GRANT EXECUTE ON FUNCTION {sig} TO {g};{chr(10)}" for g in grants)}SET LOCAL ROLE {OWNER};
REVOKE CREATE ON SCHEMA {schema} FROM {owner};
REVOKE {owner} FROM {OWNER};
SELECT kernel.register_function({_lit(sig)}, {_lit(fn.cls)}, {_lit(fn.writer) if fn.writer else 'NULL'}, {_lit(fn.registry_scope)}, {str(fn.content_ok).lower()});
{annotate}"""


def render_all(fns: list[Fn], *, replace: bool = False) -> dict[str, str]:
    return {f"{f.name}.sql": render(f, replace=replace or f.extends) for f in fns}


def manifest_files() -> list[Path]:
    """``vetted/manifest.yaml`` of every module directory (core first) and of the extra migration locations."""
    from db.locations import version_locations
    return [m for d in version_locations() if (m := Path(d) / "vetted" / "manifest.yaml").is_file()]


HAND_WRITTEN = frozenset({"kernel.profile_epoch()", "kernel.postgres_store_epoch()"})       # registered by a revision's SQL file, not by a manifest


def problems(conn: psycopg.Connection, fns: dict[str, Fn] | None = None) -> list[tuple[str, str]]:
    """The checklist items the catalog cannot see: every registered function (but the prologue fence) is in a manifest and the
    manifest agrees with the registry; every table a function reads is in its schema and opted into vetted_read."""
    if not conn.execute("SELECT to_regclass('kernel.function_registry') IS NOT NULL").fetchone()[0]:
        return []
    try:
        fns = fns if fns is not None else load_all(manifest_files())
    except (ManifestError, OSError, yaml.YAMLError) as e:
        return [("71 vetted function manifest is invalid", str(e))]
    registry = {r[0]: r[1:] for r in conn.execute(
        "SELECT proc::text, class, scope, COALESCE(writer, ''), content_ok, COALESCE(person_effect, ''), COALESCE(dedupe_rel::text, '') "
        "FROM kernel.function_registry WHERE class <> 'prologue'")}
    for h in HAND_WRITTEN:
        registry.pop(h, None)
    out, seen = [], set()
    for f in fns.values():
        canon = conn.execute("SELECT to_regprocedure(%s)::text", (f.signature,)).fetchone()[0]
        if canon is None or canon not in registry:
            out.append(("71 manifest function is not deployed and registered", f.signature))
            continue
        seen.add(canon)
        dedupe = conn.execute("SELECT COALESCE(to_regclass(%s)::text, '')", (f.dedupe or "",)).fetchone()[0]
        if registry[canon] != (f.cls, f.registry_scope, f.writer or "", f.content_ok, f.effect or "", dedupe):
            out.append(("71 manifest and function registry disagree", canon))
        for t in f.reads:
            ok = conn.execute("SELECT EXISTS (SELECT 1 FROM kernel.rls_registry WHERE rel = to_regclass(%s) AND (vetted_read OR vetted_write))", (t,)).fetchone()[0]
            if not ok:
                out.append(("71 function reads a table that did not opt into vetted_read", f"{canon} -> {t}"))
        for t in (*f.writes, *f.fanout, *([f.audit] if f.audit else [])):
            ok = conn.execute("SELECT EXISTS (SELECT 1 FROM kernel.rls_registry WHERE rel = to_regclass(%s) AND vetted_write)", (t,)).fetchone()[0]
            if not ok:
                out.append(("71 function writes a table that did not opt into vetted_write", f"{canon} -> {t}"))
        # org-scoped tables of any org: only the person writes with the proof, and only through fanout
        if f.scope in ("person", "global") or f.cls == "jobs":
            out += [("71 writes names an org-scoped table, which only fanout may", f"{canon} -> {t}") for t in f.writes
                    if t not in f.fanout and conn.execute("SELECT EXISTS (SELECT 1 FROM kernel.rls_registry WHERE rel = to_regclass(%s) AND class IN ('org', 'lineage'))", (t,)).fetchone()[0]]
        # fan-out into rows of other orgs is for org-scoped tables only
        out += [("71 fanout names a table that is not an org-scoped table", f"{canon} -> {t}") for t in f.fanout
                if not conn.execute("SELECT EXISTS (SELECT 1 FROM kernel.rls_registry WHERE rel = to_regclass(%s) AND class IN ('org', 'lineage'))", (t,)).fetchone()[0]]
    out += [("71 vetted function is not in any manifest", sig) for sig in sorted(set(registry) - seen)]
    return out


TEST_ROOTS = ("python/tests/", "nodejs/apps/tests/", "db/tests/")      # the suites may call anything
_SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".sql", ".sh", ".yaml", ".yml", ".json"}


def callers_violations(fns: dict[str, Fn], root: Path) -> list[str]:
    """A dedupe function answers "already processed" and so is an existence oracle for the issuer's ids: only the source paths its
    manifest names (a file, or a directory below ``root``) may mention it. The bare function name is searched, so a name built by
    string interpolation is still seen; the test suites, the generated SQL and the manifests themselves are exempt."""
    out = []
    for f in fns.values():
        if not f.dedupe:
            continue
        call = re.compile(rf"\b{re.escape(f.name)}\b")
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in ("node_modules", ".git", "__pycache__", "dist", ".venv"))
            for name in sorted(files):
                path = Path(dirpath) / name
                rel = path.relative_to(root).as_posix()
                if (path.suffix not in _SOURCE_SUFFIXES or rel.startswith(TEST_ROOTS) or rel.startswith("db/versions/") or name == "manifest.yaml"
                        or any(rel == c or rel.startswith(c.rstrip("/") + "/") for c in f.callers)):
                    continue
                if call.search(path.read_text(encoding="utf-8", errors="ignore")):
                    out.append(f"{rel}: mentions {f.qualified}, which only {', '.join(f.callers)} may call")
    return out


def run_vetted(revision_file: str, *names: str) -> None:
    """From a revision: run ``vetted/<name>.sql`` (generated files beside the revision) in order."""
    from alembic import op
    d = Path(revision_file).parent / "vetted"
    for n in names:
        op.get_bind().connection.cursor().execute((d / f"{n}.sql").read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m db.lib.vetted")
    ap.add_argument("command", choices=("render", "check"))
    ap.add_argument("manifest")
    ap.add_argument("--out")
    ns = ap.parse_args(argv)
    manifest = Path(ns.manifest)
    out = Path(ns.out) if ns.out else manifest.parent
    stale = []
    for name, sql in render_all(load(manifest)).items():
        target = out / name
        if ns.command == "render":
            target.write_text(sql, encoding="utf-8")
        elif not target.is_file() or target.read_text(encoding="utf-8") != sql:
            stale.append(str(target))
    if stale:
        print("generated vetted SQL is out of date (run render):\n  " + "\n  ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
