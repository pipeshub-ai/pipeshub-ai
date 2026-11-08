"""Block.externalRefs: the library calls each block makes, as `specifier:name`.

An import bound outside the repo used to be an empty marker, so "who calls
requests.get" was unanswerable. The binding now keeps where it came from, and
every re-resolved block gets its refs rewritten, an empty list included, so a
call that was removed cannot leave a stale ref behind.
"""
import pytest

from app.config.constants.arangodb import RecordRelations
from app.modules.code_graph.edge_builder import EdgeBuildResult, build_code_graph_edges

from .conftest import GROUP_ID, ORG_ID

pytestmark = pytest.mark.asyncio

CLIENT = b'''
import numpy as np
from requests import get
from sqlalchemy import func

from shop.local import helper


def fetch(url):
    return get(url)


def stats(xs):
    return np.array(xs), func.count()


def mine():
    return helper()
'''

LOCAL = b'''
def helper():
    return 1
'''

WEB = b'''
import * as fs from "node:fs";
import { useState } from "react";

export function Panel(): string {
  const [v] = useState(0);
  return fs.readFileSync(String(v), "utf8");
}
'''


async def _build(graph, **kwargs) -> EdgeBuildResult:
    return await build_code_graph_edges(graph_provider=graph, facts_source=graph, org_id=ORG_ID,
                                        record_group_id=GROUP_ID, **kwargs)


def _refs(graph) -> dict[str, list[str] | None]:
    return {b.get("qualifiedName"): b.get("externalRefs") for b in graph.blocks.values()
            if b.get("qualifiedName")}


async def _python(graph, index_file) -> None:
    await index_file("recL", "shop/local.py", LOCAL, "python")
    await index_file("recC", "shop/client.py", CLIENT, "python")


async def test_a_call_through_a_package_import_keeps_its_origin(graph, index_file) -> None:
    await _python(graph, index_file)
    await _build(graph)
    refs = _refs(graph)
    assert refs["function:fetch"] == ["requests:get"]


async def test_member_calls_on_external_modules_and_symbols(graph, index_file) -> None:
    await _python(graph, index_file)
    await _build(graph)
    assert _refs(graph)["function:stats"] == ["numpy:array", "sqlalchemy:func.count"]


async def test_a_method_named_like_a_builtin_on_an_imported_module_is_kept(graph, index_file) -> None:
    src = b"import requests\n\n\ndef load(url):\n    return requests.get(url), {}.get('k')\n"
    await index_file("recR", "shop/load.py", src, "python")
    await _build(graph)
    # `{}.get` is a dict's: still dropped at parse, so it names nothing.
    assert _refs(graph)["function:load"] == ["requests:get"]


async def test_a_parameter_shadowing_an_imported_module_is_not_import_bound(graph, index_file) -> None:
    """`function show(axios) { axios.get() }` calls the argument's `get`, never the library's."""
    await index_file("recS", "web/s.ts", b'import axios from "axios";\n'
                     b'export function show(axios: any): unknown {\n  return axios.get("/s");\n}\n'
                     b'export function direct(): unknown {\n  return axios.get("/d");\n}\n', "typescript")
    await _build(graph)
    refs = _refs(graph)
    assert not refs["function:show"] and refs["function:direct"] == ["axios:default.get"]


async def test_a_block_without_library_calls_has_no_refs(graph, index_file) -> None:
    # Absent and empty mean the same "no refs"; an empty list is written only to clear a stale one.
    await _python(graph, index_file)
    await _build(graph)
    refs = _refs(graph)
    assert not refs["function:mine"] and not refs["function:helper"]


async def test_typescript_named_and_namespace_imports(graph, index_file) -> None:
    await index_file("recW", "web/panel.ts", WEB, "typescript")
    await _build(graph)
    assert _refs(graph)["function:Panel"] == ["node:fs:readFileSync", "react:useState"]


async def test_a_default_import_is_named_default_whatever_the_local_binding(graph, index_file) -> None:
    await index_file("recA", "web/a.ts", b'import axios from "axios";\n'
                     b'export function f(): unknown {\n  return axios.get("/a");\n}\n'
                     b'export function call(): unknown {\n  return axios({ url: "/c" });\n}\n', "typescript")
    await index_file("recB", "web/b.ts", b'import ax from "axios";\n'
                     b'export function g(): unknown {\n  return ax.get("/b");\n}\n', "typescript")
    await _build(graph)
    refs = _refs(graph)
    assert refs["function:f"] == refs["function:g"] == ["axios:default.get"]
    assert refs["function:call"] == ["axios:default"]


async def test_a_call_to_an_imported_library_module_is_its_ref_not_a_repo_function(graph, index_file) -> None:
    """`import requests; requests()`: the module bound outside the repo is the callee, never the repo's own
    `requests` function, and the call is kept as the module's ref (the module binding is a falsy ExternalRef)."""
    await index_file("recR", "shop/requests_compat.py", b"def requests(url):\n    return url\n", "python")
    await index_file("recQ", "shop/caller.py", b"import requests\n\n\ndef call(url):\n    return requests(url)\n",
                     "python")
    await index_file("recD", "web/log.js", b'const debug = require("debug");\n'
                     b'function trace(m) {\n  return debug(m);\n}\nmodule.exports = { trace };\n', "javascript")
    await _build(graph)
    called = {graph.blocks[e["_to"].partition("/")[2]]["qualifiedName"]
              for e in graph.code_edges(RecordRelations.CALLS.value)}
    assert "function:requests" not in called
    refs = _refs(graph)
    assert refs["function:call"] == ["requests:"]
    assert refs["function:trace"] == ["debug:"]


async def test_a_namespace_import_names_members_alone(graph, index_file) -> None:
    await index_file("recY", "web/y.ts", b'import * as yaml from "js-yaml";\n'
                     b'export function load(s: string): unknown {\n  return yaml.load(s);\n}\n', "typescript")
    await _build(graph)
    assert _refs(graph)["function:load"] == ["js-yaml:load"]


async def test_a_removed_call_clears_its_ref_on_rebuild(graph, index_file) -> None:
    await _python(graph, index_file)
    await _build(graph)
    assert _refs(graph)["function:fetch"] == ["requests:get"]
    await index_file("recC", "shop/client.py", CLIENT.replace(b"return get(url)", b"return url"), "python")
    await _build(graph, touched_record_ids={"recC"})
    assert not _refs(graph)["function:fetch"]


async def test_an_untouched_file_keeps_its_refs_on_an_incremental_build(graph, index_file) -> None:
    await _python(graph, index_file)
    await _build(graph)
    await index_file("recL", "shop/local.py", LOCAL + b"\n\ndef extra():\n    return 2\n", "python")
    await _build(graph, touched_record_ids={"recL"})
    assert _refs(graph)["function:fetch"] == ["requests:get"]


def _ref_writes(graph) -> list[str]:
    """Blocks the last batch_update_nodes calls set externalRefs on (the fake records them)."""
    return sorted(key for key, _ in getattr(graph, "ref_updates", []))


async def test_an_unchanged_rebuild_writes_no_refs(graph, index_file) -> None:
    await _python(graph, index_file)
    first = await _build(graph)
    assert first.external_refs_written > 0
    graph.ref_updates = []
    second = await _build(graph)
    assert second.external_refs_written == 0 and _ref_writes(graph) == []


async def test_a_changed_rebuild_writes_exactly_the_changed_blocks(graph, index_file) -> None:
    await _python(graph, index_file)
    await _build(graph)
    graph.ref_updates = []
    # Neo4j merges a re-projection, so every stored list survives it (fetch's stale ["requests:get"] too).
    stored = {k: b["externalRefs"] for k, b in graph.blocks.items() if "externalRefs" in b}
    await index_file("recC", "shop/client.py", CLIENT.replace(b"return get(url)", b"return url"), "python")
    for key, refs in stored.items():
        if key in graph.blocks:
            graph.blocks[key]["externalRefs"] = refs
    result = await _build(graph, touched_record_ids={"recC"})
    fetch = next(k for k, b in graph.blocks.items() if b.get("qualifiedName") == "function:fetch")
    assert _ref_writes(graph) == [fetch] and result.external_refs_written == 1
    assert _refs(graph)["function:fetch"] == []


async def test_a_wiped_list_is_written_again(graph, index_file) -> None:
    # Arango replaces the whole document on re-projection: the list is simply gone.
    await _python(graph, index_file)
    await _build(graph)
    for block in graph.blocks.values():
        block.pop("externalRefs", None)
    graph.ref_updates = []
    await _build(graph)
    assert _refs(graph)["function:fetch"] == ["requests:get"]
    assert all(_refs(graph)[q] is None for q in ("function:mine", "function:helper"))  # absent == empty


async def test_a_dry_run_writes_no_refs(graph, index_file) -> None:
    await _python(graph, index_file)
    await _build(graph, dry_run=True)
    assert all(v is None for v in _refs(graph).values())


FROM_IMPORT = b'''
import json
from uuid import uuid4


class Sync:
    def stamp(self):
        return self.meeting.uuid.render(), self.meeting.json.render(), uuid4(), json.dumps(self.meeting)
'''


async def test_a_from_import_binds_its_names_not_the_module(graph, index_file) -> None:
    """`from uuid import uuid4` binds `uuid4` only, so `self.meeting.uuid.render()` is no call into the uuid
    library; and a receiver after a dot is an attribute, so `self.meeting.json.render()` is none into json,
    which `import json` still binds (`json.dumps`)."""
    await index_file("recM", "app/meeting.py", FROM_IMPORT, "python")
    await _build(graph)
    assert _refs(graph)["method:Sync.stamp"] == ["json:dumps", "uuid:uuid4"]


UNPLACED = b'''
from app.api.routes import toolsets


def keep(secret):
    return toolsets.keep_stored_secrets(secret)
'''


async def test_a_repo_package_the_resolver_cannot_place_is_no_library(graph, index_file) -> None:
    """`app.api.routes` is the repo's own (its top-level package `app` is in the repo) though no file of it was
    indexed: unresolved, never a library ref."""
    await index_file("recI", "app/__init__.py", b"", "python")
    await index_file("recK", "app/secrets.py", UNPLACED, "python")
    await _build(graph)
    assert not _refs(graph)["function:keep"]


STDLIB_JSON = b'''
import json


def dump(x):
    return json.dumps(x)
'''


async def test_a_package_nested_in_the_repos_own_is_no_top_level_name(graph, index_file) -> None:
    """`app/modules/parsers/json/` sits in package `app/modules/` (no `parsers/__init__.py` between them): a
    namespace gap, not a root, so `import json` stays the standard library's (pipeshub-py: 2,200 refs)."""
    await index_file("recA", "app/__init__.py", b"", "python")
    await index_file("recM", "app/modules/__init__.py", b"", "python")
    await index_file("recJ", "app/modules/parsers/json/__init__.py", b"", "python")
    await index_file("recD", "app/dump.py", STDLIB_JSON, "python")
    await _build(graph)
    assert _refs(graph)["function:dump"] == ["json:dumps"]


LIBRARY_CLIENT = b'''
import redis


def connect(url):
    return redis.from_url(url)
'''


async def test_a_folder_of_other_languages_is_no_python_top_level_name(graph, index_file) -> None:
    """`redis/healthcheck.ts` (a deploy folder) is no Python package: `import redis` stays the library's."""
    await index_file("recH", "redis/healthcheck.ts", b"export const ok = (): boolean => true;\n", "typescript")
    await index_file("recC", "app/cache.py", LIBRARY_CLIENT, "python")
    await _build(graph)
    assert _refs(graph)["function:connect"] == ["redis:from_url"]


GO_CHAIN = b'''package app

import "net/http"

func fetch(req *http.Request) (*http.Response, error) {
	return http.DefaultClient.Do(req)
}
'''


async def test_a_go_chain_whose_head_is_an_imported_package_is_a_path_into_it(graph, index_file) -> None:
    """The chain-head rule is every family's: `http.DefaultClient.Do()` is net/http's, not an attribute."""
    await index_file("recG", "app/fetch.go", GO_CHAIN, "go")
    await _build(graph)
    assert _refs(graph)["function:fetch"] == ["net/http:DefaultClient.Do"]


STATIC_FIELD = b'''package com.example.math;

import static java.math.BigInteger.ZERO;

import java.math.BigInteger;

class Bits {
    BigInteger high() {
        return BigInteger.ZERO.setBit(64);
    }
}
'''


async def test_a_chain_whose_head_is_an_imported_class_keeps_its_ref(graph, index_file) -> None:
    """`BigInteger.ZERO.setBit()` (guava's BigIntegerMathTest, beside `import static ...ZERO`): the chain starts at
    the imported class, so it is a path into the library (guava: 8 refs), not an attribute named `ZERO`."""
    await index_file("recB", "src/main/java/com/example/math/Bits.java", STATIC_FIELD, "java")
    await _build(graph)
    assert _refs(graph)["method:Bits.high"] == ["java.math.BigInteger:BigInteger.ZERO.setBit"]


CLASS_LITERAL = b'''package com.example.reflect;

import java.util.List;

class Probe {
    Object first() throws Exception {
        return List.class.getMethod("get", int.class);
    }
}
'''


async def test_a_class_literal_chain_names_no_member_of_the_class(graph, index_file) -> None:
    """`List.class.getMethod()` calls `java.lang.Class.getMethod`: not a path into `java.util.List` (guava: 56)."""
    await index_file("recR", "src/main/java/com/example/reflect/Probe.java", CLASS_LITERAL, "java")
    await _build(graph)
    assert not _refs(graph)["method:Probe.first"]


MODULE_PATHS = b'''
import datetime
import unittest


class Clock:
    def tick(self):
        return datetime.datetime.now(), unittest.mock.patch("x"), self.datetime.now()
'''


async def test_a_chain_whose_head_is_an_imported_module_is_a_path_into_it(graph, index_file) -> None:
    """`datetime.datetime.now()` is the class's `now`, not a module function `datetime:now`; `unittest.mock.patch`
    is the submodule's; `self.datetime.now()` is still an attribute, no ref."""
    await index_file("recT", "app/clock.py", MODULE_PATHS, "python")
    await _build(graph)
    assert _refs(graph)["method:Clock.tick"] == ["datetime:datetime.now", "unittest:mock.patch"]


RUST_MAIN = b"""use std::thread;
mod util;

fn main() {
    thread::spawn(|| {});
    serde_json::to_string(&1);
    std::fs::read("x");
    crate::missing::f();
    util::helper();
}
"""


async def test_rust_paths_name_the_crate_item_and_a_crate_path_names_no_library(graph, index_file) -> None:
    """A path through a `use` (`thread::spawn`), a path whose head is no repo name (`serde_json::to_string`),
    and a `crate::` path the resolver cannot place, which is the repo's own, never a library."""
    await index_file("rM", "app/src/main.rs", RUST_MAIN, "rust")
    await index_file("rU", "app/src/util.rs", b"pub fn helper() {}\n", "rust")
    await _build(graph)
    assert _refs(graph)["function:main"] == [
        "serde_json:serde_json.to_string", "std::fs:fs.read", "std::thread:thread.spawn"]


GO_TYPED = b"""package svc

import "net/http"

func get(c *http.Client) {
\thttp.Get("u")
\tc.Do(nil)
}
"""


async def test_a_go_receiver_typed_by_an_imported_package_names_its_type(graph, index_file) -> None:
    await index_file("gT", "svc/get.go", GO_TYPED, "go")
    await _build(graph)
    assert _refs(graph)["function:get"] == ["net/http:Client.Do", "net/http:Get"]


PHP_USE = b"""<?php
namespace App;
use Illuminate\\Http\\Request;
class C { function f() { return Request::create("/"); } }
"""


async def test_a_php_use_names_the_class_it_imports(graph, index_file) -> None:
    await index_file("pU", "src/C.php", PHP_USE, "php")
    await _build(graph)
    assert _refs(graph)["method:App.C.f"] == ["Illuminate\\Http\\Request:Request.create"]


async def test_a_commonjs_require_and_a_named_import_from_a_subpath(graph, index_file) -> None:
    await index_file("jU", "web/u.js", b'const _ = require("lodash");\nimport { readFile } from "fs/promises";\n'
                     b'function f(xs) { readFile("a"); return _.map(xs, String); }\nmodule.exports = { f };\n',
                     "javascript")
    await _build(graph)
    assert _refs(graph)["function:f"] == ["fs/promises:readFile", "lodash:map"]


async def test_a_builtin_named_function_of_an_imported_js_module_is_a_call(graph, index_file) -> None:
    """`cache.get(k)` through `import * as cache` is the module's `get`; it used to be dropped at parse as
    `Map.get`."""
    await index_file("jC", "web/cache.ts", b"export function get(k: string): string {\n  return k;\n}\n",
                     "typescript")
    await index_file("jU", "web/use.ts", b'import * as cache from "./cache";\n'
                     b'export function load(k: string): string {\n  return cache.get(k);\n}\n', "typescript")
    await _build(graph)
    calls = {(graph.blocks[e["_from"].partition("/")[2]]["qualifiedName"],
              graph.blocks[e["_to"].partition("/")[2]]["qualifiedName"])
             for e in graph.code_edges(RecordRelations.CALLS.value)}
    assert ("function:load", "function:get") in calls
    assert not _refs(graph)["function:load"]  # a repo module, so no external ref
