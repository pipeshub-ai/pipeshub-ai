"""
Test configurations and fixtures for PipesHub AI integration tests.
"""
from typing import Dict, Any, List


# Test environment configurations
TEST_ENVIRONMENTS = {
    "minimal": {
        "containers": ["redis", "mongodb", "arangodb", "qdrant"],
        "services": ["query_service", "indexing_service"],
        "timeout": 30,
        "retry_attempts": 3,
        "retry_delay": 2
    },
    "full": {
        "containers": ["redis", "mongodb", "arangodb", "qdrant", "etcd", "kafka", "zookeeper"],
        "services": ["nodejs_backend", "query_service", "indexing_service", "connector_service", "docling_service"],
        "timeout": 60,
        "retry_attempts": 5,
        "retry_delay": 3
    },
    "development": {
        "containers": ["redis", "mongodb", "arangodb", "qdrant"],
        "services": ["nodejs_backend", "query_service", "indexing_service", "connector_service"],
        "timeout": 45,
        "retry_attempts": 4,
        "retry_delay": 2
    }
}

# Service configurations
SERVICE_CONFIGS = {
    "nodejs_backend": {
        "url": "http://localhost:3000",
        "health_endpoint": "/api/v1/health",
        "timeout": 30,
        "required": True
    },
    "query_service": {
        "url": "http://localhost:8000",
        "health_endpoint": "/health",
        "timeout": 30,
        "required": True
    },
    "indexing_service": {
        "url": "http://localhost:8091",
        "health_endpoint": "/health",
        "timeout": 30,
        "required": True
    },
    "connector_service": {
        "url": "http://localhost:8088",
        "health_endpoint": "/health",
        "timeout": 30,
        "required": True
    },
    "docling_service": {
        "url": "http://localhost:8092",
        "health_endpoint": "/health",
        "timeout": 30,
        "required": False
    }
}

# Database configurations
DATABASE_CONFIGS = {
    "mongodb": {
        "host": "localhost",
        "port": 27017,
        "username": "admin",
        "password": "password",
        "database": "enterprise-search",
        "connection_string": "mongodb://admin:password@localhost:27017/"
    },
    "arangodb": {
        "host": "localhost",
        "port": 8529,
        "username": "root",
        "password": "test_password",
        "database": "es",
        "connection_string": "http://localhost:8529"
    },
    "redis": {
        "host": "localhost",
        "port": 6379,
        "password": "",
        "database": 0,
        "connection_string": "redis://localhost:6379"
    },
    "qdrant": {
        "host": "localhost",
        "port": 6333,
        "api_key": "test_qdrant_key",
        "connection_string": "http://localhost:6333"
    }
}

# Test data configurations
TEST_DATA_CONFIGS = {
    "sample_documents": [
        {
            "name": "sample.txt",
            "content": "PipesHub AI Integration Test Document",
            "type": "text",
            "metadata": {
                "author": "Test Suite",
                "created_at": "2024-01-01T00:00:00Z",
                "source": "test",
                "category": "documentation"
            }
        },
        {
            "name": "technical_guide.md",
            "content": "# Technical Guide\n\nThis is a markdown document for testing.",
            "type": "markdown",
            "metadata": {
                "author": "Test Suite",
                "created_at": "2024-01-01T00:00:00Z",
                "source": "test",
                "category": "guide"
            }
        },
        {
            "name": "data.json",
            "content": '{"name": "Test Data", "value": 123, "active": true}',
            "type": "json",
            "metadata": {
                "author": "Test Suite",
                "created_at": "2024-01-01T00:00:00Z",
                "source": "test",
                "category": "data"
            }
        }
    ],
    "search_queries": [
        "test document",
        "integration testing",
        "PipesHub AI",
        "search functionality",
        "artificial intelligence",
        "machine learning",
        "document processing",
        "enterprise software",
        "workplace automation",
        "natural language processing"
    ],
    "test_filters": [
        {"org_id": "test-org-456"},
        {"user_id": "test-user-123"},
        {"document_type": "text"},
        {"source": "test"},
        {"category": "documentation"},
        {"author": "Test Suite"}
    ]
}

# Performance test configurations
PERFORMANCE_CONFIGS = {
    "response_time_limits": {
        "health_check": 5.0,
        "search_query": 10.0,
        "document_upload": 30.0,
        "document_indexing": 60.0
    },
    "load_test": {
        "concurrent_requests": 10,
        "duration_seconds": 30,
        "ramp_up_seconds": 5
    },
    "memory_limits": {
        "max_document_size": 10 * 1024 * 1024,  # 10MB
        "max_query_length": 10000,
        "max_results": 1000
    }
}

# Error test configurations
ERROR_TEST_CONFIGS = {
    "invalid_inputs": [
        {"query": "", "expected_status": 400},
        {"query": None, "expected_status": 400},
        {"limit": -1, "expected_status": 400},
        {"limit": 10000, "expected_status": 400},
        {"filters": "invalid", "expected_status": 400}
    ],
    "malformed_requests": [
        {"invalid_field": "value"},
        {"query": "test", "limit": "invalid"},
        {"query": "test", "filters": "invalid"}
    ],
    "timeout_scenarios": [
        {"timeout": 0.1, "expected_behavior": "timeout"},
        {"timeout": 1.0, "expected_behavior": "timeout_or_success"}
    ]
}

# Authentication configurations
AUTH_CONFIGS = {
    "test_user": {
        "user_id": "test-user-123",
        "org_id": "test-org-456",
        "email": "test@example.com",
        "role": "admin",
        "permissions": ["read", "write", "admin"]
    },
    "test_org": {
        "org_id": "test-org-456",
        "name": "Test Organization",
        "domain": "test.example.com",
        "settings": {
            "max_users": 100,
            "max_documents": 10000,
            "features": ["search", "indexing", "connectors"]
        }
    }
}

# Test execution configurations
EXECUTION_CONFIGS = {
    "test_phases": [
        "infrastructure_setup",
        "service_health_checks",
        "basic_functionality",
        "integration_testing",
        "performance_testing",
        "error_handling"
    ],
    "test_categories": [
        "health",
        "indexing",
        "search",
        "integration",
        "performance",
        "error_handling"
    ],
    "test_priorities": {
        "P0": ["health", "basic_indexing", "basic_search"],
        "P1": ["advanced_indexing", "advanced_search", "authentication"],
        "P2": ["performance", "load_testing", "security"]
    }
}

# Logging configurations
LOGGING_CONFIGS = {
    "log_levels": ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    "log_formats": {
        "console": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        "file": "%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s"
    },
    "log_files": {
        "test_results": "test_results.log",
        "service_logs": "service_logs.log",
        "error_logs": "error_logs.log"
    }
}

# Environment variable mappings
ENV_VAR_MAPPINGS = {
    "NODEJS_BACKEND_URL": "http://localhost:3000",
    "QUERY_SERVICE_URL": "http://localhost:8000",
    "INDEXING_SERVICE_URL": "http://localhost:8091",
    "CONNECTOR_SERVICE_URL": "http://localhost:8088",
    "DOCLING_SERVICE_URL": "http://localhost:8092",
    "MONGODB_URI": "mongodb://admin:password@localhost:27017/",
    "ARANGO_URL": "http://localhost:8529",
    "REDIS_URL": "redis://localhost:6379",
    "QDRANT_HOST": "localhost",
    "QDRANT_PORT": "6333"
}

def get_test_config(environment: str = "minimal") -> Dict[str, Any]:
    """Get test configuration for specified environment."""
    if environment not in TEST_ENVIRONMENTS:
        raise ValueError(f"Unknown environment: {environment}")
    
    return TEST_ENVIRONMENTS[environment]

def get_service_config(service_name: str) -> Dict[str, Any]:
    """Get configuration for specified service."""
    if service_name not in SERVICE_CONFIGS:
        raise ValueError(f"Unknown service: {service_name}")
    
    return SERVICE_CONFIGS[service_name]

def get_database_config(database_name: str) -> Dict[str, Any]:
    """Get configuration for specified database."""
    if database_name not in DATABASE_CONFIGS:
        raise ValueError(f"Unknown database: {database_name}")
    
    return DATABASE_CONFIGS[database_name]

def get_test_data(data_type: str) -> List[Dict[str, Any]]:
    """Get test data for specified type."""
    if data_type not in TEST_DATA_CONFIGS:
        raise ValueError(f"Unknown data type: {data_type}")
    
    return TEST_DATA_CONFIGS[data_type]

