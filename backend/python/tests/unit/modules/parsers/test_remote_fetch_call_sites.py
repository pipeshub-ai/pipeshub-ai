"""Every place that can fetch a remote image says whether it may, and a new one cannot hide.

Pushed content must never make indexing open a connection, so the choice is not
a default anywhere: ``allow_remote_fetch`` is a required keyword of the sink
(``ImageParser.urls_to_base64`` and ``_fetch_single_url``), and the LLM-provider
path has a required ``allow_remote_image_uri``. This file lists every call, and
fails on one it does not know, on a listed one that disappeared, and on a module
that gains a network import.

The inventory is a deny-list and cannot name every way to reach the network; the
runtime guard (the required keyword) is the real control, this test is a backstop.
"""

import ast
from functools import cache
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[4] / "app"

SINK_NAMES = {"urls_to_base64", "_fetch_single_url"}

# (file, enclosing function) -> "file" (a literal True: bytes a user or connector
# supplied, never a pushed blocks document) or "guarded" (the keyword is a
# variable derived from the push markers).
SINK_CALLS: dict[tuple[str, str], str] = {
    ("agents/actions/util/parse_file.py", "handle_html"): "file",
    ("agents/actions/util/parse_file.py", "_markdown_string_to_blocks"): "file",
    ("events/processor.py", "_process_blockgroup_images"): "guarded",
    ("events/processor.py", "_process_single_blockgroup_html"): "guarded",
    ("events/processor.py", "process_html_document"): "file",
    ("events/processor.py", "process_md_document"): "file",
    ("modules/parsers/blocks/blocks_parser.py", "_process_blockgroup_images"): "guarded",
    ("modules/parsers/blocks/blocks_parser.py", "_process_single_blockgroup_html"): "guarded",
    ("modules/parsers/html_parser/docling_html_parser.py", "parse"): "file",
    ("modules/parsers/html_parser/selectolax_html_parser.py", "parse"): "file",
    ("modules/parsers/markdown/docling_markdown_parser.py", "parse"): "file",
    ("modules/parsers/markdown/markdown_it_parser.py", "parse"): "file",
    # The sink handing the keyword to its own single-URL worker.
    ("modules/parsers/image_parser/image_parser.py", "fetch"): "guarded",
}

# Modules under these roots that import a network client, reviewed one by one.
NETWORK_NAMES = (
    "public_http", "url_fetcher", "httpx", "aiohttp", "curl_cffi", "requests", "urllib3", "pycurl", "httpcore",
    "socket", "grpc",
)
# Stdlib modules that are only a client in part: `from http import HTTPStatus` is fine.
NETWORK_DOTTED = ("urllib.request", "http.client")
NETWORK_ROOTS = ("modules/parsers", "modules/transformers", "events", "utils/image_utils.py")
NETWORK_IMPORTERS = {
    "modules/parsers/image_parser/image_parser.py": "the one image sink, behind allow_remote_fetch",
    "modules/transformers/blob_storage.py": "talks to the platform's own storage, not to an image URL",
}


@cache
def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8-sig"))


def _py_files() -> list[Path]:
    return sorted(p for p in APP.rglob("*.py") if "__pycache__" not in p.parts)


def _rel(p: Path) -> str:
    return p.relative_to(APP).as_posix()


def _enclosing_functions(tree: ast.AST) -> dict[int, str]:
    owner: dict[int, str] = {}

    def visit(node: ast.AST, current: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else current
            owner[id(child)] = name
            visit(child, name)

    visit(tree, "<module>")
    return owner


def _call_name(call: ast.Call) -> str | None:
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None


@cache
def _found_sink_calls() -> dict[tuple[str, str], list[ast.Call]]:
    found: dict[tuple[str, str], list[ast.Call]] = {}
    for path in _py_files():
        if not any(name in path.read_text(encoding="utf-8-sig") for name in SINK_NAMES):
            continue
        tree = _tree(path)
        owner = _enclosing_functions(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node) in SINK_NAMES:
                found.setdefault((_rel(path), owner.get(id(node), "<module>")), []).append(node)
    return found


def _keyword(call: ast.Call, name: str) -> ast.keyword | None:
    return next((k for k in call.keywords if k.arg == name), None)


def test_every_sink_call_is_classified_and_none_is_stale() -> None:
    found = _found_sink_calls()
    unclassified = sorted(set(found) - set(SINK_CALLS))
    stale = sorted(set(SINK_CALLS) - set(found))
    assert not unclassified, f"classify these remote-image call sites in SINK_CALLS: {unclassified}"
    assert not stale, f"SINK_CALLS lists a call that no longer exists: {stale}"


def test_the_inventory_covers_the_twelve_urls_to_base64_sites() -> None:
    count = sum(
        1
        for calls in _found_sink_calls().values()
        for call in calls
        if _call_name(call) == "urls_to_base64"
    )
    assert count == 12


@pytest.mark.parametrize("site", sorted(SINK_CALLS))
def test_each_site_passes_the_keyword_as_classified(site: tuple[str, str]) -> None:
    for call in _found_sink_calls()[site]:
        kw = _keyword(call, "allow_remote_fetch")
        assert kw is not None, f"{site} does not say whether a remote fetch is allowed"
        if SINK_CALLS[site] == "file":
            assert isinstance(kw.value, ast.Constant) and kw.value.value is True, site
        else:
            assert not isinstance(kw.value, ast.Constant), f"{site} is guarded, so its value must come from the markers"


def _function(path: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    tree = _tree(APP / path)
    return next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


def _kwonly_without_default(fn: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
    args = fn.args
    return any(a.arg == name and d is None for a, d in zip(args.kwonlyargs, args.kw_defaults))


@pytest.mark.parametrize("name", ["urls_to_base64", "_fetch_single_url"])
def test_the_sink_requires_the_keyword_with_no_default(name: str) -> None:
    fn = _function("modules/parsers/image_parser/image_parser.py", name)
    assert _kwonly_without_default(fn, "allow_remote_fetch")


def test_the_dead_curl_cffi_image_fetch_is_gone() -> None:
    src = (APP / "utils/image_utils.py").read_text()
    assert "_fetch_image_as_base64" not in src and "fetch_url" not in src and "curl_cffi" not in src


# --- the LLM-provider path: the provider fetches an http(s) image URI itself ---

LLM_URI_FUNCTIONS = ("_prepare_content", "_classify_blocks", "extract_metadata", "process_document")


@pytest.mark.parametrize("name", LLM_URI_FUNCTIONS)
def test_the_llm_uri_path_requires_the_keyword_with_no_default(name: str) -> None:
    assert _kwonly_without_default(_function("modules/transformers/document_extraction.py", name), "allow_remote_image_uri")


def test_the_llm_uri_keyword_decides_the_http_branch() -> None:
    fn = _function("modules/transformers/document_extraction.py", "_prepare_content")
    src = ast.unparse(fn)
    http_branch = src[src.index("startswith(('http://', 'https://'))"):]
    assert "allow_remote_image_uri" in http_branch.split("elif", 1)[0]


def test_every_llm_uri_call_states_its_value() -> None:
    """Callers of the LLM image path, anywhere in app, say what they allow.

    Only ``classify`` may pass a literal True: it receives blocks over the wire,
    after indexing asserted every pushed image inline.
    """
    guarded = {"_prepare_content", "_classify_blocks", "extract_metadata", "process_document"}
    literal_true_allowed = {("modules/transformers/document_extraction.py", "classify")}
    literal_true_seen: set[tuple[str, str]] = set()
    for path in _py_files():
        text = path.read_text(encoding="utf-8-sig")
        if not any(name in text for name in guarded):
            continue
        rel = _rel(path)
        tree = _tree(path)
        owner = _enclosing_functions(tree)
        for node in ast.walk(tree):
            name = _call_name(node) if isinstance(node, ast.Call) else None
            if name not in guarded:
                continue
            if name == "process_document" and rel != "modules/transformers/document_extraction.py":
                continue  # the OCR handler has a process_document of its own
            kw = _keyword(node, "allow_remote_image_uri")
            site = (rel, owner.get(id(node), "<module>"))
            assert kw is not None, f"{site} calls {name} without allow_remote_image_uri"
            if isinstance(kw.value, ast.Constant) and kw.value.value is True:
                literal_true_seen.add(site)
    assert literal_true_seen == literal_true_allowed


# --- no module of the indexing path gains a network client unreviewed ---


def _import_names(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.add(node.module or "")
            out.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Call) and _call_name(node) in ("import_module", "__import__"):
            out.update(a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str))
    return out


def _network_imports_in(tree: ast.AST) -> set[str]:
    return {
        name
        for name in _import_names(tree)
        if any(part in NETWORK_NAMES for part in name.split("."))
        or any(name == d or name.startswith(d + ".") for d in NETWORK_DOTTED)
    }


def _network_imports(path: Path) -> set[str]:
    return _network_imports_in(_tree(path))


def _stray_sink_references(tree: ast.AST) -> list[int]:
    """Lines that name a sink other than as the callee of a call: an alias, a getattr, a stored reference."""
    callees = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in SINK_NAMES and id(node) not in callees:
            lines.append(node.lineno)
        elif isinstance(node, ast.Name) and node.id in SINK_NAMES and id(node) not in callees:
            lines.append(node.lineno)
        elif isinstance(node, ast.Call) and _call_name(node) in ("getattr", "setattr"):
            lines.extend(a.lineno for a in node.args if isinstance(a, ast.Constant) and a.value in SINK_NAMES)
    return lines


def test_only_reviewed_modules_import_a_network_client() -> None:
    importers = set()
    for path in _py_files():
        rel = _rel(path)
        if any(rel == r or rel.startswith(r + "/") for r in NETWORK_ROOTS) and _network_imports(path):
            importers.add(rel)
    assert importers == set(NETWORK_IMPORTERS), (
        f"review the network import in {sorted(importers - set(NETWORK_IMPORTERS))}"
        f" / remove the stale entry {sorted(set(NETWORK_IMPORTERS) - importers)}"
    )


@pytest.mark.parametrize(
    "line",
    [
        "import httpx", "import aiohttp", "import requests", "import urllib3", "import curl_cffi",
        "import urllib.request", "from urllib.request import urlopen", "from urllib import request",
        "import http.client", "from http import client", "import socket", "import pycurl", "import httpcore",
        "import grpc", 'importlib.import_module("httpx")', '__import__("socket")',
        "from app.utils.public_http import PublicUrlFetcher",
    ],
)
def test_every_network_client_import_is_seen(line: str) -> None:
    assert _network_imports_in(ast.parse(line)), f"{line!r} would enter a parser unnoticed"


@pytest.mark.parametrize("line", ["from http import HTTPStatus", "from urllib.parse import urlparse", "import json"])
def test_harmless_stdlib_imports_are_not_flagged(line: str) -> None:
    assert not _network_imports_in(ast.parse(line))


@pytest.mark.parametrize(
    "src",
    [
        "f = ImageParser.urls_to_base64\nf(u, allow_remote_fetch=True)",
        'getattr(p, "urls_to_base64")(u, allow_remote_fetch=True)',
        "g = _fetch_single_url",
        "register(ImageParser._fetch_single_url)",
    ],
)
def test_a_sink_reference_that_is_not_a_call_is_seen(src: str) -> None:
    assert _stray_sink_references(ast.parse(src))


def test_a_direct_call_is_not_a_stray_reference() -> None:
    assert not _stray_sink_references(ast.parse("await ImageParser.urls_to_base64(u, allow_remote_fetch=True)"))


def test_no_module_references_a_sink_except_by_calling_it() -> None:
    stray = {}
    for path in _py_files():
        text = path.read_text(encoding="utf-8-sig")
        if any(name in text for name in SINK_NAMES) and (lines := _stray_sink_references(_tree(path))):
            stray[_rel(path)] = lines
    assert stray == {}, f"a sink is referenced without being called, so the inventory cannot see its calls: {stray}"
