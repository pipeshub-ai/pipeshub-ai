"""Unit tests for app.agents.constants.mcp_server_constants."""
from app.agents.constants.mcp_server_constants import (
    get_mcp_credentials_path,
    get_mcp_dcr_client_path,
    get_mcp_instance_credentials_prefix,
    get_mcp_instance_path,
    get_mcp_instances_prefix,
    get_mcp_oauth_client_config_path,
    get_mcp_oauth_state_path,
    get_mcp_oauth_states_prefix,
    get_mcp_oauth_tokens_path,
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
    def test_state_path_keyed_by_state(self) -> None:
        assert get_mcp_oauth_state_path("abc123") == "/services/mcp/oauth-states/abc123"

    def test_states_prefix(self) -> None:
        assert get_mcp_oauth_states_prefix() == "/services/mcp/oauth-states/"

    def test_state_path_nested_under_prefix(self) -> None:
        prefix = get_mcp_oauth_states_prefix()
        path = get_mcp_oauth_state_path("xyz")
        assert path.startswith(prefix)
