"""The Node upload table is the registry's, as generated."""
from __future__ import annotations

from app.config import file_types


def test_the_generated_node_file_is_what_the_registry_renders() -> None:
    assert file_types.NODE_GENERATED_PATH.read_text() == file_types.render_node(), (
        "run `python -m app.config.file_types --emit node` from backend/python"
    )


def test_the_content_hash_covers_the_tables() -> None:
    tables = file_types.node_tables()
    changed = {**tables, "UPLOAD_EXTENSION_TO_MIME": {**tables["UPLOAD_EXTENSION_TO_MIME"], "x": "y"}}  # type: ignore[dict-item]
    assert file_types.content_sha256(changed) != file_types.content_sha256(tables)
    assert f"  '{file_types.content_sha256(tables)}';" in file_types.render_node()
