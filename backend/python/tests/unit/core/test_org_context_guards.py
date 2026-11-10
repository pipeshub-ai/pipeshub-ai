"""CI ratchets for org context (direct_org_reads, detached_work).

Counts per file may only go down. To record an intentional change run
``ORG_CONTEXT_UPDATE_BASELINE=1 pytest tests/unit/core/test_org_context_guards.py``
and review the diff of ``org_context_baseline.json``.

detached_work: long-lived work (loops, consumers, lazily created clients) starts with
``spawn_detached``; a bare ``create_task``/``ensure_future`` copies the creating
request's context, so the task would serve every later event as that org.
direct_org_reads: new reads of the org straight off claims or payloads bypass the context.
"""

import ast
import json
import os
import re
import sys
from functools import lru_cache
from pathlib import Path

import pytest

from tests.support.app_tree import may_contain_name, sources

APP = Path(__file__).resolve().parents[3] / "app"
BASELINE = Path(__file__).with_name("org_context_baseline.json")
SPAWN = {"create_task", "ensure_future"}
G_OC1_EXEMPT = {"app/api/middlewares/auth.py", "app/core/envelope.py", "app/core/org_context.py"}
G_OC7_EXEMPT = {"app/core/org_context.py"}


# One honest pass over the tree parses about 15 MB of source (10 s on an idle core, under a loaded host a
# multiple of that), so the default 30 s is too tight for it. The two tests share the pass.
pytestmark = pytest.mark.timeout(120)

# Which files can be skipped without parsing. A skipped file must be one the full parse would find nothing in.
# * spawn: the call is an attribute whose name is an identifier, so the (NFKC-folded) text holds the name.
# * org key: a string constant equal to ``orgId``/``org_id`` is written as plain adjacent literals or, if it
#   uses an escape that can produce one of its letters (``\x``, octal, ``\u``, ``\U``, ``\N{}``), the file is
#   parsed. Without such an escape every literal piece is plain text, so the key's letters appear in the file in
#   order with only quotes, string prefixes, whitespace, parentheses, line continuations and comments between
#   them. ``_JUNK`` is exactly that, and the comment is atomic so the match stays linear.
_SPAWN_NAMES = tuple(SPAWN)
_ESCAPE_THAT_MAKES_A_LETTER = re.compile(r"\\[x0-7uUN]")
_JUNK = r"""(?:[\s()'"]|[rRuU](?=['"])|\\\r?\n|\\\r|(?>\#[^\n]*))*"""
# The key starts right after an opening quote and ends right before a closing one (either may be reached across
# the junk), which drops files that only have the identifier ``org_id``.
_SPLIT_KEY = re.compile(f"""['"]{_JUNK}o{_JUNK}r{_JUNK}g{_JUNK}(?:_{_JUNK}i|I){_JUNK}d{_JUNK}['"]""")


def _may_hold_findings(text: str) -> bool:
    return (
        may_contain_name(text, *_SPAWN_NAMES)
        or _ESCAPE_THAT_MAKES_A_LETTER.search(text) is not None
        or _SPLIT_KEY.search(text) is not None
    )


_ORG_KEYS = ("orgId", "org_id")


def _is_org_key(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value in _ORG_KEYS


def _count_tree(module: ast.AST) -> tuple[int, int]:
    """(spawns, direct org reads) in one parsed module."""
    spawn = reads = 0
    for node in ast.walk(module):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in SPAWN:
                spawn += 1
            if node.func.attr == "get" and node.args and _is_org_key(node.args[0]):
                reads += 1
        elif isinstance(node, ast.Subscript) and _is_org_key(node.slice):
            reads += 1
    return spawn, reads


def _scan_files(files: "tuple[tuple[Path, str], ...]", root: Path = APP, prefilter: bool = True) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {"detached_work": {}, "direct_org_reads": {}}
    for path, text in files:
        if prefilter and not _may_hold_findings(text):
            continue
        rel = "app/" + path.relative_to(root).as_posix()
        try:
            spawn, reads = _count_tree(ast.parse(text, filename=str(path)))
        except SyntaxError:  # a stray non-module file is not service code
            continue
        if spawn and rel not in G_OC7_EXEMPT:
            out["detached_work"][rel] = spawn
        if reads and rel not in G_OC1_EXEMPT:
            out["direct_org_reads"][rel] = reads
    return out


@lru_cache(maxsize=1)
def _scan() -> dict[str, dict[str, int]]:
    """One pass per process: the two tests below share it."""
    return _scan_files(sources(APP))


def _check(guard: str, hint: str) -> None:
    found = _scan()
    if os.environ.get("ORG_CONTEXT_UPDATE_BASELINE"):
        BASELINE.write_text(json.dumps(found, indent=1, sort_keys=True) + "\n")
    base = json.loads(BASELINE.read_text())
    grown = {f: (base[guard].get(f, 0), n) for f, n in found[guard].items() if n > base[guard].get(f, 0)}
    assert not grown, f"{guard}: new sites (baseline, now) {grown}. {hint}"


def test_no_new_bare_task_creation() -> None:
    _check("detached_work", "Use app.core.org_context.spawn_detached (long-lived) or spawn_in_request (request child work).")


def test_no_new_direct_org_id_reads() -> None:
    _check("direct_org_reads", "Read the org from app.core.org_context.current() instead of claims or payloads.")


# The scan has to keep catching what it is for. These plant violations in a temporary tree.
_PLANTED = {
    "svc/spawns.py": "import asyncio\n\n\ndef start(coro):\n    return asyncio.create_task(coro)\n",
    "svc/reads.py": "def org(claims, payload):\n    return claims.get('orgId'), payload['org_id']\n",
    # Generated-client shaped: big, no needle. Prefiltered out, and the full parse finds nothing in it either.
    "external/client.py": "class Client:\n    def call(self, x):\n        return x\n" * 2000,
}


def _plant(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    for rel, text in _PLANTED.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def test_the_scan_finds_planted_violations_and_skips_files_that_cannot_match(tmp_path: Path) -> None:
    root = _plant(tmp_path)
    found = _scan_files(sources(root), root)
    assert found == {
        "detached_work": {"app/svc/spawns.py": 1},
        "direct_org_reads": {"app/svc/reads.py": 2},
    }
    # The prefilter drops files without parsing them; it must not change the answer.
    assert found == _scan_files(sources(root), root, prefilter=False)


def test_a_planted_violation_fails_the_ratchet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _plant(tmp_path)
    monkeypatch.delenv("ORG_CONTEXT_UPDATE_BASELINE", raising=False)
    monkeypatch.setattr(sys.modules[__name__], "_scan", lambda: _scan_files(sources(root), root))
    with pytest.raises(AssertionError, match="new sites"):
        _check("direct_org_reads", "hint")
    with pytest.raises(AssertionError, match="new sites"):
        _check("detached_work", "hint")


def _old_scan_files(files: "tuple[tuple[Path, str], ...]", root: Path) -> dict[str, dict[str, int]]:
    """The scan as it was before any prefilter: parse every file, compare ``ast.unparse`` text."""
    out: dict[str, dict[str, int]] = {"detached_work": {}, "direct_org_reads": {}}
    for path, text in files:
        rel = "app/" + path.relative_to(root).as_posix()
        spawn = reads = 0
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in SPAWN:
                    spawn += 1
                if node.func.attr == "get" and node.args and ast.unparse(node.args[0]) in ("'orgId'", "'org_id'"):
                    reads += 1
            elif isinstance(node, ast.Subscript) and ast.unparse(node.slice) in ("'orgId'", "'org_id'"):
                reads += 1
        if spawn:
            out["detached_work"][rel] = spawn
        if reads:
            out["direct_org_reads"][rel] = reads
    return out


# Every way found so far to write the two things the scan looks for. Each runs alone in a file, so the
# prefilter is the only thing between the file and the answer.
_FORMS: dict[str, tuple[str, str]] = {
    "spawn_plain": ("detached_work", "import asyncio\nasyncio.create_task(c())\n"),
    "spawn_ensure_future": ("detached_work", "import asyncio\nasyncio.ensure_future(c())\n"),
    "spawn_space_after_dot": ("detached_work", "import asyncio\nasyncio. create_task(c())\n"),
    "spawn_continuation": ("detached_work", "import asyncio\nasyncio.\\\n    create_task(c())\n"),
    "spawn_parenthesised": ("detached_work", "import asyncio\n(asyncio.\n create_task)(c())\n"),
    "spawn_nested": ("detached_work", "import asyncio\nfoo(asyncio.\n ensure_future(c()))\n"),
    "spawn_comment_after_dot": ("detached_work", "import asyncio\n(asyncio.  # note\n create_task(c()))\n"),
    "spawn_fullwidth": ("detached_work", "import asyncio\nasyncio.\uff43reate_task(c())\n"),
    "org_get": ("direct_org_reads", "x.get('orgId')\n"),
    "org_get_snake": ("direct_org_reads", 'x.get("org_id")\n'),
    "org_subscript": ("direct_org_reads", "x['org_id']\n"),
    "org_implicit_concat": ("direct_org_reads", "x.get('org' 'Id')\n"),
    "org_implicit_concat_snake": ("direct_org_reads", 'x.get("org_" "id")\n'),
    "org_concat_across_lines": ("direct_org_reads", "x.get(('org'\n 'Id'))\n"),
    "org_concat_with_comment": ("direct_org_reads", "x.get(('org'  # first half\n 'Id'))\n"),
    "org_concat_prefixed": ("direct_org_reads", "x.get(r'org' u'Id')\n"),
    "org_concat_each_letter": ("direct_org_reads", "x.get('o' 'r' 'g' 'I' 'd')\n"),
    "org_concat_each_letter_snake": ("direct_org_reads", "x['o' 'r' 'g' '_' 'i' 'd']\n"),
    "org_line_continuation_in_string": ("direct_org_reads", "x.get('or\\\ngId')\n"),
    "org_escape_hex": ("direct_org_reads", "x.get('org\\x49d')\n"),
    "org_escape_octal": ("direct_org_reads", "x['org\\111d']\n"),
    "org_escape_unicode": ("direct_org_reads", "x.get('org\\u0049d')\n"),
    "org_escape_big_unicode": ("direct_org_reads", "x.get('org\\U00000049d')\n"),
    "org_escape_named": ("direct_org_reads", "x.get('org\\N{LATIN CAPITAL LETTER I}d')\n"),
    "org_escape_underscore_named": ("direct_org_reads", "x['org\\N{LOW LINE}id']\n"),
}


@pytest.mark.parametrize("form", sorted(_FORMS))
def test_a_planted_form_is_found_exactly_as_the_unfiltered_scan_finds_it(tmp_path: Path, form: str) -> None:
    guard, text = _FORMS[form]
    root = tmp_path / "app"
    root.mkdir()
    (root / "one.py").write_text(text, encoding="utf-8")
    (root / "empty.py").write_text("x = 1\n", encoding="utf-8")
    files = sources(root)
    found = _scan_files(files, root)
    assert found == _old_scan_files(files, root)
    assert found[guard] == {"app/one.py": 1}, f"the form {form!r} was not found at all"


def test_files_with_nothing_to_find_are_not_parsed(tmp_path: Path) -> None:
    for text in ("x = 1\n", "class C:\n    def orgs(self, ids):\n        return {'orgs': ids, 'org': 1, 'Id': 2}\n"):
        assert not _may_hold_findings(text)
    # The comment atom must not backtrack: a long comment with no key ends the search at once.
    assert not _may_hold_findings("o  # " + "x" * 200_000 + "\n")
