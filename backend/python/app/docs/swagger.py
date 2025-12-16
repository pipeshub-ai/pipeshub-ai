"""
Query Service Swagger Configuration

This module provides Swagger/OpenAPI documentation configuration
for the Query Service API.
"""

from pathlib import Path
from typing import Dict, Any

from fastapi import FastAPI
import yaml


# Path to the swagger YAML file
SWAGGER_YAML_PATH = Path(__file__).parent / "swagger.yaml"


def load_swagger_spec() -> Dict[str, Any]:
    """
    Load the Swagger specification from YAML file.
    
    Returns:
        Dict containing the OpenAPI specification
    """
    with open(SWAGGER_YAML_PATH, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def get_custom_openapi(app: FastAPI) -> Dict[str, Any]:
    """
    Generate custom OpenAPI schema for the Query Service.
    
    This function loads the OpenAPI spec entirely from the swagger.yaml file,
    using ONLY the paths defined there (no auto-generated paths).
    
    Args:
        app: FastAPI application instance
        
    Returns:
        Dict containing the complete OpenAPI specification
    """
    if app.openapi_schema:
        return app.openapi_schema
    
    try:
        # Load custom swagger spec - use it directly without merging
        swagger_spec = load_swagger_spec()
        
        # Build OpenAPI schema using ONLY the custom swagger.yaml content
        openapi_schema = {
            "openapi": swagger_spec.get("openapi", "3.0.0"),
            "info": swagger_spec.get("info", {
                "title": "Query Service API",
                "version": "1.0.0"
            }),
        }
        
        # Add optional sections from swagger.yaml
        if 'servers' in swagger_spec:
            openapi_schema['servers'] = swagger_spec['servers']
        
        if 'tags' in swagger_spec:
            openapi_schema['tags'] = swagger_spec['tags']
        
        if 'components' in swagger_spec:
            openapi_schema['components'] = swagger_spec['components']
        
        if 'security' in swagger_spec:
            openapi_schema['security'] = swagger_spec['security']
        
        # Use ONLY paths from swagger.yaml (no auto-generated paths)
        if 'paths' in swagger_spec:
            openapi_schema['paths'] = swagger_spec['paths']
        else:
            openapi_schema['paths'] = {}
        
        app.openapi_schema = openapi_schema
        return app.openapi_schema
        
    except Exception as e:
        print(f"Error loading custom Swagger spec: {e}")
        # Fallback to minimal schema
        return {
            "openapi": "3.0.0",
            "info": {
                "title": "Query Service API",
                "version": "1.0.0",
                "description": "API for retrieval and query service"
            },
            "paths": {}
        }


def configure_swagger_ui(app: FastAPI) -> None:
    """
    Configure Swagger UI for the Query Service.
    
    This sets up custom OpenAPI schema and configures the
    Swagger UI and ReDoc documentation interfaces.
    
    Args:
        app: FastAPI application instance
    """
    # Set custom OpenAPI schema generator
    app.openapi = lambda: get_custom_openapi(app)

# Swagger configuration metadata
SWAGGER_CONFIG = {
    "module_id": "query",
    "tag_name": "Query Service",
    "tag_description": "Query service operations for semantic search, AI chat, and intelligent agents",
    "yaml_file_path": str(SWAGGER_YAML_PATH),
    "base_url": "/api/v1",
    "docs_url": "/docs",
    "redoc_url": "/redoc",
    "openapi_url": "/openapi.json",
}

def get_swagger_config() -> Dict[str, Any]:
    """
    Get the Swagger configuration metadata.
    
    Returns:
        Dict containing Swagger configuration
    """
    return SWAGGER_CONFIG


def register_query_swagger(app: FastAPI) -> None:
    """
    Register Query Service Swagger documentation.
    
    This function should be called during application startup
    to configure the Swagger/OpenAPI documentation.
    
    Args:
        app: FastAPI application instance
        
    Example:
        >>> from fastapi import FastAPI
        >>> from app.docs.swagger import register_query_swagger
        >>> 
        >>> app = FastAPI()
        >>> register_query_swagger(app)
    """
    configure_swagger_ui(app)
    print(f"✅ Query Service Swagger documentation configured")
    print(f"   📄 Docs UI: {SWAGGER_CONFIG['docs_url']}")
    print(f"   📖 ReDoc: {SWAGGER_CONFIG['redoc_url']}")
    print(f"   🔧 OpenAPI JSON: {SWAGGER_CONFIG['openapi_url']}")


# Utility function to validate swagger spec
def validate_swagger_spec() -> bool:
    """
    Validate the swagger.yaml file.
    
    Returns:
        bool: True if valid, False otherwise
    """
    try:
        spec = load_swagger_spec()
        
        # Check required fields
        required_fields = ['openapi', 'info', 'paths']
        for field in required_fields:
            if field not in spec:
                print(f"❌ Missing required field: {field}")
                return False
        
        # Check info section
        info = spec.get('info', {})
        info_required = ['title', 'version']
        for field in info_required:
            if field not in info:
                print(f"❌ Missing required info field: {field}")
                return False
        
        print(f"✅ Swagger spec is valid")
        print(f"   Title: {info.get('title')}")
        print(f"   Version: {info.get('version')}")
        print(f"   Paths: {len(spec.get('paths', {}))}")
        print(f"   Schemas: {len(spec.get('components', {}).get('schemas', {}))}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error validating swagger spec: {e}")
        return False


if __name__ == "__main__":
    # Validate swagger spec when run directly
    validate_swagger_spec()

