"""A bare JS/TS call to a parameter or local is no CALLS fact: a `let`/`const`, a `for…of` variable, a
`catch` parameter or a case binding shadows only its block, a `var` the whole function.
"""
import pytest

from app.config.constants.arangodb import RecordRelations

from .test_import_binding import _build

CALLS = RecordRelations.CALLS.value


pytestmark = pytest.mark.asyncio


def _callers_of(graph, edges: set, target: str) -> set[str]:
    return {graph.blocks[src]["qualifiedName"] for src, dst in edges
            if src in graph.blocks and graph.blocks.get(dst, {}).get("qualifiedName") == target}


TS = b'''
import { helper } from "./helpers";

export function run(): number {
  function g(): number {
    return 1;
  }
  submit(g);
  return g() + helper();
}

export function shadowed(g: () => number): number {
  return g();
}
'''


BLOCK_SCOPED = b'''
export const copyOne = (el: number): number => el + 1;

export const copyAll = (els: number[]): number[] => {
  const out = [copyOne(els[0])];
  for (const copyOne of els) {
    out.push(copyOne);
  }
  if (els.length) {
    let copyOne = 2;
    out.push(copyOne);
  }
  return out;
};

export function loopsOnly(els: number[]): number {
  for (const copyOne of els) {
    copyOne();
  }
  {
    const copyOne = els[0];
    return copyOne();
  }
}

export function whole(els: number[]): number {
  const copyOne = els.length;
  return copyOne();
}
'''


async def test_a_block_scoped_let_or_const_shadows_only_its_block(graph, index_file) -> None:
    """excalidraw duplicate.ts: `for (const duplicateElement of …)` further down does not hide the module's
    `duplicateElement` from a call above the loop; inside the loop (or a block's own `let`) it does, and a
    function-level `const` still shadows the whole function."""
    await index_file("recB", "web/copy.ts", BLOCK_SCOPED, "typescript")
    await _build(graph)
    edges = {(e["_from"].partition("/")[2], e["_to"].partition("/")[2]) for e in graph.code_edges(CALLS)}
    callers = _callers_of(graph, edges, "function:copyOne")
    assert callers == {"function:copyAll"}


BLOCKS = b'''
function helper() { return 1; }

function before(x) { helper(); if (x) { let helper = () => 2; helper(); } }
function after(x) { if (x) { let helper = pick(x); helper(); } helper(); }
function inElse(x) { if (x) { const helper = 3; } else { helper(); } }
function insideOnly(x) { if (x) { const helper = () => 2; helper(); } }
function varHoists(x) { helper(); if (x) { var helper = () => 2; } }
function varInLoop(xs) { helper(); for (const x of xs) { var helper = x; } }
function tdz(x) { if (x) { helper(); let helper = pick(x); } }
function caught() { try { helper(); } catch (helper) { helper(); } }
function caughtOnly() { try { pick(1); } catch (helper) { helper(); } }
function switched(k) { helper(); switch (k) { case 1: let helper = pick(k); helper(); } }
function switchedOnly(k) { switch (k) { case 1: let helper = pick(k); helper(); } }
'''


@pytest.mark.parametrize(("language", "path"), [("javascript", "web/blocks.js"), ("typescript", "web/blocks.ts")])
async def test_js_block_scopes(graph, index_file, language, path) -> None:
    """A `let`/`const` (a function value too) shadows only its block, `catch (e)` only its clause, a `case`'s
    `let` only its `switch`; a `var` (also inside a `for (const x of …)` body) shadows the whole function, and a
    use before a block's `let` in that block is still the `let`."""
    await index_file("recJ", path, BLOCKS, language)
    await _build(graph)
    edges = {(e["_from"].partition("/")[2], e["_to"].partition("/")[2]) for e in graph.code_edges(CALLS)}
    assert _callers_of(graph, edges, "function:helper") == {
        "function:before", "function:after", "function:inElse", "function:caught", "function:switched"}
