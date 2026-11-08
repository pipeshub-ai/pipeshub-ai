"""tsconfig/jsconfig `paths` and `baseUrl` place non-relative imports, read from the config's stored JSON parse."""
import json

import pytest

from app.config.constants.arangodb import RecordRelations
from app.modules.code_graph.block_projection import (
    BlockProjectionContext,
    write_code_file_blocks_to_graph,
)
from app.modules.code_graph.deleted_files import defined_names
from app.modules.code_graph.edge_builder import EdgeBuildResult, build_code_graph_edges
from app.modules.code_graph.facts_source import facts_from_blob_record
from app.modules.code_graph.import_resolution import ImportResolution
from app.modules.code_graph.ts_config import TsConfigs, is_ts_config
from app.modules.parsers.code_parser import CodeFileParser
from app.modules.parsers.json.json_parser import JSONParser, _loads_allowing_comments
from app.services.parsing.interface import ParseError

from .conftest import GROUP_ID, ORG_ID, FakeGraphProvider

IMPORTS = RecordRelations.IMPORTS.value
IMPORTS_FROM = RecordRelations.IMPORTS_FROM.value


def _parse(source: bytes) -> dict:
    """The record blob production stores for a JSON file (process_structured_document)."""
    container = JSONParser().parse_data(_loads_allowing_comments(source.decode()), "tsconfig.json")
    return {"block_containers": container.model_dump(mode="json", exclude_none=True)}


def _fields(source: bytes) -> dict[str, str]:
    return facts_from_blob_record("cfg", _parse(source)).structured_fields


def _resolve(configs: dict[str, bytes | None], files: set[str], specifier: str, from_file: str) -> str | None:
    """*configs*: path -> JSON source, or None for one whose parse could not be read."""
    ts = TsConfigs({path: _fields(src) if src is not None else None for path, src in configs.items()})
    return ImportResolution(files | set(configs), ts_configs=ts).resolve(specifier, from_file, "js")


# -- reading the stored parse ------------------------------------------------

def test_the_stored_parse_gives_back_base_url_paths_and_extends() -> None:
    fields = _fields(b'{"extends": "../tsconfig.base.json", "compilerOptions": {"target": "es5", "lib": ["dom", "es6"],'
                     b' "plugins": [{"name": "next"}], "baseUrl": ".",'
                     b' "paths": {"@/*": ["./*"], "@/chat/*": ["./app/(main)/chat/*", "./chat/*"]}}}')
    assert fields["extends"] == "../tsconfig.base.json"
    assert fields["compilerOptions.baseUrl"] == "."
    assert fields["compilerOptions.paths.@/*"] == "./*"
    assert fields["compilerOptions.paths.@/chat/*"] == "./app/(main)/chat/*, ./chat/*"
    assert fields["compilerOptions.lib"] == "dom, es6"


def test_a_jsonc_config_is_read_with_its_comments_dropped() -> None:
    fields = _fields(b'{\n  // aliases\n  "compilerOptions": {\n    /* root */ "baseUrl": "src",\n'
                     b'    "paths": {"~lib/*": ["lib/*"]} // trailing\n  }\n}\n')
    assert fields == {"compilerOptions.baseUrl": "src", "compilerOptions.paths.~lib/*": "lib/*"}


def test_code_blobs_have_no_structured_fields() -> None:
    record = {"block_containers": {"blocks": [{"data": "def f(): pass", "format": "code",
                                               "code_metadata": {"qualified_name": "function:f"}}]}}
    assert facts_from_blob_record("r", record).structured_fields == {}


@pytest.mark.asyncio
async def test_a_broken_config_has_no_stored_parse() -> None:
    with pytest.raises(ParseError):
        await JSONParser().parse(b'{"compilerOptions": {"paths": ', "tsconfig.json")


def test_only_tsconfig_and_jsconfig_files_are_configs() -> None:
    assert is_ts_config("tsconfig.json") and is_ts_config("a/tsconfig.base.json") and is_ts_config("jsconfig.json")
    assert not is_ts_config("package.json") and not is_ts_config("tsconfig.ts")


# -- resolution ----------------------------------------------------------------

FRONTEND = b'{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./*"], "@/chat/*": ["./app/(main)/chat/*"]}}}'


@pytest.mark.parametrize(("specifier", "expected"), [
    ("@/chat/Panel", "app/(main)/chat/Panel.tsx"),        # the longer prefix wins over `@/*`
    ("@/utils/http", "utils/http.ts"),
    ("utils/http", "utils/http.ts"),                       # baseUrl
    ("react", None),
])
def test_paths_wildcards_and_base_url(specifier: str, expected: str | None) -> None:
    files = {"app/(main)/chat/Panel.tsx", "chat/Panel.tsx", "utils/http.ts", "app/page.tsx"}
    assert _resolve({"tsconfig.json": FRONTEND}, files, specifier, "app/page.tsx") == expected


def test_paths_without_base_url_are_relative_to_their_config_and_an_exact_pattern_wins() -> None:
    config = (b'{"compilerOptions": {"paths": {"lexical": ["./js/lexical/core/index.ts"],'
              b' "lexical/*": ["./js/lexical/core/*"], "@lexical/*": ["./js/lexical/*"]}}}')
    files = {"web/js/lexical/core/index.ts", "web/js/lexical/core/nodes.ts", "web/js/lexical/rich/index.ts",
             "web/js/app.ts"}
    configs = {"web/tsconfig.json": config}
    assert _resolve(configs, files, "lexical", "web/js/app.ts") == "web/js/lexical/core/index.ts"
    assert _resolve(configs, files, "lexical/nodes", "web/js/app.ts") == "web/js/lexical/core/nodes.ts"
    assert _resolve(configs, files, "@lexical/rich", "web/js/app.ts") == "web/js/lexical/rich/index.ts"


def test_extends_inherits_paths_relative_to_the_config_that_defines_them() -> None:
    base = b'{"compilerOptions": {"paths": {"@x/element": ["./element/src/index.ts"]}}}'
    child = b'{"extends": "../tsconfig.base.json", "compilerOptions": {"outDir": "dist"}}'
    files = {"packages/element/src/index.ts", "packages/app/src/main.ts"}
    configs = {"packages/tsconfig.base.json": base, "packages/app/tsconfig.json": child}
    assert _resolve(configs, files, "@x/element", "packages/app/src/main.ts") == "packages/element/src/index.ts"


def test_empty_paths_clear_the_extended_ones() -> None:
    base = b'{"compilerOptions": {"paths": {"@x/element": ["./element/src/index.ts"]}}}'
    child = b'{"extends": "../tsconfig.base.json", "compilerOptions": {"paths": {}}}'
    assert _fields(child)["compilerOptions.paths"] == "{}"

    def candidates(child_source: bytes) -> list[str]:
        configs = {"packages/tsconfig.base.json": _fields(base), "packages/app/tsconfig.json": _fields(child_source)}
        return TsConfigs(configs).candidates("@x/element", "packages/app/src/main.ts")

    assert candidates(b'{"extends": "../tsconfig.base.json"}') == ["packages/element/src/index.ts"]
    assert candidates(child) == []


def test_own_base_url_overrides_the_extended_one_and_extends_cycles_end() -> None:
    a = b'{"extends": "./tsconfig.b", "compilerOptions": {"baseUrl": "src"}}'
    b = b'{"extends": "./tsconfig.json", "compilerOptions": {"baseUrl": "lib"}}'
    files = {"src/util.ts", "lib/util.ts", "main.ts"}
    assert _resolve({"tsconfig.json": a, "tsconfig.b.json": b}, files, "util", "main.ts") == "src/util.ts"


def test_the_nearest_config_wins_and_tsconfig_outranks_jsconfig() -> None:
    root = b'{"compilerOptions": {"paths": {"@/*": ["./shared/*"]}}}'
    nested = b'{"compilerOptions": {"paths": {"@/*": ["./src/*"]}}}'
    files = {"shared/x.ts", "web/src/x.ts", "web/src/main.ts", "tool/main.ts"}
    configs = {"tsconfig.json": root, "web/tsconfig.json": nested,
               "web/jsconfig.json": b'{"compilerOptions": {"paths": {"@/*": ["./nowhere/*"]}}}'}
    assert _resolve(configs, files, "@/x", "web/src/main.ts") == "web/src/x.ts"
    assert _resolve(configs, files, "@/x", "tool/main.ts") == "shared/x.ts"


def test_a_broken_nearest_config_leaves_its_files_on_todays_rules() -> None:
    root = b'{"compilerOptions": {"paths": {"@/*": ["./shared/*"]}}}'
    files = {"shared/x.ts", "web/x.ts", "web/main.ts"}
    configs = {"tsconfig.json": root, "web/tsconfig.json": None}
    # today's root alias: the nearest ancestor holding the target, not the root config's mapping
    assert _resolve(configs, files, "@/x", "web/main.ts") == "web/x.ts"
    assert TsConfigs({"web/tsconfig.json": None}).unread == ["web/tsconfig.json"]


def test_an_unmatched_alias_falls_back_to_the_root_alias_rule() -> None:
    files = {"web/src/x.ts", "web/src/main.ts"}
    configs = {"web/tsconfig.json": b'{"compilerOptions": {"paths": {"#lib/*": ["./lib/*"]}}}'}
    assert _resolve(configs, files, "@/x", "web/src/main.ts") == "web/src/x.ts"


def test_relative_imports_and_targets_outside_the_repo_ignore_the_config() -> None:
    config = b'{"compilerOptions": {"baseUrl": "..", "paths": {"*": ["../../*"]}}}'
    files = {"web/a.ts", "web/main.ts", "a.ts"}
    assert _resolve({"web/tsconfig.json": config}, files, "./a", "web/main.ts") == "web/a.ts"
    assert _resolve({"tsconfig.json": config}, files, "a", "web/main.ts") is None


# -- builds --------------------------------------------------------------------

MAIN = b'import { http } from "@/lib/http";\nexport function main() { return http(); }\n'


async def _build(graph: FakeGraphProvider, touched: set[str] | None = None,
                 deleted_names: set[str] = frozenset()) -> EdgeBuildResult:
    return await build_code_graph_edges(graph_provider=graph, facts_source=graph, org_id=ORG_ID,
                                        record_group_id=GROUP_ID, touched_record_ids=touched,
                                        deleted_names=deleted_names)


def _put_config(graph: FakeGraphProvider, record_id: str, path: str, source: bytes) -> None:
    graph.code_files[record_id] = path
    try:
        graph.blobs[record_id] = _parse(source)
    except json.JSONDecodeError:   # production stores no parse for it
        graph.blobs.pop(record_id, None)


async def _repo(configs: dict[str, tuple[str, bytes]]) -> FakeGraphProvider:
    """web/src/main.ts importing `@/lib/http`, three files it could mean, and *configs* (record -> path, source)."""
    graph, parser = FakeGraphProvider(), CodeFileParser()
    for rid, (path, src) in configs.items():
        _put_config(graph, rid, path, src)
    for rid, path, src in (("main", "web/src/main.ts", MAIN),
                           *((rid, path, b"export function http() { return 1; }\n") for rid, path in (
                               ("h-lib", "web/lib/http.ts"), ("h-src", "web/src/lib/http.ts"),
                               ("h-alt", "web/alt/lib/http.ts")))):
        container = parser.parse_to_blocks(src, path.rsplit("/", 1)[-1], path, "typescript")
        graph.code_files[rid] = path
        graph.blobs[rid] = {"block_containers": container.model_dump(mode="json", exclude_none=True)}
        await write_code_file_blocks_to_graph(
            graph_provider=graph,
            context=BlockProjectionContext(org_id=ORG_ID, record_id=rid, record_group_id=GROUP_ID,
                                           connector_id="conn1", language="typescript", file_path=path),
            block_containers=container)
    return graph


def _import_targets(graph: FakeGraphProvider) -> set[str]:
    """Files main.ts's IMPORTS and IMPORTS_FROM edges land in."""
    def path(node_id: str) -> str | None:
        key = node_id.partition("/")[2]
        return graph.code_files.get(graph.blocks[key]["recordId"] if key in graph.blocks else key)

    return {path(e["_to"]) for e in graph.code_edges()
            if e["relationshipType"] in (IMPORTS, IMPORTS_FROM) and path(e["_from"]) == "web/src/main.ts"}


async def _full_targets(configs: dict[str, tuple[str, bytes]]) -> set[str]:
    fresh = await _repo(configs)
    await _build(fresh)
    return _import_targets(fresh)


WEB = ("web/tsconfig.json", b'{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./*"]}}}')
WEB_ALT = ("web/tsconfig.json", b'{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./alt/*"]}}}')
SRC = ("web/src/tsconfig.json", b'{"compilerOptions": {"paths": {"@/*": ["./*"]}}}')


@pytest.mark.asyncio
async def test_a_build_places_an_alias_import_by_the_config() -> None:
    graph = await _repo({"cfg": WEB})
    result = await _build(graph)
    assert _import_targets(graph) == {"web/lib/http.ts"}   # today's root alias picked web/src/lib/http.ts
    assert result.ts_configs_unread == 0


@pytest.mark.asyncio
async def test_a_broken_config_never_fails_the_build_and_is_counted() -> None:
    graph = await _repo({"cfg": ("web/tsconfig.json", b'{"compilerOptions": {,,}')})
    result = await _build(graph)
    assert _import_targets(graph) == {"web/src/lib/http.ts"}   # today's behaviour
    assert result.ts_configs_unread == 1
    assert "ts_configs_unread" in result.as_log_fields()


@pytest.mark.asyncio
async def test_without_a_facts_source_no_config_is_read() -> None:
    graph = await _repo({"cfg": WEB})
    result = await build_code_graph_edges(graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID)
    assert result.ts_configs_unread == 0


@pytest.mark.parametrize(("before", "after"), [
    ({"cfg": WEB}, {"cfg": WEB_ALT}),                     # edited
    ({"cfg": WEB}, {"cfg": WEB, "sub": SRC}),             # added in a subfolder
])
@pytest.mark.asyncio
async def test_a_changed_config_re_resolves_the_files_under_it(before, after) -> None:
    graph = await _repo(before)
    await _build(graph)
    was = _import_targets(graph)
    for rid, (path, src) in after.items():
        _put_config(graph, rid, path, src)
    touched = {rid for rid in after if after[rid] != before.get(rid)}
    result = await _build(graph, touched)
    assert result.files_reresolved > len(touched)
    assert was != _import_targets(graph) == await _full_targets(after)


@pytest.mark.asyncio
async def test_an_edited_extended_config_re_resolves_the_folders_extending_it() -> None:
    child = ("web/tsconfig.json", b'{"extends": "../tsconfig.base.json"}')
    base = ("tsconfig.base.json", b'{"compilerOptions": {"paths": {"@/*": ["./web/*"]}}}')
    base_alt = ("tsconfig.base.json", b'{"compilerOptions": {"paths": {"@/*": ["./web/alt/*"]}}}')
    graph = await _repo({"child": child, "base": base})
    await _build(graph)
    assert _import_targets(graph) == {"web/lib/http.ts"}
    _put_config(graph, "base", *base_alt)
    await _build(graph, {"base"})
    assert _import_targets(graph) == {"web/alt/lib/http.ts"} == await _full_targets({"child": child, "base": base_alt})


@pytest.mark.parametrize(("before", "deleted", "expected"), [
    ({"cfg": WEB, "sub": SRC}, "sub", "web/lib/http.ts"),      # falls back to the parent's config
    ({"cfg": WEB}, "cfg", "web/src/lib/http.ts"),              # falls back to today's behaviour
])
@pytest.mark.asyncio
async def test_a_deleted_config_re_resolves_the_files_under_its_folder(before, deleted, expected) -> None:
    graph = await _repo(before)
    await _build(graph)
    assert _import_targets(graph) != {expected}
    names = await defined_names(graph, ORG_ID, deleted)   # what the delete's sync point keeps
    graph.code_files.pop(deleted)
    graph.blobs.pop(deleted)
    await _build(graph, set(), names)
    remaining = {rid: cfg for rid, cfg in before.items() if rid != deleted}
    assert _import_targets(graph) == {expected} == await _full_targets(remaining)


def test_only_the_folders_a_config_reaches_are_re_resolved() -> None:
    configs = TsConfigs({"tsconfig.base.json": {}, "web/tsconfig.json": {"extends": "../tsconfig.base.json"},
                         "tool/jsconfig.json": {}})
    assert configs.folders_reading({"tsconfig.base.json"}) == {"web"}
    assert configs.folders_reading({"tool/jsconfig.json"}) == {"tool"}
    assert configs.folders_reading({"web/src/tsconfig.json"}) == {"web/src"}   # added or deleted
    assert configs.folders_reading({"web/main.ts"}) == set()
