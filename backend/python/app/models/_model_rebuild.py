"""
Model rebuild utility to resolve forward references after all models are defined.

This module handles rebuilding Pydantic models to resolve forward references
that occur due to circular imports between entities.py and blocks.py.

Import this module after all model definitions are complete to trigger rebuilds.
"""
from typing import List, Type

from pydantic import BaseModel


def get_all_subclasses(cls: Type[BaseModel]) -> List[Type[BaseModel]]:
    """Recursively get all subclasses of a class."""
    subclasses = []
    for subclass in cls.__subclasses__():
        subclasses.append(subclass)
        subclasses.extend(get_all_subclasses(subclass))
    return subclasses


def rebuild_all_models() -> None:
    """
    Rebuild all Pydantic models to resolve forward references after all imports are complete.
    This is necessary due to circular imports between entities.py and blocks.py.

    This function automatically discovers all Record subclasses and rebuilds them,
    along with blocks models that have forward references. New Record subclasses
    are automatically included without manual updates.

    Call this function after all model imports are complete.
    """
    # Import here to avoid circular imports - models must be fully defined first
    from app.models.blocks import (
        BlockGroup,
        BlocksContainer,
        ChildRecord,
        TableRowMetadata,
    )
    from app.models.entities import Record

    # Automatically discover all Record subclasses (including nested subclasses)
    record_models = [Record] + get_all_subclasses(Record)

    # Collect blocks models that have forward references
    # These are explicitly listed since they're imported from blocks.py
    blocks_models = [
        ChildRecord,
        TableRowMetadata,
        BlockGroup,
        BlocksContainer,
    ]

    # Rebuild all models
    all_models = record_models + blocks_models
    for model in all_models:
        model.model_rebuild()

    # Log for debugging (optional)
    # print(f"Rebuilt {len(all_models)} models: {[m.__name__ for m in all_models]}")
