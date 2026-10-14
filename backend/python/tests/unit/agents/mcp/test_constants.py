"""Unit tests for app.agents.constants.mcp_server_constants."""
import hashlib

from app.agents.constants.mcp_server_constants import (
    get_mcp_credentials_path,
    get_mcp_dcr_client_path,
    get_mcp_instance_credentials_prefix,
    get_mcp_instance_path,
    get_mcp_instances_prefix,
    get_mcp_oauth_client_config_path,
    get_mcp_oauth_state_claim_path,
    get_mcp_oauth_state_path,
    get_mcp_oauth_states_prefix,
    get_mcp_oauth_tokens_path,
    get_mcp_unhashed_oauth_state_path,
    normalize_mcp_type,
)


class TestNormalizeMcpType:
    def test_lowercases_and_strips(self) -> None:
        assert normalize_mcp_type("  Brave Search  ") == "brave_search"

    def test_replaces_hyphens(self) -> None:
        assert normalize_mcp_type("google-drive") == "google_drive"

    def test_already_normalized(self) -> None:
        assert normalize_mcp_type("jira") == "jira"


class TestInstancePaths:
    def test_instances_prefix(self) -> None:
        assert get_mcp_instances_prefix() == "/services/mcp/instances/"

    def test_instance_path(self) -> None:
        assert get_mcp_instance_path("inst-1") == "/services/mcp/instances/inst-1"

    def test_instance_path_is_nested_under_prefix(self) -> None:
        prefix = get_mcp_instances_prefix()
        path = get_mcp_instance_path("inst-1")
        assert path.startswith(prefix)


class TestCredentialPaths:
    def test_credentials_path(self) -> None:
        assert get_mcp_credentials_path("inst-1", "user-1") == "/services/mcp/credentials/inst-1/user-1"

    def test_instance_credentials_prefix(self) -> None:
        assert get_mcp_instance_credentials_prefix("inst-1") == "/services/mcp/credentials/inst-1/"

    def test_oauth_tokens_path_nested_under_credentials(self) -> None:
        cred_path = get_mcp_credentials_path("inst-1", "user-1")
        tokens_path = get_mcp_oauth_tokens_path("inst-1", "user-1")
        assert tokens_path == f"{cred_path}/oauth-tokens"

    def test_dcr_client_path_nested_under_credentials(self) -> None:
        cred_path = get_mcp_credentials_path("inst-1", "user-1")
        dcr_path = get_mcp_dcr_client_path("inst-1", "user-1")
        assert dcr_path == f"{cred_path}/dcr-client"


class TestOAuthClientConfigPath:
    def test_path(self) -> None:
        assert get_mcp_oauth_client_config_path("inst-1") == "/services/mcp/oauth-clients/inst-1"


class TestOAuthStatePaths:
    def test_state_path_keyed_by_a_hash_of_the_state(self) -> None:
        digest = hashlib.sha256(b"abc123").hexdigest()
        assert get_mcp_oauth_state_path("abc123") == f"/services/mcp/oauth-states/{digest}"

    def test_neither_key_carries_the_state_itself(self) -> None:
        # Keys are logged by the store and visible to anyone who can list it.
        state = "Zx9-very-secret-state"
        assert state not in get_mcp_oauth_state_path(state)
        assert state not in get_mcp_oauth_state_claim_path(state)

    def test_the_claim_is_keyed_by_the_same_hash(self) -> None:
        digest = hashlib.sha256(b"abc123").hexdigest()
        assert get_mcp_oauth_state_claim_path("abc123") == f"/services/mcp/oauth-state-claims/{digest}"

    def test_the_unhashed_path_is_where_states_used_to_be(self) -> None:
        assert get_mcp_unhashed_oauth_state_path("abc123") == "/services/mcp/oauth-states/abc123"

    def test_states_prefix(self) -> None:
        assert get_mcp_oauth_states_prefix() == "/services/mcp/oauth-states/"

    def test_state_path_nested_under_prefix(self) -> None:
        prefix = get_mcp_oauth_states_prefix()
        path = get_mcp_oauth_state_path("xyz")
        assert path.startswith(prefix)
