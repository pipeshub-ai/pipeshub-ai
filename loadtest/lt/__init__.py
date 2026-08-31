"""Connector-service load harness.

Measures how fast the connector service turns a source into graph records, and
what that costs the API, with indexing switched off so the numbers describe the
connector service alone.
"""

__all__ = ["cli"]
