"""
Box connector module for Pipeshub AI.

This module provides integration with Box cloud storage platform,
allowing synchronization of files, folders, users, and groups.
"""

from app.connectors.sources.box.common.apps import BoxApp
from app.connectors.sources.box.connector import BoxConnector

__all__ = ['BoxConnector', 'BoxApp']
