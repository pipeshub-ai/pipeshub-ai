"""The entity index is built only where it is written or searched: the indexing and the query services."""

import ast
from pathlib import Path

from app.containers import indexing as indexing_module

CONTAINERS = Path(indexing_module.__file__).parent


def _builders(path: Path) -> bool:
    tree = ast.parse(path.read_text())
    return any(
        isinstance(node, ast.Attribute) and node.attr == "create_entity_vector_store"
        for node in ast.walk(tree)
    )


def test_only_the_indexing_and_the_query_containers_build_the_entity_store() -> None:
    builders = {p.name for p in CONTAINERS.glob("*.py") if _builders(p)}
    assert builders == {"indexing.py", "query.py"}


def test_the_other_services_have_no_entity_store_provider() -> None:
    from app.containers.connector import ConnectorAppContainer
    from app.containers.docling import DoclingAppContainer
    from app.containers.extraction import ExtractionAppContainer
    from app.containers.parsing import ParsingAppContainer

    for container in (ConnectorAppContainer, DoclingAppContainer, ExtractionAppContainer, ParsingAppContainer):
        assert not hasattr(container, "entity_vector_store"), container.__name__
