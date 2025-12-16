"""
Swagger/OpenAPI configuration for FastAPI application.
This module provides configuration for serving Swagger UI documentation.
"""

from pathlib import Path
from typing import Any, Dict

import yaml
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi


def load_openapi_spec() -> Dict[str, Any]:
    """
    Load the OpenAPI specification from the YAML file.

    Returns:
        Dict containing the OpenAPI specification
    """
    yaml_path = Path(__file__).parent / "openapi.yaml"

    if not yaml_path.exists():
        raise FileNotFoundError(f"OpenAPI spec not found at {yaml_path}")

    with open(yaml_path, 'r', encoding='utf-8') as f:
        spec = yaml.safe_load(f)

    return spec


def custom_openapi(app: FastAPI) -> Dict[str, Any]:
    """
    Custom OpenAPI schema generator that loads from our YAML file.

    Args:
        app: FastAPI application instance

    Returns:
        Dict containing the OpenAPI specification
    """
    if app.openapi_schema:
        return app.openapi_schema

    try:
        # Load our custom OpenAPI spec
        openapi_schema = load_openapi_spec()

        # Store it in the app
        app.openapi_schema = openapi_schema
        return app.openapi_schema
    except Exception as e:
        # Fallback to auto-generated schema if custom spec fails to load
        print(f"Warning: Failed to load custom OpenAPI spec: {e}")
        print("Falling back to auto-generated schema")

        openapi_schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        app.openapi_schema = openapi_schema
        return app.openapi_schema


def configure_swagger(app: FastAPI) -> None:
    """
    Configure Swagger UI for the FastAPI application.

    Args:
        app: FastAPI application instance
    """
    # Set custom OpenAPI schema
    app.openapi = lambda: custom_openapi(app)

    # Update app metadata
    app.title = "PipesHub Connector Service API"
    app.version = "1.0.0"
    app.description = """
    RESTful API for the PipesHub Connector Service - providing knowledge base management,
    connector integrations (Google Drive, Gmail, OneDrive, Confluence, Jira, Slack, SharePoint),
    record management, and data synchronization capabilities.
    """

    # Swagger UI will be available at /docs
    # ReDoc will be available at /redoc
    print("✅ Swagger UI configured successfully")
    print("📚 Documentation available at:")
    print("   - Swagger UI: http://localhost:8088/docs")
    print("   - ReDoc: http://localhost:8088/redoc")
    print("   - OpenAPI JSON: http://localhost:8088/openapi.json")

