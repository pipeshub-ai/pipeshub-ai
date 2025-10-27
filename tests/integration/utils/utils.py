from typing import Dict
from faker import Faker # type: ignore
from tests.utils.db_interfaces import MongoInterface, ArangoInterface
from tests.utils.http_client import HTTPClient
from bson import ObjectId # type: ignore
from typing import Optional
import time
import logging

fake = Faker()
logger = logging.getLogger(__name__)

def generate_business_org_data() -> Dict:
    """
    Generate test data for business organization creation.
    Returns:
        Dictionary with org creation data
    """
    return {
        "accountType": "business",
        "registeredName": fake.company(),
        "shortName": fake.company_suffix().upper(),
        "contactEmail": fake.company_email(),
        "adminFullName": fake.name(),
        "password": "Test1234!@#$",
        "sendEmail": False,
        "permanentAddress": {
            "addressLine1": fake.street_address(),
            "city": fake.city(),
            "state": fake.state(),
            "country": fake.country(),
            "postCode": fake.zipcode(),
        },
    }


def generate_individual_org_data() -> Dict:
    """
    Generate test data for individual organization creation.
    Returns:
        Dictionary with org creation data
    """
    return {
        "accountType": "individual",
        "contactEmail": fake.email(),
        "adminFullName": fake.name(),
        "password": "Test1234!@#$",
        "sendEmail": False,
    }


def create_org(http_client: HTTPClient, org_data: Dict) -> Dict:
    """
    Create an organization via API.
    Args:
        http_client: HTTP client for API calls
        org_data: Organization data to create
    Returns:
        Created organization data from API response
        
    Raises:
        AssertionError: If organization creation fails
    """
    response = http_client.post("/api/v1/org", json=org_data)
    assert response.status_code == 200, f"Expected status 200, got {response.status_code}: {response.text}"
    created_org = response.json()
    assert "contactEmail" in created_org
    assert created_org["accountType"] == org_data["accountType"]
    if org_data["accountType"] == "business":
        assert "registeredName" in created_org
    return created_org


def get_org_id(created_org: Dict) -> str:
    """
    Extract organization ID from created org response.
    Args:
        created_org: Organization data from API response
    Returns:
        Organization ID as string
    """
    org_id = created_org.get("_id") or created_org.get("id")
    assert org_id is not None, "Organization ID should be returned"
    return str(org_id)


def get_org_id_as_object_id(created_org: Dict) -> ObjectId:
    """
    Extract organization ID and convert to ObjectId.
    Args:
        created_org: Organization data from API response
    Returns:
        Organization ID as ObjectId
    """
    org_id = created_org.get("_id") or created_org.get("id")
    assert org_id is not None, "Organization ID should be returned"
    
    if isinstance(org_id, str):
        return ObjectId(org_id)
    return org_id


def get_org_from_mongo(mongo_interface: MongoInterface, org_id: ObjectId) -> Dict:
    """
    Get organization from MongoDB.
    Args:
        mongo_interface: MongoDB interface
        org_id: Organization ID as ObjectId
        
    Returns:
        Organization data from MongoDB
        
    Raises:
        AssertionError: If organization not found
    """
    mongo_org = mongo_interface.find_one("org", {"_id": org_id})
    assert mongo_org is not None, f"Organization {org_id} should exist in MongoDB"
    return mongo_org


def wait_for_arango_org(arango_interface: ArangoInterface, org_id: str, max_attempts: int = 5, delay: int = 1) -> Optional[Dict]:
    """
    Wait for organization to appear in ArangoDB after Kafka processing.
    Args:
        arango_interface: ArangoDB interface
        org_id: Organization ID as string
        max_attempts: Maximum number of attempts to check
        delay: Delay between attempts in seconds
        
    Returns:
        Organization data from ArangoDB, or None if not found
    """
    for attempt in range(max_attempts):
        try:
            arango_org = arango_interface.get_vertex("organizations", org_id)
            if arango_org:
                return arango_org
        except Exception:
            # Collection might not exist yet, wait and retry
            pass
        time.sleep(delay)
    return None


def get_org_from_arango(arango_interface: ArangoInterface, org_id: str, wait_for_kafka: bool = True) -> Optional[Dict]:
    """
    Get organization from ArangoDB.

    Args:
        arango_interface: ArangoDB interface
        org_id: Organization ID as string
        wait_for_kafka: Whether to wait for Kafka processing
        
    Returns:
        Organization data from ArangoDB, or None if not found
    """
    if wait_for_kafka:
        # Wait for Kafka to process
        time.sleep(20)
        return wait_for_arango_org(arango_interface, org_id)
    else:
        try:
            return arango_interface.get_vertex("organizations", org_id)
        except Exception:
            return None


def init_auth(http_client: HTTPClient, email: str, bearer_token: Optional[str] = None) -> Optional[str]:
    """
    Initialize authentication by calling initAuth endpoint.

    Args:
        http_client: HTTP client for API calls
        email: User email address
        bearer_token: Optional Bearer token for authorization

    Returns:
        Session token from response header
    Raises:
        AssertionError: If initAuth fails
    """
    headers = {"Content-Type": "application/json"}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    response = http_client.post(
        "/api/v1/userAccount/initAuth",
        json={"email": email},
        headers=headers
    )

    assert response.status_code == 200, f"Expected status 200, got {response.status_code}: {response.text}"
    # Session token is in response header
    session_token = response.headers.get("x-session-token")
    assert session_token is not None, "Session token should be returned from initAuth in x-session-token header"

    logger.debug(f"Initialized auth for email {email}")
    return session_token


def authenticate(http_client: HTTPClient, session_token: str, password: str) -> Dict:
    """
    Authenticate user with password using the session token.

    Args:
        http_client: HTTP client for座谈会 API calls
        session_token: Session token from initAuth
        password: User password

    Returns:
        Authentication response data containing token

    Raises:
        AssertionError: If authentication fails
    """
    headers = {
        "X-Session-Token": session_token,
        "Content-Type": "application/json"
    }

    response = http_client.post(
        "/api/v1/userAccount/authenticate",
        json={
            "method": "password",
            "credentials": {
                "password": password
            }
        },
        headers=headers
    )

    assert response.status_code == 200, f"Expected status 200, got {response.status_code}: {response.text}"
    data = response.json()

    logger.debug("Successfully authenticated user")
    return data


def login_and_get_token(http_client: HTTPClient, email: str, password: str, bearer_token: Optional[str] = None) -> str:
    """
    Complete login flow: initAuth + authenticate.
    Args:
        http_client: HTTP client for API calls
        email: User email address
        password: User password
        bearer_token: Optional Bearer token
    Returns:
        Authentication token for subsequent requests
    Raises:
        AssertionError: If login fails
    """
    # Step 1: Initialize auth
    session_token = init_auth(http_client, email, bearer_token)

    # Step 2: Authenticate with password
    auth_data = authenticate(http_client, session_token, password)

    # Extract token from response
    token = auth_data.get("token") or auth_data.get("accessToken")
    assert token is not None, "Token should be returned from authenticate"

    logger.debug(f"Successfully logged in user {email}")
    return token
