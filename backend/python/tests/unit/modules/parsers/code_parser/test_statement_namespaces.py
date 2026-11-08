"""A namespace written as a statement names every definition after it.

C# `namespace X;` and PHP `namespace A\\B;` own no body: the definitions they
cover are their siblings. Without this, a file-scoped C# class was `class:Order`
while the answer key, and the block form, name it `class:Shop.Orders.Order`.
"""
from app.modules.parsers.code_parser import CodeFileParser, detect_language


def names(src: bytes, path: str) -> list[str]:
    container = CodeFileParser().parse_to_blocks(src, path.rsplit("/", 1)[-1], path, detect_language(path))
    found = [b.code_metadata.qualified_name for b in container.blocks if b.code_metadata]
    found += [g.code_metadata.qualified_name for g in container.block_groups if g.code_metadata]
    return [n for n in found if n and n.split(":", 1)[0] in ("class", "interface", "method", "function", "struct")]


CSHARP = (b"using System;\nnamespace Shop.Orders;\n\npublic class Order {\n  public void Pay() { Log(); }\n}\n\n"
          b"public interface IRepo { void Save(); }\n")


def test_a_file_scoped_csharp_namespace_prefixes_every_type_after_it() -> None:
    assert sorted(names(CSHARP, "src/Order.cs")) == [
        "class:Shop.Orders.Order", "interface:Shop.Orders.IRepo",
        "method:Shop.Orders.IRepo.Save", "method:Shop.Orders.Order.Pay"]


def test_a_csharp_block_namespace_is_named_the_same_way() -> None:
    src = b"namespace Shop.Orders {\n  public class Order { public void Pay() {} }\n}\n"
    assert sorted(names(src, "src/Order.cs")) == ["class:Shop.Orders.Order", "method:Shop.Orders.Order.Pay"]


PHP = (b"<?php\nnamespace App\\Models;\n\nuse Foo\\Bar;\n\nclass User {\n  public function save() { $this->x(); }\n}\n\n"
       b"function helper() {}\n\nnamespace App\\Other;\n\nclass Thing {}\n")


def test_a_php_namespace_statement_names_what_follows_up_to_the_next_one() -> None:
    assert sorted(names(PHP, "app/Models/User.php")) == [
        "class:App.Models.User", "class:App.Other.Thing", "function:App.Models.helper",
        "method:App.Models.User.save"]


def test_a_php_block_namespace_uses_dots_too() -> None:
    src = b"<?php\nnamespace App\\Models {\n  class User {}\n}\n"
    assert names(src, "app/User.php") == ["class:App.Models.User"]


def test_definitions_before_the_namespace_statement_keep_no_prefix() -> None:
    src = b"<?php\nfunction early() {}\nnamespace App;\nfunction late() {}\n"
    assert sorted(names(src, "a.php")) == ["function:App.late", "function:early"]


def test_a_rust_mod_declaration_is_not_a_namespace_statement() -> None:
    # `mod foo;` declares a file module: an import, not a container of what follows.
    assert names(b"mod foo;\nfn g() {}\n", "src/lib.rs") == ["function:g"]
