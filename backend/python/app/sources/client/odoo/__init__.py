"""Odoo client package."""
from app.sources.client.odoo.odoo import (
    OdooClient,
    OdooClientBuilder,
    OdooConfig,
)

__all__ = [
    "OdooClient",
    "OdooClientBuilder",
    "OdooConfig",
]
