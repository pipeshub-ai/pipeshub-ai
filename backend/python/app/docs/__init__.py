"""
Query Service Documentation Module

This module provides Swagger/OpenAPI documentation configuration
and utilities for the Query Service API.
"""

from .swagger import (
    configure_swagger_ui,
    get_custom_openapi,
    get_swagger_config,
    load_swagger_spec,
    register_query_swagger,
    validate_swagger_spec,
    SWAGGER_CONFIG,
    SWAGGER_YAML_PATH,
)

__all__ = [
    'configure_swagger_ui',
    'get_custom_openapi',
    'get_swagger_config',
    'load_swagger_spec',
    'register_query_swagger',
    'validate_swagger_spec',
    'SWAGGER_CONFIG',
    'SWAGGER_YAML_PATH',
]

