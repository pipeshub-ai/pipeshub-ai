"""Concrete CollectionStrategy implementations.

Importing this package registers every built-in strategy with
``CollectionStrategyFactory``. Enterprise Edition packages register
additional strategies the same way from their own import path.

``single`` is the default and what most deployments should stay on;
``per_connector_type`` is opt-in via ``VECTOR_COLLECTION_STRATEGY``.
"""

from app.services.vector_db.strategies.per_connector_type import (
    PerConnectorTypeStrategy,
)
from app.services.vector_db.strategies.single import SingleCollectionStrategy
from app.services.vector_db.strategy import CollectionStrategyFactory

CollectionStrategyFactory.register("single", SingleCollectionStrategy)
CollectionStrategyFactory.register("per_connector_type", PerConnectorTypeStrategy)

__all__ = ["PerConnectorTypeStrategy", "SingleCollectionStrategy"]
