"""Single flip file for all OSS↔EE container switches.

Flip OSS vs EE imports here together with ``app.edition_config`` and
``app.edition_services``.  Kept separate so API-layer config (edition_config)
stays free of service-specific DI containers.

EE: uncomment the EE lines and comment out the matching OSS lines.
"""

# OSS
from app.containers.query import QueryAppContainer
from app.containers.connector import ConnectorAppContainer
from app.containers.indexing import IndexingAppContainer

# EE — uncomment all EE lines together (and comment out the matching OSS lines)
# from app.ee.containers.query import QueryAppContainer
# from app.ee.containers.connector import ConnectorAppContainer
# from app.ee.containers.indexing import IndexingAppContainer

__all__ = ["QueryAppContainer", "ConnectorAppContainer", "IndexingAppContainer"]
