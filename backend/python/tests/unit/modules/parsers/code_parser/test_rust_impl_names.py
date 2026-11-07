"""A Rust `impl` is named by the head type it implements for, without generic arguments, lifetimes, references
or a path, so `impl<'a> Renderer<'a>` holds `Renderer.new`, not `Renderer<'a>.new`, and edges into its methods
match the type's own name."""
import pytest

from app.modules.parsers.code_parser.code_file_parser import qualified_name_for
from app.modules.parsers.code_parser.engine import parse_code


def names(source: str) -> set[str]:
    """Every named symbol as ``kind:Scope.name``."""
    return {qualified_name_for(s.kind, s.name, s.parent_chain)
            for s in parse_code(source.encode(), "rust").symbols if s.name}


@pytest.mark.parametrize("impl_header,expected", [
    ("impl<T> Foo<T>", "Foo"),
    ("impl<'a> Renderer<'a>", "Renderer"),
    ("impl<T: Clone> Clone for Wrapper<T>", "Wrapper"),
    ("impl Display for &Bar", "Bar"),
    ("impl<'a> Display for &'a mut Bar", "Bar"),
    ("impl fmt::Display for crate::model::Baz<u8>", "Baz"),
    ("impl<T> From<T> for Vec<T>", "Vec"),
    ("impl Iterator for Box<dyn Source>", "Box"),
])
def test_rust_impl_is_named_by_its_head_type(impl_header: str, expected: str) -> None:
    got = names(f"struct X;\n{impl_header} {{\n    fn new() -> Self {{ todo!() }}\n}}\n")
    assert f"impl:{expected}" in got
    assert any(n.endswith(f":{expected}.new") for n in got), sorted(got)
    assert not any("<" in n or "&" in n or "::" in n for n in got), sorted(got)


def test_rust_impl_of_a_type_parameter_and_of_a_slice_keep_a_name() -> None:
    got = names("impl<T: Show> Print for T {\n    fn print(&self) {}\n}\n"
                "impl<'a> Print for &'a [u8] {\n    fn print(&self) {}\n}\n")
    assert "impl:T" in got and any(n.endswith(":T.print") for n in got)
    assert any(n.startswith("impl:") and n != "impl:T" for n in got)   # a slice still gets a name
    assert not any("&" in n for n in got), sorted(got)                 # without its reference


def test_rust_methods_of_two_generic_impls_of_one_type_share_its_name() -> None:
    got = names("struct Cell<T>(T);\nimpl<T> Cell<T> {\n    fn get(&self) {}\n}\n"
                "impl Cell<String> {\n    fn text(&self) {}\n}\n")
    assert {n for n in got if n.endswith((".get", ".text"))} == {"method:Cell.get", "method:Cell.text"}
