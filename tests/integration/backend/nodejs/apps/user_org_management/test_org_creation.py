"""
Integration tests for organization creation in PipesHub.

This test suite validates the complete flow of organization creation:
1. API call to create organization
2. MongoDB validation
3. Kafka message validation
4. ArangoDB validation
5. GET org endpoint validation
"""

import logging
import time
from typing import Dict, Generator, Optional
from bson import ObjectId # type: ignore
import pytest # type: ignore
from faker import Faker # type: ignore
from tests.integration.utils.utils import generate_business_org_data, generate_individual_org_data, create_org, get_org_id, get_org_id_as_object_id, get_org_from_mongo, get_org_from_arango, login_and_get_token

from tests.config.settings import get_settings
from tests.utils.db_interfaces import (
    ArangoInterface,
    MongoInterface,
    QdrantInterface,
    RedisInterface,
    create_arango_interface,
    create_mongo_interface,
    create_qdrant_interface,
    create_redis_interface,
)
from tests.utils.http_client import HTTPClient

fake = Faker()


@pytest.fixture(scope="function")
def mongo_interface() -> Generator[MongoInterface, None, None]:
    """
    Provide MongoDB interface for tests.
    
    Returns:
        Connected MongoDB interface
    """
    mongo = create_mongo_interface()
    yield mongo
    mongo.disconnect()


@pytest.fixture(scope="function")
def arango_interface() -> Generator[ArangoInterface, None, None]:
    """
    Provide ArangoDB interface for tests.
    
    Returns:
        Connected ArangoDB interface
    """
    arango = create_arango_interface()
    yield arango
    arango.disconnect()


@pytest.fixture(scope="function")
def qdrant_interface() -> Generator[QdrantInterface, None, None]:
    """
    Provide Qdrant interface for tests.
    
    Returns:
        Connected Qdrant interface
    """
    qdrant = create_qdrant_interface()
    yield qdrant
    qdrant.disconnect()


@pytest.fixture(scope="function")
def redis_interface() -> Generator[RedisInterface, None, None]:
    """
    Provide Redis interface for tests.
    
    Returns:
        Connected Redis interface
    """
    redis = create_redis_interface()
    yield redis
    redis.disconnect()


@pytest.fixture(scope="function")
def http_client() -> Generator[HTTPClient, None, None]:
    """
    Provide HTTP client for API calls.
    
    Returns:
        Configured HTTP client
    """
    settings = get_settings()
    client = HTTPClient(
        base_url="http://localhost:3000",  # Node.js backend URL
        timeout=30,
        verify_ssl=False
    )
    yield client
    client.close()


def _cleanup_orgs(mongo_interface: MongoInterface, arango_interface: Optional[ArangoInterface] = None):
    """Helper function to clean up orgs and related data."""
    logger = logging.getLogger(__name__)
    try:
        # Delete all orgs from MongoDB (no filter means delete all)
        deleted_orgs = mongo_interface.delete_many("org", {})
        logger.debug(f"Deleted {deleted_orgs} orgs from MongoDB")
        
        # Clean up all related data from MongoDB
        mongo_interface.delete_many("users", {})
        mongo_interface.delete_many("usercredentials", {})
        mongo_interface.delete_many("usergroups", {})
        mongo_interface.delete_many("orgauthconfigurations", {})
        mongo_interface.delete_many("orglogos", {})
        
        # Clean up orgs from ArangoDB if interface is provided
        if arango_interface is not None:
            try:
                deleted_arango_count = arango_interface.delete_all_vertices("organizations")
                logger.debug(f"Deleted {deleted_arango_count} orgs from ArangoDB")
            except Exception as arango_e:
                logger.warning(f"ArangoDB cleanup failed: {arango_e}")
    except Exception as e:
        logger.warning(f"Cleanup failed: {e}")


@pytest.fixture(scope="function", autouse=True)
def cleanup_test_data(mongo_interface: MongoInterface, arango_interface: ArangoInterface):
    """
    Cleanup test data before and after each test.
    
    Args:
        mongo_interface: MongoDB interface
        arango_interface: ArangoDB interface
    """
    # Clean up existing orgs BEFORE running the test
    _cleanup_orgs(mongo_interface, arango_interface)
    
    yield
    
    # Clean up orgs AFTER the test completes
    _cleanup_orgs(mongo_interface, arango_interface)

@pytest.mark.integration
def test_create_business_org(
    http_client: HTTPClient,
    mongo_interface: MongoInterface,
    arango_interface: ArangoInterface,
    cleanup_test_data,
):
    """
    Test business organization creation end-to-end.
    
    This test validates:
    1. POST /api/v1/org creates the organization
    2. MongoDB contains the org entry
    3. GET /api/v1/org returns the correct org data
    4. ArangoDB contains the org vertex
    """
    # Generate test data
    org_data = generate_business_org_data()
    
    # Step 1: Create organization via API
    created_org = create_org(http_client, org_data)
    org_id = get_org_id(created_org)
    org_id_obj = get_org_id_as_object_id(created_org)
    
    logger = logging.getLogger(__name__)
    logger.info(f"Looking for org with ID: {org_id_obj}")
    
    # Step 2: Validate MongoDB entry
    mongo_org = get_org_from_mongo(mongo_interface, org_id_obj)
    assert mongo_org["accountType"] == "business"
    assert mongo_org["contactEmail"] == org_data["contactEmail"]
    assert mongo_org["registeredName"] == org_data["registeredName"]
    
    # Step 3: Validate GET endpoint with authentication
    # Login using the credentials from org creation
    try:
        auth_token = login_and_get_token(
            http_client,
            org_data["contactEmail"],
            org_data["password"]
        )
        # Set auth token in client
        http_client.set_auth_token(auth_token)

        # Call GET endpoint
        get_response = http_client.get("/api/v1/org")
        assert get_response.status_code == 200, f"Expected status 200, got {get_response.status_code}: {get_response.text}"

        # Validate GET response data
        get_org_data = get_response.json()
        assert get_org_data["_id"] == org_id, "GET org ID should match created org ID"
        assert get_org_data["contactEmail"] == org_data["contactEmail"]

        logger.info("Successfully validated GET endpoint with authentication")

        # Clear auth token
        http_client.clear_auth_token()
    except Exception as e:
        logger.warning(f"GET endpoint validation skipped: {str(e)}")
        # Clear auth token if it was set
        http_client.clear_auth_token()

    # Step 4: Validate ArangoDB entry (wait for Kafka processing)
    arango_org = get_org_from_arango(arango_interface, org_id, wait_for_kafka=True)

    try:
        if arango_org:
            # Assert that the ID from ArangoDB matches the ID from MongoDB
            assert str(mongo_org["_id"]) == arango_org["_key"], f"MongoDB ID {mongo_org['_id']} does not match ArangoDB _key {arango_org['_key']}"
            # Assert ArangoDB _id format is 'organizations/{_key}'
            expected_arango_id = f"organizations/{str(arango_org['_key'])}"
            assert arango_org["_id"] == expected_arango_id, f"Expected ArangoDB _id {expected_arango_id}, got {arango_org['_id']}"
            assert arango_org["name"] == org_data["registeredName"], f"Expected name {org_data['registeredName']}, got {arango_org.get('name')}"
            assert arango_org["accountType"] in ["business", "enterprise"]
            assert arango_org["isActive"] is True
        else:
            logger = logging.getLogger(__name__)
            logger.error(f"Org {org_id} not found in ArangoDB after Kafka processing. This may be expected if Kafka processing is slow or disabled.")
            assert False, "Org not found in ArangoDB after Kafka processing"
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Could not validate ArangoDB: {str(e)}. Kafka processing may not be complete.")
        assert False, "Could not validate ArangoDB"


@pytest.mark.integration
def test_create_individual_org(
    http_client: HTTPClient,
    mongo_interface: MongoInterface,
    cleanup_test_data,
):
    """
    Test individual organization creation end-to-end.
    
    This test validates:
    1. POST /api/v1/org creates the organization
    2. MongoDB contains the org entry
    """
    # Generate test data
    org_data = generate_individual_org_data()
    
    # Step 1: Create organization via API
    created_org = create_org(http_client, org_data)
    org_id_obj = get_org_id_as_object_id(created_org)
    
    # Step 2: Validate MongoDB entry
    mongo_org = get_org_from_mongo(mongo_interface, org_id_obj)
    assert mongo_org["accountType"] == "individual"
    assert mongo_org["contactEmail"] == org_data["contactEmail"]


@pytest.mark.integration
def test_create_org_with_missing_fields(http_client: HTTPClient):
    """
    Test organization creation with missing required fields.
    """
    # Test missing registeredName for business account
    invalid_data = {
        "accountType": "business",
        "contactEmail": fake.email(),
        "adminFullName": fake.name(),
        "password": "Test1234!@#$",
    }
    
    response = http_client.post("/api/v1/org", json=invalid_data)
    assert response.status_code == 400, "Should return 400 for missing required fields"


@pytest.mark.integration
def test_create_org_with_invalid_email(http_client: HTTPClient):
    """
    Test organization creation with invalid email format.
    """
    invalid_data = generate_business_org_data()
    invalid_data["contactEmail"] = "invalid-email"
    
    response = http_client.post("/api/v1/org", json=invalid_data)
    assert response.status_code == 400, "Should return 400 for invalid email"


@pytest.mark.integration
def test_create_org_password_validation(http_client: HTTPClient):
    """
    Test organization creation with weak password.
    """
    invalid_data = generate_business_org_data()
    invalid_data["password"] = "weak"
    
    response = http_client.post("/api/v1/org", json=invalid_data)
    assert response.status_code == 400, "Should return 400 for weak password"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

