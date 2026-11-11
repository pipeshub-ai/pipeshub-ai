#!/usr/bin/env python3
"""Find log calls that put a person's email or name into the log line (docs/logging.md, "Personal data").

Python files are read with ``ast``; TypeScript files are parsed by ``log_pii_ts.cjs`` (the
``typescript`` package of backend/nodejs/apps), and both are classified here, so the two runtimes
share one rule set.

A log call is ``<x>.<level>(...)`` where a word of the receiver is log, logger or logging
(``logger``, ``self._log``, ``state_logger``, ``logging``; not ``catalog``), or ``log_event`` /
``log_failure``, whose keyword
fields are field names. In TypeScript the receiver may also be ``console``. Every argument is
read: the message, f-string and template parts, ``%`` / ``+`` operands, ``%s`` args, ``extra=``,
``**{...}`` fields, ``.get`` / ``getattr`` defaults and object-literal keys and values. An argument is reduced to a terminal name and its owner:
``user.email`` -> (email, user), ``payload["email"]`` -> (email, payload),
``m.get("displayName")`` -> (display_name, m). Any other call stops the walk, so
``len(emails)`` and ``bool(user.email)`` are not findings.

A name is personal data when it is an email word (``email``, ``user_email``, ``emailAddress``,
``upn``, ``mailbox``), a person's name (``full_name`` of a user, ``display_name`` of a member,
``first_name``, ``username``), or a whole user list (``users``, ``user_info``). Names of
connectors, files, tools, groups, repos and the like are not, and neither are ids, counts or
flags (``email_count``, ``no_email``). A bare ``name`` or ``user`` is not gated in Python; in
TypeScript ``{ user }`` and ``{ ...user }`` are.

``# log-pii: ok <reason>`` (``// log-pii: ok <reason>`` in TypeScript) on the line of a flagged
expression exempts that expression only. It must be a comment and must give a reason.

Prints ruff-shaped JSON for .github/scripts/new_lint_findings.py: one ``LOG-PII`` per flagged
expression and one ``LOG-PII-OK`` per annotated one, which only warns, each at its call's first
line. A file that does not parse has no findings.

Usage: log_pii.py <file>...   (TS_MODULE overrides where the typescript package is read from)
"""
import ast
import io
import json
import os
import re
import subprocess
import sys
import tokenize
from collections.abc import Iterator

from log_emoji import LOG_METHODS

CODE = "LOG-PII"
CODE_OK = "LOG-PII-OK"
HELPERS = frozenset({"log_event", "log_failure"})
LOGGER_WORDS = frozenset({"log", "logger", "logging"})  # self._log, state_logger, auditLogger; not catalogService
GATED = frozenset({"email", "person-name", "user-object"})
# `{ user }`, `{ ...user }` and `req.user` are the usual Node dump shapes; in Python bare `user` is mostly a DB login or an id.
GATED_TS = GATED | {"user-ref"}
# A field named `user` or `users` says nothing about its value (`{ user: userId }`); the value is checked on its own.
KEY_IGNORED = frozenset({"user-ref", "user-object"})
OK = re.compile(r"(#|//)\s*log-pii:\s*ok\s+\S")
HERE = os.path.dirname(os.path.abspath(__file__))
TS_SCANNER = os.path.join(HERE, "log_pii_ts.cjs")
DEFAULT_TS_MODULE = os.path.join(HERE, "..", "backend", "nodejs", "apps", "node_modules", "typescript")

EMAIL_WORDS = frozenset({"email", "emails", "mail", "upn", "email_address", "email_addresses", "mail_address",
                         "mailbox"})  # GmailMailboxSync(mailbox=user_email): a mailbox is a person's address
# Names that end in these carry an email word but are not an address.
NOT_VALUE = frozenset({"id", "ids", "count", "counts", "num", "len", "type", "types", "enabled", "status", "template",
                       "domain", "domains", "verified", "sent", "config", "settings", "service", "provider", "client",
                       "event", "events", "folder", "folders", "thread", "threads", "message", "messages", "subject",
                       "body", "header", "headers", "attachment", "attachments", "record", "records", "label",
                       "labels", "size", "query", "filter", "key", "keys", "field", "column", "regex", "pattern",
                       "valid", "is", "has", "missing", "map", "mapping", "index", "set", "list", "batch", "total",
                       "error", "errors", "exc", "exception", "content"})
COUNT_PREFIX = frozenset({"is", "has", "num", "n", "count", "total", "len", "no", "skipped", "without", "missing"})
PERSON_SUFFIXES = (("full", "name"), ("display", "name"), ("first", "name"), ("last", "name"), ("middle", "name"),
                   ("given", "name"), ("family", "name"), ("real", "name"), ("user", "name"), ("surname",),
                   ("username",), ("fullname",), ("displayname",), ("firstname",), ("lastname",), ("login",))
USER_HINTS = frozenset({"user", "users", "member", "members", "person", "people", "persons", "owner", "owners",
                        "creator", "author", "authors", "assignee", "assignees", "reporter", "sender", "recipient",
                        "recipients", "actor", "contact", "contacts", "employee", "employees", "principal", "lead",
                        "manager", "admin", "admins", "attendee", "attendees", "organizer", "participant", "invitee",
                        "requester", "commenter", "approver", "reviewer", "caller", "me",
                        "guest", "guests", "collaborator", "collaborators", "watcher", "watchers", "app_user", "sa",
                        "login"})
NONPERSON_HINTS = {
    "fp-connector": {"connector", "connectors", "app", "apps", "integration", "source", "sources", "datasource"},
    "fp-file": {"file", "files", "folder", "folders", "drive", "drives", "doc", "docs", "document", "documents",
                "page", "pages", "attachment", "record", "records", "item", "items", "blob", "bucket", "object",
                "path", "dir", "directory", "sheet", "workbook", "notebook", "note", "path_name", "mime", "kb"},
    "fp-tool": {"tool", "tools", "toolset", "toolsets", "action", "actions", "function", "func", "method", "agent",
                "agents", "model", "models", "llm", "provider", "mcp", "server", "skill", "skills", "prompt"},
    "fp-group": {"group", "groups", "team", "teams", "role", "roles", "org", "organization", "organisation", "tenant",
                 "workspace", "workspaces", "site", "sites", "channel", "channels", "space", "spaces", "project",
                 "projects", "repo", "repository", "repositories", "board", "boards", "queue", "list", "lists",
                 "calendar", "label", "labels", "category", "tag", "tags", "unit", "department",
                 "company", "domain", "license", "plan", "library", "collection", "database", "db", "table",
                 "schema", "field", "fields", "column", "index", "topic", "partition", "bot", "webhook",
                 "issue", "ticket", "event", "service", "host", "node", "edge", "entity", "class", "module",
                 "task", "job", "step", "stage", "phase", "process", "thread", "env", "key", "secret", "config",
                 "setting", "option", "param", "parameter", "attr", "attribute", "property", "type", "kind",
                 "status", "state", "region", "zone", "cluster", "instance", "container", "image", "version",
                 "branch", "commit", "release", "pipeline", "workflow", "template", "form", "object_type",
                 "sobject", "permission", "scope", "policy", "rule", "filter", "sort", "view", "report",
                 "dashboard", "widget", "component", "layout", "theme", "locale", "language", "country", "city"},
}
# Whole user objects or lists (the F5 dump class). Bare `user` / `member` / `profile` are left out: in this code
# they are mostly a DB login, a Slack/Teams user id or a browser profile (inventory class user-ref, not gated).
GROUP_OWNERS = frozenset({"group", "groups", "team", "teams", "channel", "channels", "org", "organization",
                          "organisation", "tenant", "domain", "list", "lists", "site", "space", "workspace"})
USER_OBJECT_NAMES = frozenset({"users", "members", "persons", "people", "app_users", "user_info", "user_data",
                               "user_details", "user_profile", "member_data", "user_doc", "user_record", "user_obj",
                               "enriched_user_info", "user_list", "member_list"})
USER_REF_NAMES = frozenset({"user", "member", "person", "app_user", "profile", "owner", "creator"})
STR_METHODS = frozenset({"lower", "upper", "strip", "lstrip", "rstrip", "title", "casefold", "replace", "format",
                         "encode", "decode", "split", "rsplit", "partition"})
PASS_FUNCS = frozenset({"str", "repr", "format", "list", "sorted", "set", "tuple"})


def words(name: str) -> list[str]:
    """snake/camel/kebab name -> lowercase words: userPrincipalName -> [user, principal, name]."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name.replace("-", "_"))
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    return [w for w in s.lower().split("_") if w]


def _hint(ws: list[str]) -> str | None:
    """'user' / an fp-* class / None from a list of words (owner or name prefix)."""
    joined = "_".join(ws)
    for cls, hints in NONPERSON_HINTS.items():
        if any(w in hints for w in ws) or joined in hints:
            return cls
    if any(w in USER_HINTS for w in ws) or joined in USER_HINTS:
        return "user"
    return None


def classify(terminal: str, owner: str | None) -> str | None:
    if terminal.startswith("__"):  # type(e).__name__, fn.__name__
        return None
    ws = words(terminal)
    if not ws:
        return None
    if ws[-1] in NOT_VALUE or any(w in COUNT_PREFIX for w in ws[:-1]) or ws[0] in COUNT_PREFIX:
        return None
    if ws == ["user", "principal", "name"]:
        return "email"
    # The email word must end the name: email_format, mail_server, mailbox_name are about an address, not one.
    if ws[-1] in EMAIL_WORDS or "_".join(ws[-2:]) in EMAIL_WORDS:
        if len(ws) == 1 and owner and GROUP_OWNERS & set(words(owner)):
            return "fp-group"  # group.email, team.mail: a list's address (permission.email is a person's)
        prefix = _hint(ws[:-1]) if len(ws) > 1 else None
        return "email" if prefix in (None, "user") else prefix  # group_email, channel_mail: not a person's
    for suf in PERSON_SUFFIXES:
        if tuple(ws[-len(suf):]) == suf:
            prefix = ws[: -len(suf)]
            if prefix and prefix != ["user"] and suf != ("user", "name"):
                h = _hint(prefix)
                return "person-name" if h == "user" else (h or "person-name")
            if suf in (("user", "name"), ("username",), ("login",), ("real", "name")) or prefix == ["user"]:
                h = _hint(words(owner)) if owner else None
                return h if h and h.startswith("fp-") else "person-name"
            h = _hint(words(owner)) if owner else None  # bare display_name / full_name
            if h == "user":
                return "person-name"
            if h:
                return h
            if suf in (("full", "name"), ("fullname",)) and not owner:
                return "ambiguous-name"  # bare full_name is mostly a GitHub owner/repo here
            return "person-name" if suf[0] in ("full", "first", "last", "middle", "given", "family", "surname",
                                               "fullname", "firstname", "lastname") else "ambiguous-name"
    if ws[-1] == "name":
        prefix = ws[:-1]
        h = _hint(prefix) if prefix else (_hint(words(owner)) if owner else None)
        if h == "user":
            return "person-name"
        if h:
            return h
        return "ambiguous-name" if not prefix else None  # foo_name with unknown foo: not reported
    if "_".join(ws) in USER_OBJECT_NAMES:
        return "user-object"
    if "_".join(ws) in USER_REF_NAMES:
        return "user-ref"
    return None


def _owner(node: ast.expr) -> str | None:
    t = _terminal(node)
    return t[0] if t else None


def _terminal(node: ast.expr) -> tuple[str, str | None] | None:
    if isinstance(node, ast.Name):
        return node.id, None
    if isinstance(node, ast.Attribute):
        return node.attr, _owner(node.value)
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
        return node.slice.value, _owner(node.value)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
            and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
        return node.args[0].value, _owner(node.func.value)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr"
            and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)):
        return node.args[1].value, _owner(node.args[0])
    return None


def leaves(node: ast.expr) -> Iterator[ast.expr]:
    """Yield the value expressions an argument puts into the log line."""
    if isinstance(node, (ast.Name, ast.Attribute, ast.Subscript)):
        yield node
    elif isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "get":
            yield node
            for a in node.args[1:]:  # the default: m.get("id", user.email)
                yield from leaves(a)
        elif isinstance(f, ast.Name) and f.id == "getattr":
            yield node
            for a in node.args[2:]:
                yield from leaves(a)
        elif isinstance(f, ast.Attribute) and f.attr == "join":
            for a in node.args:
                yield from leaves(a)
        elif isinstance(f, ast.Attribute) and f.attr in STR_METHODS:
            yield from leaves(f.value)
            if f.attr == "format":
                for a in [*node.args, *(k.value for k in node.keywords)]:
                    yield from leaves(a)
        elif isinstance(f, ast.Name) and f.id in PASS_FUNCS:
            for a in node.args:
                yield from leaves(a)
    elif isinstance(node, ast.JoinedStr):
        for v in node.values:
            yield from leaves(v)
    elif isinstance(node, ast.FormattedValue):
        yield from leaves(node.value)
    elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mod, ast.Add)):
        yield from leaves(node.left)
        yield from leaves(node.right)
    elif isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        for e in node.elts:
            yield from leaves(e)
    elif isinstance(node, ast.Dict):
        for k, v in zip(node.keys, node.values):
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                yield k  # a field named "email" is an email field, whatever the value is called
            if v is not None:
                yield from leaves(v)
    elif isinstance(node, ast.BoolOp):
        for v in node.values:
            yield from leaves(v)
    elif isinstance(node, ast.IfExp):
        yield from leaves(node.body)
        yield from leaves(node.orelse)
    elif isinstance(node, (ast.ListComp, ast.GeneratorExp, ast.SetComp)):
        yield from leaves(node.elt)
    elif isinstance(node, ast.Starred):
        yield from leaves(node.value)


def _match(leaf: ast.expr) -> tuple[str, str | None] | None:
    if isinstance(leaf, ast.Constant):
        return (leaf.value, None) if isinstance(leaf.value, str) else None
    return _terminal(leaf)


def is_log_call(node: ast.Call) -> str | None:
    """The level name of a log call, or None."""
    f = node.func
    if isinstance(f, ast.Name) and f.id in HELPERS:
        return f.id
    if isinstance(f, ast.Attribute) and f.attr in LOG_METHODS and LOGGER_WORDS & set(words(re.sub(r"\W+", "_", ast.unparse(f.value)))):
        return f.attr
    return None


def _args(node: ast.Call, level: str) -> Iterator[ast.expr]:
    if level in HELPERS:  # log_event(logger, "event", **fields): the fields are the payload
        for k in node.keywords:
            if k.arg is None:  # **{"email": ...}: its keys and values are fields
                yield k.value
            elif k.arg not in {"level", "exc_info", "error_class", "include_error_text"}:
                yield ast.Dict(keys=[ast.copy_location(ast.Constant(k.arg), k.value)], values=[k.value])
        return
    yield from node.args
    for k in node.keywords:
        if k.arg in (None, "extra", "msg"):
            yield k.value


def scan(source: str) -> list[dict]:
    """One dict per classified expression of each log call: line and col (the call's), leaf_line, level, cls, expr, owner, ok."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    ok_lines = annotated_lines(source)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        level = is_log_call(node)
        if not level:
            continue
        seen = set()
        for arg in _args(node, level):
            for leaf in leaves(arg):
                m = _match(leaf)
                if not m:
                    continue
                cls = classify(*m)
                if isinstance(leaf, ast.Constant) and cls in KEY_IGNORED:
                    continue
                expr = ast.unparse(leaf)
                first = getattr(leaf, "lineno", node.lineno)
                last = getattr(leaf, "end_lineno", None) or first
                if cls and (expr, first) not in seen:
                    seen.add((expr, first))
                    ok = any(n in ok_lines for n in range(first, last + 1))
                    out.append({"line": node.lineno, "col": node.col_offset, "leaf_line": first, "level": level, "cls": cls,
                                "expr": expr, "owner": m[1], "ok": ok})
    return out


def annotated_lines(source: str) -> set[int]:
    """Lines holding a ``# log-pii: ok <reason>`` comment token."""
    try:
        return {t.start[0] for t in tokenize.generate_tokens(io.StringIO(source).readline)
                if t.type == tokenize.COMMENT and OK.search(t.string)}
    except (tokenize.TokenError, SyntaxError):
        return set()


def findings(hits: list[dict], gated: frozenset[str]) -> list[tuple[int, str]]:
    """(call line, code) per gated expression: LOG-PII, or LOG-PII-OK when annotated.

    Counting expressions, not calls, makes a value added to a call that already leaks show as new; the call's
    column keeps two calls on one line apart."""
    keys = sorted({(h["line"], h["col"], h["expr"], h["leaf_line"], h["ok"]) for h in hits if h["cls"] in gated})
    return [(line, CODE_OK if ok else CODE) for line, _, _, _, ok in keys]


def ts_hits(paths: list[str]) -> dict[str, list[dict]]:
    """Classified expressions of TypeScript log calls, per path. Exits 2 when the parser cannot run."""
    module = os.path.abspath(os.environ.get("TS_MODULE") or DEFAULT_TS_MODULE)
    if not os.path.isdir(module):
        sys.exit(f"typescript is not installed at {module}; run npm ci in backend/nodejs/apps or set TS_MODULE")
    try:
        proc = subprocess.run(["node", TS_SCANNER, *paths], env={**os.environ, "TS_MODULE": module},
                              capture_output=True, text=True, check=False)
    except FileNotFoundError:
        sys.exit("node is not installed; it is needed to read TypeScript files")
    if proc.returncode:
        sys.stderr.write(proc.stderr)
        sys.exit(f"log_pii_ts.cjs failed (exit {proc.returncode})")
    out: dict[str, list[dict]] = {p: [] for p in paths}
    seen = set()
    for line in proc.stdout.splitlines():
        row = json.loads(line)
        cls = classify(row["terminal"], row["owner"])
        key = (row["path"], row["line"], row["col"], row["expr"], row["leaf_line"])
        if cls and not (row["is_key"] and cls in KEY_IGNORED) and key not in seen:
            seen.add(key)
            out[row["path"]].append({**row, "cls": cls})
    return out


def main(paths: list[str]) -> None:
    ts_paths = [p for p in paths if p.endswith(".ts")]
    by_path = ts_hits(ts_paths) if ts_paths else {}
    out = []
    for path in paths:
        if path in by_path:
            found = findings(by_path[path], GATED_TS)
        else:
            with open(path, encoding="utf-8", errors="replace") as f:
                found = findings(scan(f.read()), GATED)
        out += [{"filename": os.path.abspath(path), "code": code, "location": {"row": row}} for row, code in found]
    json.dump(out, sys.stdout)


if __name__ == "__main__":
    main(sys.argv[1:])
