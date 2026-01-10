from typing import Any, Dict, List, Optional

from app.connectors.core.registry.auth_builder import OAuthConfig, OAuthScopeType


class OAuthConfigRegistry:
    """
    Registry for managing OAuth configurations for connectors and toolsets.

    This registry is completely independent of connector and toolset registries.
    OAuth configs are registered when connectors/toolsets are built, but the registry
    itself has no dependencies on those registries.

    This registry:
    - Stores and retrieves OAuth configurations by connector_name (unique key)
    - Provides discovery methods to find connectors/toolsets with OAuth support
    - Filters connectors by OAuth scope types
    - If same connector_name is registered from both connector and toolset registries,
      the last registration wins (OAuth config is generic and shared)
    """

    def __init__(self) -> None:
        """
        Initialize the OAuth config registry.

        This registry is completely independent and has no dependencies on
        connector or toolset registries.
        """
        self._configs: Dict[str, OAuthConfig] = {}

    def register(self, config: OAuthConfig) -> None:
        """
        Register an OAuth configuration for a connector/toolset.

        If a config with the same connector_name already exists, it will be
        overwritten. This allows the same OAuth config to be shared between
        connector and toolset registries (e.g., "Jira" can exist in both,
        but OAuth config is generic and shared).

        Args:
            config: OAuthConfig instance to register
        """
        self._configs[config.connector_name] = config

    def get_config(self, connector_name: str) -> Optional[OAuthConfig]:
        """Get OAuth configuration for a connector"""
        return self._configs.get(connector_name)

    def get_metadata(self, connector_name: str) -> Dict[str, Any]:
        """
        Get display metadata for a connector/toolset type from OAuth config registry.

        This method returns generic metadata (iconPath, appGroup, appDescription, appCategories)
        stored in the OAuth config, making OAuth configs completely independent.

        Args:
            connector_name: Name of the connector/toolset type

        Returns:
            Dictionary with metadata fields (iconPath, appGroup, appDescription, appCategories)
        """
        oauth_config = self.get_config(connector_name)
        if oauth_config:
            return {
                "iconPath": oauth_config.icon_path,
                "appGroup": oauth_config.app_group,
                "appDescription": oauth_config.app_description,
                "appCategories": oauth_config.app_categories
            }

        # Return defaults if no OAuth config found
        return {
            "iconPath": "/assets/icons/connectors/default.svg",
            "appGroup": "",
            "appDescription": "",
            "appCategories": []
        }

    def get_scopes(
        self,
        connector_name: str,
        scope_type: Optional[OAuthScopeType] = None
    ) -> List[str]:
        """Get OAuth scopes for a connector"""
        config = self.get_config(connector_name)
        if not config:
            return []
        if scope_type:
            return config.scopes.get_scopes_for_type(scope_type)
        return config.scopes.get_all_scopes()

    def has_config(self, connector_name: str) -> bool:
        """Check if a connector has OAuth configuration"""
        return connector_name in self._configs

    def list_connectors(self) -> List[str]:
        """List all connectors with OAuth configurations"""
        return list(self._configs.keys())

    def remove_config(self, connector_name: str) -> bool:
        """Remove OAuth configuration for a connector"""
        if connector_name in self._configs:
            del self._configs[connector_name]
            return True
        return False

    def get_all_configs(self) -> Dict[str, OAuthConfig]:
        """Get all registered OAuth configurations"""
        return self._configs.copy()

    def get_oauth_connectors(self) -> List[str]:
        """
        Get list of all connector/toolset names that support OAuth authentication.

        This method returns only connectors/toolsets that have OAuth configs registered
        in this registry, making it completely independent and self-contained.

        Returns:
            List of connector/toolset names that have OAuth configs registered
        """
        return sorted(self.list_connectors())

    def has_oauth(self, connector_name: str) -> bool:
        """Check if a connector supports OAuth authentication"""
        return self.has_config(connector_name)

    def get_oauth_connectors_by_scope_type(self, scope_type: OAuthScopeType) -> List[str]:
        """
        Get connectors that have OAuth scopes configured for a specific scope type.

        Args:
            scope_type: Type of scope (personal_sync, team_sync, agent)

        Returns:
            List of connector names that have scopes for the specified type
        """
        connectors = []
        for connector_name in self.get_oauth_connectors():
            config = self.get_config(connector_name)
            if config and config.scopes.get_scopes_for_type(scope_type):
                connectors.append(connector_name)
        return sorted(connectors)

    def get_connectors_with_personal_sync(self) -> List[str]:
        """Get connectors with personal sync OAuth scopes"""
        return self.get_oauth_connectors_by_scope_type(OAuthScopeType.PERSONAL_SYNC)

    def get_connectors_with_team_sync(self) -> List[str]:
        """Get connectors with team sync OAuth scopes"""
        return self.get_oauth_connectors_by_scope_type(OAuthScopeType.TEAM_SYNC)

    def get_connectors_with_agent(self) -> List[str]:
        """Get connectors with agent OAuth scopes"""
        return self.get_oauth_connectors_by_scope_type(OAuthScopeType.AGENT)

    def _prepare_search_tokens(self, search: Optional[str]) -> List[str]:
        """
        Prepare search tokens from search query.

        Args:
            search: Search query string

        Returns:
            List of normalized search tokens
        """
        if not search:
            return []
        return [t.strip().lower() for t in str(search).split() if t.strip()]

    def _matches_search(self, item: Dict[str, Any], tokens: List[str], search_fields: List[str]) -> bool:
        """
        Check if an item matches the search tokens.

        Args:
            item: Item to check
            tokens: List of search tokens
            search_fields: List of field names to search in

        Returns:
            True if item matches all tokens (AND logic)
        """
        if not tokens:
            return True

        haystacks: List[str] = []
        for field in search_fields:
            value = item.get(field)
            if isinstance(value, list):
                for v in value:
                    haystacks.append(str(v).lower())
            else:
                haystacks.append(str(value or '').lower())

        combined = ' '.join(haystacks)
        return all(tok in combined for tok in tokens)

    async def get_oauth_config_registry_connectors(
        self,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get all OAuth-enabled connectors/toolsets from registry with pagination and search.

        This method returns connector/toolset types that have OAuth configurations registered.
        It includes auth fields directly from the OAuth config (from AuthBuilder).

        Args:
            page: Page number (1-indexed)
            limit: Number of items per page
            search: Optional search query

        Returns:
            Dictionary with connectors list and pagination info
            Each connector includes auth fields from OAuth config for OAuth configuration
        """
        # Get all OAuth configs from registry (self-contained, no dependency on connector registry)
        oauth_connector_names = self.list_connectors()

        if not oauth_connector_names:
            return {
                "connectors": [],
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "search": search,
                    "totalCount": 0,
                    "totalPages": 0,
                    "hasPrev": False,
                    "hasNext": False,
                    "prevPage": None,
                    "nextPage": None
                }
            }

        # Prepare search tokens
        tokens = self._prepare_search_tokens(search)

        # Build connector info list from OAuth configs with auth schema fields
        connectors = []
        for connector_name in oauth_connector_names:
            oauth_config = self.get_config(connector_name)
            if not oauth_config:
                continue

            # Build connector info from OAuth config metadata
            # Include all OAuth config properties from the auth builder
            connector_info = {
                'name': connector_name,
                'type': connector_name,
                'appGroup': oauth_config.app_group,
                'authType': 'OAUTH',  # OAuth configs are always OAuth
                'appDescription': oauth_config.app_description,
                'appCategories': oauth_config.app_categories,
                'iconPath': oauth_config.icon_path,
                'oauthScopes': {
                    'personalSync': oauth_config.scopes.get_scopes_for_type(
                        OAuthScopeType.PERSONAL_SYNC
                    ),
                    'teamSync': oauth_config.scopes.get_scopes_for_type(
                        OAuthScopeType.TEAM_SYNC
                    ),
                    'agent': oauth_config.scopes.get_scopes_for_type(
                        OAuthScopeType.AGENT
                    )
                },
                # Include all OAuth config properties from auth builder
                'redirectUri': oauth_config.redirect_uri,
                'authorizeUrl': oauth_config.authorize_url,
                'tokenUrl': oauth_config.token_url,
            }

            # Add optional OAuth config properties if they exist
            if oauth_config.token_access_type:
                connector_info['tokenAccessType'] = oauth_config.token_access_type

            if oauth_config.additional_params:
                connector_info['additionalParams'] = oauth_config.additional_params

            # Get auth fields directly from OAuth config (auth_fields from AuthBuilder)
            # Convert AuthField objects to dictionary format for API response
            from app.connectors.core.registry.auth_utils import auth_field_to_dict

            authFields = []
            if oauth_config.auth_fields:
                for auth_field in oauth_config.auth_fields:
                    field_dict = auth_field_to_dict(auth_field)
                    authFields.append(field_dict)

            # Add auth fields if found
            if authFields:
                connector_info['authFields'] = authFields

            # Add documentation links from OAuth config
            if oauth_config.documentation_links:
                connector_info['documentationLinks'] = [
                    {
                        'title': link.title,
                        'url': link.url,
                        'type': link.doc_type
                    }
                    for link in oauth_config.documentation_links
                ]

            # Apply search filter
            search_fields = ['name', 'type', 'appGroup', 'appDescription', 'appCategories']
            if self._matches_search(connector_info, tokens, search_fields):
                connectors.append(connector_info)

        # Calculate pagination
        total_count = len(connectors)
        total_pages = (total_count + limit - 1) // limit
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        has_prev = page > 1
        has_next = end_idx < total_count

        paginated_connectors = connectors[start_idx:end_idx]

        return {
            "connectors": paginated_connectors,
            "pagination": {
                "page": page,
                "limit": limit,
                "search": search,
                "totalCount": total_count,
                "totalPages": total_pages,
                "hasPrev": has_prev,
                "hasNext": has_next,
                "prevPage": page - 1 if has_prev else None,
                "nextPage": page + 1 if has_next else None
            }
        }

    def get_connector_registry_info(self, connector_name: str) -> Optional[Dict[str, Any]]:
        """
        Get registry information for a specific connector type.

        This method returns the same structure as get_oauth_config_registry_connectors
        but for a single connector, making it efficient for fetching specific connector info.

        Args:
            connector_name: Name of the connector/toolset type

        Returns:
            Dictionary with connector info including authFields, documentationLinks, etc.
            Returns None if connector not found
        """
        oauth_config = self.get_config(connector_name)
        if not oauth_config:
            return None

        # Build connector info from OAuth config metadata (same structure as get_oauth_config_registry_connectors)
        connector_info = {
            'name': connector_name,
            'type': connector_name,
            'appGroup': oauth_config.app_group,
            'authType': 'OAUTH',
            'appDescription': oauth_config.app_description,
            'appCategories': oauth_config.app_categories,
            'iconPath': oauth_config.icon_path,
            'oauthScopes': {
                'personalSync': oauth_config.scopes.get_scopes_for_type(
                    OAuthScopeType.PERSONAL_SYNC
                ),
                'teamSync': oauth_config.scopes.get_scopes_for_type(
                    OAuthScopeType.TEAM_SYNC
                ),
                'agent': oauth_config.scopes.get_scopes_for_type(
                    OAuthScopeType.AGENT
                )
            },
            'redirectUri': oauth_config.redirect_uri,
            'authorizeUrl': oauth_config.authorize_url,
            'tokenUrl': oauth_config.token_url,
        }

        # Add optional OAuth config properties if they exist
        if oauth_config.token_access_type:
            connector_info['tokenAccessType'] = oauth_config.token_access_type

        if oauth_config.additional_params:
            connector_info['additionalParams'] = oauth_config.additional_params

        # Get auth fields directly from OAuth config (auth_fields from AuthBuilder)
        from app.connectors.core.registry.auth_utils import auth_field_to_dict

        authFields = []
        if oauth_config.auth_fields:
            for auth_field in oauth_config.auth_fields:
                field_dict = auth_field_to_dict(auth_field)
                authFields.append(field_dict)

        if authFields:
            connector_info['authFields'] = authFields

        # Add documentation links from OAuth config
        if oauth_config.documentation_links:
            connector_info['documentationLinks'] = [
                {
                    'title': link.title,
                    'url': link.url,
                    'type': link.doc_type
                }
                for link in oauth_config.documentation_links
            ]

        return connector_info

    async def get_oauth_configs_for_connector(
        self,
        connector_type: str,
        oauth_configs: List[Dict[str, Any]],
        org_id: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        include_full_config: bool = False,
        is_admin: bool = False
    ) -> Dict[str, Any]:
        """
        Get OAuth configs for a connector type with pagination and search.

        Args:
            connector_type: Type of connector
            oauth_configs: List of all OAuth configs from etcd
            org_id: Organization ID for filtering
            page: Page number (1-indexed)
            limit: Number of items per page
            search: Optional search query
            include_full_config: If True and user is admin, include full config details (performance optimization)
            is_admin: Whether the requesting user is an admin

        Returns:
            Dictionary with filtered configs and pagination info
        """
        # Prepare search tokens
        tokens = self._prepare_search_tokens(search)

        # Filter configs by org
        filtered_configs = []
        for config in oauth_configs:
            config_org_id = config.get("orgId")

            # All users in the same org can see published OAuth configs
            if config_org_id == org_id:
                # For admins with include_full_config, return complete config
                # For others, return only essential fields (no sensitive data)
                if include_full_config and is_admin:
                    config_info = config  # Full config with credentials
                else:
                    # Build config info with only essential fields (no sensitive data)
                    config_info = {
                        "_id": config.get("_id"),
                        "oauthInstanceName": config.get("oauthInstanceName"),
                        "iconPath": config.get("iconPath", "/assets/icons/connectors/default.svg"),
                        "appGroup": config.get("appGroup", ""),
                        "appDescription": config.get("appDescription", ""),
                        "appCategories": config.get("appCategories", []),
                        "connectorType": config.get("connectorType", connector_type),
                        "createdAtTimestamp": config.get("createdAtTimestamp"),
                        "updatedAtTimestamp": config.get("updatedAtTimestamp")
                    }

                # Apply search filter
                search_fields = [
                    "oauthInstanceName",
                    "appGroup",
                    "appDescription",
                    "connectorType",
                    "appCategories"
                ]
                if self._matches_search(config_info, tokens, search_fields):
                    filtered_configs.append(config_info)

        # Calculate pagination
        total_count = len(filtered_configs)
        total_pages = (total_count + limit - 1) // limit
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        has_prev = page > 1
        has_next = end_idx < total_count

        paginated_configs = filtered_configs[start_idx:end_idx]

        return {
            "oauthConfigs": paginated_configs,
            "pagination": {
                "page": page,
                "limit": limit,
                "search": search,
                "totalCount": total_count,
                "totalPages": total_pages,
                "hasPrev": has_prev,
                "hasNext": has_next,
                "prevPage": page - 1 if has_prev else None,
                "nextPage": page + 1 if has_next else None
            }
        }


# Global OAuth config registry instance (completely independent)
_global_oauth_config_registry = OAuthConfigRegistry()


def get_oauth_config_registry() -> OAuthConfigRegistry:
    """
    Get the global OAuth config registry instance.

    This registry is completely independent of connector and toolset registries.
    OAuth configs are registered when connectors/toolsets are built, but the registry
    itself has no dependencies on those registries.

    Returns:
        OAuthConfigRegistry instance (singleton)
    """
    return _global_oauth_config_registry

