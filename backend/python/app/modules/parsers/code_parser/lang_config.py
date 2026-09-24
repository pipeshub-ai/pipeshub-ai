"""Per-language tree-sitter node-type configuration.

Adding a language is one ``LanguageConfig`` entry plus its grammar dependency;
the walker in ``engine.py`` never changes. Node-type names come from each
grammar's ``node-types.json``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "COMMENT_NODE_TYPES",
    "LANGUAGES",
    "SUPPORTED_CODE_EXTENSIONS",
    "LanguageConfig",
    "config_for_extension",
    "config_for_language",
    "detect_language",
]


# Grammars disagree on what a comment node is called, and a name missing from
# this set turns a doc comment into a block of its own instead of attaching it
# to the definition below.
COMMENT_NODE_TYPES = frozenset({
    "comment", "line_comment", "block_comment", "multiline_comment",
    "documentation_comment", "doc_comment",
})

_ATTACHED_DEFAULT = COMMENT_NODE_TYPES | frozenset({"decorator"})


@dataclass(frozen=True, eq=False)
class LanguageConfig:
    name: str
    ts_module: str
    ts_language_fn: str
    extensions: frozenset[str]

    # Definitions. Each set maps its node types onto one block kind.
    class_types: frozenset[str] = frozenset()
    interface_types: frozenset[str] = frozenset()
    enum_types: frozenset[str] = frozenset()
    struct_types: frozenset[str] = frozenset()
    trait_types: frozenset[str] = frozenset()
    module_types: frozenset[str] = frozenset()
    impl_types: frozenset[str] = frozenset()
    type_alias_types: frozenset[str] = frozenset()
    function_types: frozenset[str] = frozenset()
    method_types: frozenset[str] = frozenset()
    field_types: frozenset[str] = frozenset()

    # Wraps a definition and lends it its own byte range, so decorator, template
    # and `type (...)` text stays inside the block instead of splitting off.
    decorator_wrapper_types: frozenset[str] = frozenset()
    # Precedes a definition and belongs to it. Comments everywhere; Rust and C#
    # attach attributes as siblings rather than as children of the definition.
    attached_types: frozenset[str] = _ATTACHED_DEFAULT
    # Grammars that split one definition across a signature node and a sibling
    # body node -- Dart's `function_signature` + `function_body`. Without this
    # the block for a method would stop at its signature.
    trailing_body_types: frozenset[str] = frozenset()

    # Span boundaries that are not definitions.
    import_types: frozenset[str] = frozenset()
    export_types: frozenset[str] = frozenset()

    # `const handler = () => {}` binds a function to a name without being a
    # function node. Statements listed here are checked for that shape.
    binding_types: frozenset[str] = frozenset()
    function_value_types: frozenset[str] = frozenset()

    name_field: str = "name"
    body_field: str = "body"
    # For the one definition in a language that names itself through a different
    # field: Rust's `impl_item` carries its name in `type`.
    name_field_overrides: dict[str, str] = field(default_factory=dict)
    # Checked when the name field is absent: Ruby names a class with a bare
    # `constant` child, Kotlin and Swift with a `simple_identifier`.
    name_fallback_child_types: tuple[str, ...] = (
        "identifier", "type_identifier", "property_identifier",
    )
    # Checked when the body field is absent: Kotlin and Swift hang the member
    # list off a typed child rather than off a named field.
    body_fallback_child_types: tuple[str, ...] = ()
    # C, C++, Java and Groovy bury the name under a `declarator` chain.
    unwrap_declarator: bool = False

    # Kinds whose body is tiled into member blocks.
    container_kinds: frozenset[str] = frozenset(
        {"class", "interface", "enum", "struct", "trait", "module", "impl"}
    )
    # Of those, the ones that are types: a function declared directly inside one
    # is a method. A namespace or module is a container but not a type, so its
    # functions stay functions.
    method_container_kinds: frozenset[str] = frozenset(
        {"class", "interface", "enum", "struct", "trait", "impl"}
    )

    docstring_style: str = "none"  # python | block_comment | line_comment | none
    doc_line_prefixes: tuple[str, ...] = ()


_BLOCK_DOC = "block_comment"
_LINE_DOC = "line_comment"

_C_FAMILY_NAMES = ("identifier", "type_identifier", "field_identifier")


_PYTHON = LanguageConfig(
    name="python",
    ts_module="tree_sitter_python",
    ts_language_fn="language",
    extensions=frozenset({"py", "pyi"}),
    class_types=frozenset({"class_definition"}),
    function_types=frozenset({"function_definition"}),
    field_types=frozenset({"assignment"}),
    decorator_wrapper_types=frozenset({"decorated_definition"}),
    import_types=frozenset({"import_statement", "import_from_statement", "future_import_statement"}),
    docstring_style="python",
)

_JS_FUNCTION_TYPES = frozenset({
    "function_declaration", "generator_function_declaration",
    "function_expression", "arrow_function",
})
_JS_BINDING_TYPES = frozenset({"lexical_declaration", "variable_declaration"})
_JS_FUNCTION_VALUE_TYPES = frozenset({"arrow_function", "function_expression"})

_JAVASCRIPT = LanguageConfig(
    name="javascript",
    ts_module="tree_sitter_javascript",
    ts_language_fn="language",
    extensions=frozenset({"js", "jsx", "mjs", "cjs"}),
    class_types=frozenset({"class_declaration", "class"}),
    function_types=_JS_FUNCTION_TYPES,
    method_types=frozenset({"method_definition"}),
    field_types=frozenset({"field_definition", "public_field_definition"}),
    import_types=frozenset({"import_statement"}),
    export_types=frozenset({"export_statement"}),
    binding_types=_JS_BINDING_TYPES,
    function_value_types=_JS_FUNCTION_VALUE_TYPES,
    docstring_style=_BLOCK_DOC,
)

_TS_CLASS_TYPES = frozenset({"class_declaration", "abstract_class_declaration", "class"})
_TS_METHOD_TYPES = frozenset({"method_definition", "method_signature", "abstract_method_signature"})
_TS_FIELD_TYPES = frozenset({"public_field_definition", "property_signature", "field_definition"})
_TS_IMPORT_TYPES = frozenset({"import_statement", "import_alias"})

_TYPESCRIPT = LanguageConfig(
    name="typescript",
    ts_module="tree_sitter_typescript",
    ts_language_fn="language_typescript",
    extensions=frozenset({"ts", "mts", "cts"}),
    class_types=_TS_CLASS_TYPES,
    interface_types=frozenset({"interface_declaration"}),
    enum_types=frozenset({"enum_declaration"}),
    module_types=frozenset({"internal_module", "module"}),
    type_alias_types=frozenset({"type_alias_declaration"}),
    function_types=_JS_FUNCTION_TYPES,
    method_types=_TS_METHOD_TYPES,
    field_types=_TS_FIELD_TYPES,
    import_types=_TS_IMPORT_TYPES,
    export_types=frozenset({"export_statement"}),
    binding_types=_JS_BINDING_TYPES,
    function_value_types=_JS_FUNCTION_VALUE_TYPES,
    docstring_style=_BLOCK_DOC,
)

_TSX = LanguageConfig(
    name="tsx",
    ts_module="tree_sitter_typescript",
    ts_language_fn="language_tsx",
    extensions=frozenset({"tsx"}),
    class_types=_TS_CLASS_TYPES,
    interface_types=_TYPESCRIPT.interface_types,
    enum_types=_TYPESCRIPT.enum_types,
    module_types=_TYPESCRIPT.module_types,
    type_alias_types=_TYPESCRIPT.type_alias_types,
    function_types=_JS_FUNCTION_TYPES,
    method_types=_TS_METHOD_TYPES,
    field_types=_TS_FIELD_TYPES,
    import_types=_TS_IMPORT_TYPES,
    export_types=frozenset({"export_statement"}),
    binding_types=_JS_BINDING_TYPES,
    function_value_types=_JS_FUNCTION_VALUE_TYPES,
    docstring_style=_BLOCK_DOC,
)

_C = LanguageConfig(
    name="c",
    ts_module="tree_sitter_c",
    ts_language_fn="language",
    extensions=frozenset({"c"}),
    struct_types=frozenset({"struct_specifier", "union_specifier"}),
    enum_types=frozenset({"enum_specifier"}),
    type_alias_types=frozenset({"type_definition"}),
    function_types=frozenset({"function_definition"}),
    field_types=frozenset({"field_declaration"}),
    import_types=frozenset({"preproc_include"}),
    name_fallback_child_types=_C_FAMILY_NAMES,
    body_fallback_child_types=("field_declaration_list", "enumerator_list", "compound_statement"),
    unwrap_declarator=True,
    docstring_style=_BLOCK_DOC,
)

_CPP = LanguageConfig(
    name="cpp",
    ts_module="tree_sitter_cpp",
    ts_language_fn="language",
    # .h uses the C++ grammar rather than the C one: it is a superset, headers
    # routinely hold C++, and the node types below are identical for the C subset.
    extensions=frozenset({"cpp", "cc", "cxx", "hpp", "hxx", "h"}),
    class_types=frozenset({"class_specifier"}),
    struct_types=frozenset({"struct_specifier", "union_specifier"}),
    enum_types=frozenset({"enum_specifier"}),
    module_types=frozenset({"namespace_definition"}),
    type_alias_types=frozenset({"alias_declaration", "type_definition"}),
    function_types=frozenset({"function_definition"}),
    field_types=frozenset({"field_declaration"}),
    decorator_wrapper_types=frozenset({"template_declaration"}),
    import_types=frozenset({"preproc_include"}),
    name_fallback_child_types=_C_FAMILY_NAMES,
    body_fallback_child_types=(
        "field_declaration_list", "declaration_list", "enumerator_list", "compound_statement",
    ),
    unwrap_declarator=True,
    docstring_style=_BLOCK_DOC,
)

_CSHARP = LanguageConfig(
    name="csharp",
    ts_module="tree_sitter_c_sharp",
    ts_language_fn="language",
    extensions=frozenset({"cs"}),
    class_types=frozenset({"class_declaration", "record_declaration"}),
    interface_types=frozenset({"interface_declaration"}),
    enum_types=frozenset({"enum_declaration"}),
    struct_types=frozenset({"struct_declaration", "record_struct_declaration"}),
    module_types=frozenset({"namespace_declaration", "file_scoped_namespace_declaration"}),
    function_types=frozenset({"local_function_statement"}),
    method_types=frozenset({
        "method_declaration", "constructor_declaration", "destructor_declaration",
        "property_declaration", "operator_declaration", "indexer_declaration",
    }),
    field_types=frozenset({"field_declaration", "event_field_declaration"}),
    attached_types=_ATTACHED_DEFAULT | frozenset({"attribute_list"}),
    import_types=frozenset({"using_directive", "extern_alias_directive"}),
    name_fallback_child_types=("identifier",),
    body_fallback_child_types=("declaration_list", "enum_member_declaration_list", "block"),
    docstring_style=_LINE_DOC,
    doc_line_prefixes=("///", "//"),
)

_JAVA = LanguageConfig(
    name="java",
    ts_module="tree_sitter_java",
    ts_language_fn="language",
    extensions=frozenset({"java"}),
    class_types=frozenset({"class_declaration", "record_declaration"}),
    interface_types=frozenset({"interface_declaration", "annotation_type_declaration"}),
    enum_types=frozenset({"enum_declaration"}),
    method_types=frozenset({
        "method_declaration", "constructor_declaration", "compact_constructor_declaration",
    }),
    field_types=frozenset({"field_declaration"}),
    import_types=frozenset({"import_declaration", "package_declaration"}),
    name_fallback_child_types=("identifier", "type_identifier"),
    body_fallback_child_types=(
        "class_body", "interface_body", "enum_body", "annotation_type_body", "block",
    ),
    unwrap_declarator=True,
    docstring_style=_BLOCK_DOC,
)

_KOTLIN = LanguageConfig(
    name="kotlin",
    ts_module="tree_sitter_kotlin",
    ts_language_fn="language",
    extensions=frozenset({"kt", "kts"}),
    class_types=frozenset({"class_declaration", "object_declaration"}),
    function_types=frozenset({"function_declaration"}),
    field_types=frozenset({"property_declaration"}),
    # Grammar 1.1.0 names the import node `import`; older forks use `import_header`.
    import_types=frozenset({"import", "import_header", "package_header"}),
    name_fallback_child_types=("simple_identifier", "identifier", "type_identifier"),
    body_fallback_child_types=("class_body", "enum_class_body", "function_body"),
    docstring_style=_BLOCK_DOC,
)

_SCALA = LanguageConfig(
    name="scala",
    ts_module="tree_sitter_scala",
    ts_language_fn="language",
    extensions=frozenset({"scala"}),
    class_types=frozenset({"class_definition", "object_definition"}),
    trait_types=frozenset({"trait_definition"}),
    enum_types=frozenset({"enum_definition"}),
    type_alias_types=frozenset({"type_definition"}),
    function_types=frozenset({"function_definition", "function_declaration"}),
    field_types=frozenset({"val_definition", "var_definition"}),
    import_types=frozenset({"import_declaration", "package_clause"}),
    name_fallback_child_types=("identifier",),
    body_fallback_child_types=("template_body", "block"),
    docstring_style=_BLOCK_DOC,
)

_GROOVY = LanguageConfig(
    name="groovy",
    ts_module="tree_sitter_groovy",
    ts_language_fn="language",
    extensions=frozenset({"groovy", "gradle"}),
    class_types=frozenset({"class_declaration"}),
    interface_types=frozenset({"interface_declaration"}),
    method_types=frozenset({"method_declaration", "constructor_declaration"}),
    field_types=frozenset({"field_declaration"}),
    import_types=frozenset({"import_declaration", "package_declaration"}),
    name_fallback_child_types=("identifier",),
    body_fallback_child_types=("class_body", "interface_body", "block"),
    unwrap_declarator=True,
    docstring_style=_BLOCK_DOC,
)

_GO = LanguageConfig(
    name="go",
    ts_module="tree_sitter_go",
    ts_language_fn="language",
    extensions=frozenset({"go"}),
    # `type Foo struct {...}` is a `type_declaration` wrapping a `type_spec`, so
    # the wrapper lends its range and the spec supplies the name.
    struct_types=frozenset({"type_spec"}),
    function_types=frozenset({"function_declaration"}),
    method_types=frozenset({"method_declaration"}),
    field_types=frozenset({"field_declaration"}),
    decorator_wrapper_types=frozenset({"type_declaration"}),
    import_types=frozenset({"import_declaration", "package_clause"}),
    name_fallback_child_types=_C_FAMILY_NAMES,
    body_fallback_child_types=("block",),
    docstring_style=_LINE_DOC,
    doc_line_prefixes=("//",),
)

_RUST = LanguageConfig(
    name="rust",
    ts_module="tree_sitter_rust",
    ts_language_fn="language",
    extensions=frozenset({"rs"}),
    struct_types=frozenset({"struct_item", "union_item"}),
    enum_types=frozenset({"enum_item"}),
    trait_types=frozenset({"trait_item"}),
    impl_types=frozenset({"impl_item"}),
    module_types=frozenset({"mod_item"}),
    type_alias_types=frozenset({"type_item"}),
    function_types=frozenset({"function_item", "function_signature_item"}),
    field_types=frozenset({"field_declaration", "const_item", "static_item"}),
    attached_types=_ATTACHED_DEFAULT | frozenset({"attribute_item"}),
    import_types=frozenset({"use_declaration", "extern_crate_declaration"}),
    name_field_overrides={"impl_item": "type"},
    name_fallback_child_types=("identifier", "type_identifier"),
    body_fallback_child_types=(
        "declaration_list", "field_declaration_list", "enum_variant_list", "block",
    ),
    docstring_style=_LINE_DOC,
    doc_line_prefixes=("///", "//!", "//"),
)

_RUBY = LanguageConfig(
    name="ruby",
    ts_module="tree_sitter_ruby",
    ts_language_fn="language",
    extensions=frozenset({"rb"}),
    class_types=frozenset({"class", "singleton_class"}),
    module_types=frozenset({"module"}),
    method_types=frozenset({"method", "singleton_method"}),
    field_types=frozenset({"assignment"}),
    name_fallback_child_types=("constant", "scope_resolution", "identifier"),
    body_fallback_child_types=("body_statement",),
    docstring_style=_LINE_DOC,
    doc_line_prefixes=("#",),
)

_PHP = LanguageConfig(
    name="php",
    ts_module="tree_sitter_php",
    ts_language_fn="language_php",
    extensions=frozenset({"php"}),
    class_types=frozenset({"class_declaration"}),
    interface_types=frozenset({"interface_declaration"}),
    trait_types=frozenset({"trait_declaration"}),
    enum_types=frozenset({"enum_declaration"}),
    module_types=frozenset({"namespace_definition"}),
    function_types=frozenset({"function_definition"}),
    method_types=frozenset({"method_declaration"}),
    field_types=frozenset({"property_declaration", "const_declaration"}),
    attached_types=_ATTACHED_DEFAULT | frozenset({"attribute_list"}),
    import_types=frozenset({"namespace_use_declaration"}),
    name_fallback_child_types=("name",),
    body_fallback_child_types=(
        "declaration_list", "enum_declaration_list", "compound_statement",
    ),
    docstring_style=_BLOCK_DOC,
)

_SWIFT = LanguageConfig(
    name="swift",
    ts_module="tree_sitter_swift",
    ts_language_fn="language",
    extensions=frozenset({"swift"}),
    # This grammar folds class, struct and extension into `class_declaration`.
    class_types=frozenset({"class_declaration"}),
    trait_types=frozenset({"protocol_declaration"}),
    function_types=frozenset({"function_declaration", "protocol_function_declaration"}),
    method_types=frozenset({
        "init_declaration", "deinit_declaration", "subscript_declaration",
    }),
    field_types=frozenset({"property_declaration"}),
    import_types=frozenset({"import_declaration"}),
    name_fallback_child_types=("simple_identifier", "type_identifier", "user_type"),
    body_fallback_child_types=(
        "class_body", "protocol_body", "enum_class_body", "function_body",
    ),
    docstring_style=_LINE_DOC,
    doc_line_prefixes=("///", "//"),
)

_DART = LanguageConfig(
    name="dart",
    ts_module="tree_sitter_dart",
    ts_language_fn="language",
    extensions=frozenset({"dart"}),
    class_types=frozenset({"class_definition"}),
    enum_types=frozenset({"enum_declaration"}),
    module_types=frozenset({"mixin_declaration", "extension_declaration"}),
    function_types=frozenset({"function_signature"}),
    method_types=frozenset({"method_signature"}),
    # This grammar emits the body as a sibling of the signature rather than a
    # child of it, so without this a function block would be its signature only.
    trailing_body_types=frozenset({"function_body"}),
    import_types=frozenset({"import_or_export", "library_name", "part_directive"}),
    name_fallback_child_types=("identifier", "type_identifier"),
    body_fallback_child_types=("class_body", "enum_body", "function_body", "block"),
    docstring_style=_LINE_DOC,
    doc_line_prefixes=("///", "//"),
)

_LUA = LanguageConfig(
    name="lua",
    ts_module="tree_sitter_lua",
    ts_language_fn="language",
    extensions=frozenset({"lua"}),
    function_types=frozenset({"function_declaration", "function_definition"}),
    name_fallback_child_types=(
        "identifier", "dot_index_expression", "method_index_expression",
    ),
    body_fallback_child_types=("block",),
    docstring_style=_LINE_DOC,
    doc_line_prefixes=("---", "--"),
)


_ALL_CONFIGS = (
    _PYTHON, _JAVASCRIPT, _TYPESCRIPT, _TSX,
    _C, _CPP, _CSHARP, _JAVA, _KOTLIN, _SCALA, _GROOVY,
    _GO, _RUST, _RUBY, _PHP, _SWIFT, _DART, _LUA,
)

LANGUAGES: dict[str, LanguageConfig] = {cfg.name: cfg for cfg in _ALL_CONFIGS}


def _build_extension_index() -> dict[str, LanguageConfig]:
    """Two configs claiming one extension would resolve by dict order, silently
    routing a language to the wrong grammar."""
    index: dict[str, LanguageConfig] = {}
    for cfg in _ALL_CONFIGS:
        for ext in cfg.extensions:
            if ext in index:
                raise ValueError(
                    f"extension {ext!r} claimed by both {index[ext].name!r} and {cfg.name!r}"
                )
            index[ext] = cfg
    return index


_EXT_TO_CONFIG = _build_extension_index()

SUPPORTED_CODE_EXTENSIONS: frozenset[str] = frozenset(_EXT_TO_CONFIG)


def config_for_extension(extension: str) -> LanguageConfig | None:
    return _EXT_TO_CONFIG.get((extension or "").lower().lstrip("."))


def config_for_language(language: str) -> LanguageConfig | None:
    return LANGUAGES.get(language or "")


def detect_language(file_name: str) -> str | None:
    _, dot, ext = (file_name or "").rpartition(".")
    if not dot:
        return None
    cfg = config_for_extension(ext)
    return cfg.name if cfg else None
