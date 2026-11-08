"""Config-store paths and limits for API Toolsets.

Layout, one key per record so concurrent admin writes never race on a shared list:

  /services/api-toolsets/instances/{instanceId}                 -> instance record (no secrets)
  /services/api-toolsets/credentials/{instanceId}/{ownerId}     -> a user's or the shared credential
  /services/api-toolsets/oauth-clients/{instanceId}             -> the admin's OAuth app (authorization code)
  /services/api-toolsets/oauth-states/{sha256(state)}           -> a sign-in in progress
  /services/api-toolsets/oauth-state-claims/{sha256(state)}     -> single-use claim of that sign-in
"""
import hashlib

API_TOOLSETS_ROOT = "/services/api-toolsets"

# The owner id of the one credential every user shares when an admin turns that on.
SHARED_CREDENTIAL_OWNER = "_shared"
SIGN_IN_STATE_TTL_SECONDS = 600
MAX_OPERATIONS_PER_TOOLSET = 200
# Instance records hold every selected operation's schema; etcd refuses values over 1.5 MB.
MAX_INSTANCE_RECORD_BYTES = 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 120
PRIVATE_NETWORK_ENV = "API_TOOLSETS_ALLOW_PRIVATE_NETWORK_URLS"
OAUTH_CALLBACK_PATH = "/api-toolsets/oauth/callback/"


def get_instances_prefix() -> str:
    return f"{API_TOOLSETS_ROOT}/instances/"


def get_instance_path(instance_id: str) -> str:
    return f"{API_TOOLSETS_ROOT}/instances/{instance_id}"


def get_credentials_prefix(instance_id: str) -> str:
    return f"{API_TOOLSETS_ROOT}/credentials/{instance_id}/"


def get_credentials_path(instance_id: str, owner_id: str) -> str:
    return f"{API_TOOLSETS_ROOT}/credentials/{instance_id}/{owner_id}"


def get_oauth_client_path(instance_id: str) -> str:
    return f"{API_TOOLSETS_ROOT}/oauth-clients/{instance_id}"


def _state_key(state: str) -> str:
    # Hashed so the raw state, which grants the sign-in, is never a readable store key.
    return hashlib.sha256(state.encode()).hexdigest()


def get_oauth_state_path(state: str) -> str:
    return f"{API_TOOLSETS_ROOT}/oauth-states/{_state_key(state)}"


def get_oauth_state_claim_path(state: str) -> str:
    return f"{API_TOOLSETS_ROOT}/oauth-state-claims/{_state_key(state)}"
