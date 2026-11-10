"""Rust's parser facts: `use` items, re-exports, trait implementations, the type table."""
import pytest

from app.config.constants.arangodb import RecordRelations
from app.modules.parsers.code_parser.engine import parse_code

SRC = b"""use crate::util::{double, half as h, self};
use super::model::History;
use std::collections::HashMap;
use serde;
pub use model::*;
use other::*;

pub trait Store: Send + Bound { fn save(&self); }

struct Engine { client: Client, store: Box<dyn Store> }

impl<T> Store for Engine<T> { fn save(&self) { self.client.post(); } }
impl<T: Store> Bound for T {}
impl Clone for Engine { fn clone(&self) -> Self { todo!() } }

fn make(settings: &Settings, n: u32) {
    let a = History::new();
    let b: Settings = load();
    let c = Engine { client: Client::new() };
}

mod tests {
    use super::make;
    use super::super::Top;
}
"""


def facts(relation: str) -> list[tuple]:
    return [(f.to_name, f.qualified_prefix, f.local_name, f.from_kind) for f in parse_code(SRC, "rust").pending
            if getattr(f.relation, "value", f.relation) == relation]


def test_a_use_imports_each_item_for_the_file() -> None:
    assert facts(RecordRelations.IMPORTS.value) == [
        ("double", "crate::util::double", None, "record"), ("half", "crate::util::half", "h", "record"),
        ("util", "crate::util", None, "record"), ("History", "super::model::History", None, "record"),
        ("HashMap", "std::collections::HashMap", None, "record"),
        # inside `mod tests`, super is the file's own module
        ("make", "self::make", None, "record"), ("Top", "super::Top", None, "record")]
    assert [f[0] for f in facts(RecordRelations.IMPORTS_FROM.value)] == [
        "crate::util::double", "crate::util::half", "crate::util", "super::model::History",
        "std::collections::HashMap", "self::make", "super::Top"]   # no bare crate, no glob


def test_only_a_public_glob_is_a_re_export() -> None:
    assert [f[0] for f in facts(RecordRelations.RE_EXPORTS.value)] == ["model"]


def test_impls_and_supertraits_are_heritage_but_not_blanket_impls() -> None:
    marked = lambda rel: [(f.to_name, f.context) for f in parse_code(SRC, "rust").pending  # noqa: E731
                          if getattr(f.relation, "value", f.relation) == rel]
    # the prelude's names are marked for the builder, which knows the file's bindings
    assert marked(RecordRelations.IMPLEMENTS.value) == [("Store", None), ("Clone", "prelude")]
    assert marked(RecordRelations.INHERITS.value) == [("Send", "prelude"), ("Bound", None)]


def test_a_qualified_base_is_written_as_the_file_s_module_sees_it() -> None:
    src = (b"mod sealed { pub trait Sealed {} }\nstruct A;\nimpl sealed::Sealed for A {}\n"
           b"mod tests {\n    struct B;\n    impl super::hooks::Hooks for B {}\n}\n")
    heritage = [(f.to_name, f.qualified_prefix) for f in parse_code(src, "rust").pending
                if getattr(f.relation, "value", f.relation) == RecordRelations.IMPLEMENTS.value]
    assert heritage == [("Sealed", "self::sealed"), ("Hooks", "self::hooks")]


def test_a_use_of_a_module_the_file_declares_is_written_self_relative() -> None:
    # `#[path]` and inline modules: the tree rules cannot see them, the file's `mod` line can.
    src = (b'#[path = "common/agents.rs"]\nmod agents;\nmod mock { pub struct Env; }\n'
           b"use agents::{Agent, ran};\nuse mock::Env;\nuse tokio::sync::Mutex;\n")
    imports = [f.to_name for f in parse_code(src, "rust").pending
               if getattr(f.relation, "value", f.relation) == RecordRelations.IMPORTS_FROM.value]
    assert imports == ["self::agents::Agent", "self::agents::ran", "self::mock::Env", "tokio::sync::Mutex"]


@pytest.mark.parametrize("src, written", [
    (b"// mod serde; was removed\nuse serde::Serialize;\n", "serde::Serialize"),                   # a comment
    (b"use time::Duration;\nmod tests { mod time {} }\n", "time::Duration"),                       # another scope
    (b"mod inner { use inner::Thing; }\n", "inner::Thing"),                                         # its own name
], ids=["comment", "module-in-another-scope", "inside-the-module-itself"])
def test_only_a_module_declared_in_the_use_s_own_scope_is_self_relative(src, written) -> None:
    # the module comes from the parse tree, never from text elsewhere in the file.
    imports = [f.to_name for f in parse_code(src, "rust").pending
               if getattr(f.relation, "value", f.relation) == RecordRelations.IMPORTS_FROM.value]
    assert imports == [written]


def test_a_module_declared_beside_a_use_inside_an_inline_module_is_self_relative() -> None:
    src = b"mod outer {\n    mod helpers { pub struct H; }\n    use helpers::H;\n}\n"
    imports = [f.to_name for f in parse_code(src, "rust").pending
               if getattr(f.relation, "value", f.relation) == RecordRelations.IMPORTS_FROM.value]
    assert imports == ["self::helpers::H"]


def test_the_type_table_reads_lets_parameters_and_fields() -> None:
    table = parse_code(SRC, "rust").type_table
    assert {k: table.get(k) for k in ("a", "b", "c", "settings", "client", "store")} == {
        "a": "History", "b": "Settings", "c": "Engine", "settings": "Settings", "client": "Client",
        "store": "*Store"}   # a Box points at what it holds, a trait object is its trait
    assert "n" not in table   # a builtin
