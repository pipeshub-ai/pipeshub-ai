from app.agents.registry.toolset_registry import get_toolset_registry
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthConfig,
    OAuthScopeConfig,
)
from app.connectors.core.registry.connector_builder import (
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
)
from app.connectors.core.registry.oauth_config_registry import get_oauth_config_registry
from app.connectors.core.registry.tool_builder import (
    ToolCategory,
    ToolDefinition,
    ToolsetBuilder,
    ToolsetCommonFields,
)
from app.connectors.core.registry.types import AuthField, DocumentationLink

# ============================================================================
# Example 1: Connector with Multiple Authentication Types (Uniform API)
# ============================================================================

# Now use it in the connector builder - uniform and clean auth configuration
# OAuth config is created and registered within the builder automatically
@ConnectorBuilder("ExampleConnector")\
    .in_group("Example Group")\
    .with_description("Example connector with multiple auth types")\
    .with_categories(["Example"])\
    .with_scopes([ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value])\
    .with_auth([
        # OAuth auth type - config created and registered within builder
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="ExampleConnector",
            authorize_url="https://example.com/oauth/authorize",
            token_url="https://example.com/oauth/token",
            redirect_uri="connectors/oauth/callback/ExampleConnector",
            scopes=OAuthScopeConfig(
                personal_sync=["read", "write"],
                team_sync=["read", "write", "admin"],
                agent=["read"]  # Agents only need read access
            ),
            fields=[
                # All OAuth auth fields defined here - auto-added to builder
                CommonFields.client_id("Example Provider"),
                CommonFields.client_secret("Example Provider"),
                AuthField(
                    name="tenantId",
                    display_name="Tenant ID",
                    description="Your tenant ID for multi-tenant OAuth",
                    field_type="TEXT",
                    required=False
                )
            ]
        ),
        # API Token auth type - uniform API, just specify fields
        AuthBuilder.type(AuthType.API_TOKEN).fields([
            AuthField(
                name="apiToken",
                display_name="API Token",
                field_type="PASSWORD",
                is_secret=True
            )
        ])
    ])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/example.svg")
        .with_sync_support(True)
        .with_agent_support(True)
    )\
    .build_decorator()
class ExampleConnector:
    """Example connector - uniform auth API, OAuth config created in builder"""
    pass


# ============================================================================
# Example 1b: Alternative - Register OAuth Config Separately (if needed)
# ============================================================================

def register_example_oauth_config_manually() -> None:
    """
    Alternative way to register OAuth configuration separately.
    Note: This is usually not needed if using with_oauth_config() in the builder,
    as it auto-registers. This is only useful if you need to register configs
    before building connectors.
    """
    oauth_registry = get_oauth_config_registry()

    # Create scope configuration
    scopes = OAuthScopeConfig(
        personal_sync=["read", "write"],
        team_sync=["read", "write", "admin"],
        agent=["read"]
    )

    # Create OAuth config with all fields defined
    oauth_config = OAuthConfig(
        connector_name="ExampleConnector",
        authorize_url="https://example.com/oauth/authorize",
        token_url="https://example.com/oauth/token",
        redirect_uri="connectors/oauth/callback/ExampleConnector",
        scopes=scopes,
        auth_fields=[
            CommonFields.client_id("Example Provider"),
            CommonFields.client_secret("Example Provider")
        ]
    )

    # Register it manually
    oauth_registry.register(oauth_config)


# ============================================================================
# Example 2: Using OAuth Registry to Find OAuth Connectors
# ============================================================================

def find_oauth_connectors() -> None:
    """Find all connectors that support OAuth"""
    # Get OAuth registry (completely independent, no connector registry needed)
    oauth_registry = get_oauth_config_registry()

    # Get all OAuth connectors
    oauth_connectors = oauth_registry.get_oauth_connectors()
    print(f"OAuth connectors count: {len(oauth_connectors)}")

    # Get connectors with personal sync scopes
    personal_sync_connectors = oauth_registry.get_connectors_with_personal_sync()
    print(f"Personal sync connectors: {personal_sync_connectors}")

    # Get connectors with agent scopes
    agent_connectors = oauth_registry.get_connectors_with_agent()
    print(f"Agent connectors: {agent_connectors}")


# ============================================================================
# Example 3: Creating a Toolset with OAuth Config
# ============================================================================

# Define tools for the toolset
jira_tools = [
    ToolDefinition(
        name="search_issues",
        description="Search for Jira issues",
        parameters=[
            {
                "name": "query",
                "type": "string",
                "description": "Search query",
                "required": True
            }
        ],
        returns="List of Jira issues",
        tags=["search", "issues"]
    ),
    ToolDefinition(
        name="create_issue",
        description="Create a new Jira issue",
        parameters=[
            {
                "name": "summary",
                "type": "string",
                "description": "Issue summary",
                "required": True
            },
            {
                "name": "description",
                "type": "string",
                "description": "Issue description",
                "required": False
            }
        ],
        returns="Created issue",
        tags=["create", "issues"]
    ),
    ToolDefinition(
        name="update_issue",
        description="Update an existing Jira issue",
        parameters=[
            {
                "name": "issue_key",
                "type": "string",
                "description": "Issue key (e.g., PROJ-123)",
                "required": True
            },
            {
                "name": "updates",
                "type": "object",
                "description": "Fields to update",
                "required": True
            }
        ],
        returns="Updated issue",
        tags=["update", "issues"]
    )
]


# Clean auth configuration using uniform AuthBuilder API
@ToolsetBuilder("Jira")\
    .in_group("Atlassian")\
    .with_description("Jira issue management tools")\
    .with_category(ToolCategory.APP)\
    .with_auth([
        # API Token auth type - uniform API
        AuthBuilder.type(AuthType.API_TOKEN).fields([
            ToolsetCommonFields.api_token("Jira API Token")
        ]),
        # OAuth auth type - config created and registered within builder
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Jira",
            authorize_url="https://auth.atlassian.com/authorize",
            token_url="https://auth.atlassian.com/oauth/token",
            redirect_uri="toolsets/oauth/callback/Jira",
            scopes=OAuthScopeConfig(
                personal_sync=["read:jira-work"],
                team_sync=["read:jira-work", "write:jira-work"],
                agent=["read:jira-work"]  # Agents need read access
            ),
            fields=[
                # All OAuth fields defined here - auto-added to builder
                ToolsetCommonFields.client_id("Atlassian"),
                ToolsetCommonFields.client_secret("Atlassian")
            ]
        )
    ])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/toolsets/jira.svg")
        .add_documentation_link(DocumentationLink(
            "Jira API Documentation",
            "https://developer.atlassian.com/cloud/jira/platform/rest/v3/",
            "api"
        ))
    )\
    .with_tools(jira_tools)\
    .build_decorator()
class JiraToolset:
    """Jira toolset - uniform auth API, OAuth config created in builder"""
    pass


# ============================================================================
# Example 4: Using Toolset Registry
# ============================================================================

def use_toolset_registry() -> None:
    """Example of using the toolset registry"""
    registry = get_toolset_registry()

    # Register the toolset (usually done automatically via discovery)
    # registry.register_toolset(JiraToolset)

    # Get all toolsets
    all_toolsets = registry.get_all_toolsets()
    print(f"All toolsets: {all_toolsets}")

    # Get toolsets by category
    app_toolsets = registry.get_toolsets_by_category(ToolCategory.APP)
    print(f"App toolsets: {app_toolsets}")

    # Get toolsets by auth type
    oauth_toolsets = registry.get_toolsets_by_auth_type("OAUTH")
    print(f"OAuth toolsets count: {len(oauth_toolsets)}")

    # Get specific toolset metadata
    jira_metadata = registry.get_toolset_metadata("Jira")
    print(f"Jira toolset: {jira_metadata}")

