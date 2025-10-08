## Add a New Application, Client, DataSource and Tools (Complete LLM-Ready Guide)

This comprehensive guide shows the complete lifecycle for adding a new application with tools, from client creation to LLM exposure. It includes working code examples modeled after your existing Linear, Google, Jira, Airtable, Zendesk, and S3 implementations.

### Overview
- **Goal**: Create a new app (e.g., `linear`) with one or more tools (functions) callable by the LLM.
- **Complete lifecycle**:
  1. Create a client class under `app/sources/client/<app>/`
  2. Create a DataSource wrapper under `app/sources/external/<app>/`
  3. Create Action class with `@tool` methods under `app/agents/actions/<app>/`
  4. Create a Factory under `app/agents/tools/factories/`
  5. Register in discovery config and factory registry
  6. Verify tools are exposed to LLM

### File/Folder Conventions (Your Existing Pattern)
- **Clients**: `backend/python/app/sources/client/<vendor>/<service>.py` (e.g., `linear/linear.py`)
- **DataSources**: `backend/python/app/sources/external/<vendor>/<service>.py` (e.g., `linear/linear.py`)
- **Actions/Tools**: `backend/python/app/agents/actions/<vendor>/<service>.py` (e.g., `linear/linear.py`)
- **Factories**: `backend/python/app/agents/tools/factories/<vendor>.py` (e.g., `linear.py`)
- **Discovery Config**: `backend/python/app/agents/tools/config.py`
- **Factory Registry**: `backend/python/app/agents/tools/factories/registry.py`

### Architectural Pattern (Used by Google/Jira/Meet/Linear/Airtable)
1. **Factory** builds an authenticated SDK client using `ConfigurationService` (etcd-backed)
2. **DataSource** wraps the SDK client and exposes async methods mirroring the external API
3. **Action** constructs DataSource with injected client and exposes sync `@tool` methods that call DataSource

**Strict Rule**: Only Factories may import from `app/sources/client/*`. Actions/DataSources must never import client classes directly.

---

### 0) Create Your Client (SDK wrapper under `app/sources/client/*`)

A client class encapsulates authentication and low-level SDK/service access. It should implement `IClient` and expose a `.get_client()` method returning the underlying SDK client.

**Place it under**: `backend/python/app/sources/client/<vendor>/<service>.py`

**Pattern from your codebase** (Linear example - see `app/sources/client/linear/linear.py`):

```python
# File: backend/python/app/sources/client/linear/linear.py
import logging
from typing import Any, Optional, Union
from pydantic import BaseModel, Field  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.sources.client.graphql.client import GraphQLClient
from app.sources.client.iclient import IClient


class LinearGraphQLClient(GraphQLClient):
    """Linear GraphQL client via API token."""

    def __init__(self, token: str, timeout: int = 30) -> None:
        headers = {"Authorization": token, "Content-Type": "application/json"}
        super().__init__(endpoint="https://api.linear.app/graphql", headers=headers, timeout=timeout)


class LinearTokenConfig(BaseModel):
    """Configuration for Linear GraphQL client via API token."""
    token: str = Field(..., description="Linear API token")
    timeout: int = Field(default=30, description="Request timeout in seconds", gt=0)

    def create_client(self) -> LinearGraphQLClient:
        """Create a Linear GraphQL client."""
        return LinearGraphQLClient(self.token, self.timeout)


class LinearClient(IClient):
    """Builder class for Linear GraphQL clients with different construction methods."""

    def __init__(self, client: LinearGraphQLClient) -> None:
        """Initialize with a Linear GraphQL client object."""
        self.client = client

    def get_client(self) -> LinearGraphQLClient:
        """Return the Linear GraphQL client object."""
        return self.client

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
    ) -> "LinearClient":
        """Build LinearClient using configuration service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
        Returns:
            LinearClient instance
        """
        try:
            # Get Linear configuration from the configuration service
            config = await cls._get_connector_config(logger, config_service)

            if not config:
                raise ValueError("Failed to get Linear connector configuration")

            # Extract configuration values
            auth_type = config.get("auth_type", "token")  # token or oauth
            timeout = config.get("timeout", 30)

            # Create appropriate client based on auth type
            if auth_type == "oauth":
                oauth_token = config.get("oauth_token", "")
                if not oauth_token:
                    raise ValueError("OAuth token required for oauth auth type")
                client = LinearGraphQLClientViaOAuth(oauth_token, timeout)

            else:  # Default to token auth
                token = config.get("token", "")
                if not token:
                    raise ValueError("Token required for token auth type")
                client = LinearGraphQLClient(token, timeout)

            return cls(client)

        except Exception as e:
            logger.error(f"Failed to build Linear client from services: {str(e)}")
            raise

    @staticmethod
    async def _get_connector_config(logger: logging.Logger, config_service: ConfigurationService) -> Dict[str, Any]:
        """Fetch connector config from etcd for Linear."""
        try:
            config = await config_service.get_config("/services/connectors/linear/config")
            return config or {}
        except Exception as e:
            logger.error(f"Failed to get Linear connector config: {e}")
            return {}
```

**Other client patterns in your repo**:
- **Google**: `app/sources/client/google/google.py` - Uses service account/OAuth, builds SDK clients with scopes
- **Jira**: `app/sources/client/jira/jira.py` - Multiple auth modes, discovers base URL via cloud ID
- **Airtable**: `app/sources/client/airtable/airtable.py` - Similar to Linear, uses API keys
- **Zendesk**: `app/sources/client/zendesk/zendesk.py` - REST API with OAuth/token auth
- **S3**: `app/sources/client/s3/s3.py` - AWS SDK with access keys

**Key Requirements**:
- Implement `IClient` interface
- Provide `build_from_services(logger, config_service, ...)` class method
- Expose `get_client()` returning the underlying SDK client
- Handle authentication via `ConfigurationService` (etcd-backed secrets)

---

### 1) Create Your DataSource (under `app/sources/external/*`) and Action (Tools)

Use a DataSource that wraps the SDK client, then an Action class that builds the DataSource from the injected client. Decorate action methods as tools.

**Pattern from your codebase** (Linear example - see `app/sources/external/linear/linear.py`):

```python
# File: backend/python/app/sources/external/linear/linear.py
from typing import Any, Dict, List, Optional

from app.sources.client.graphql.response import GraphQLResponse
from app.sources.client.linear.graphql_op import LinearGraphQLOperations
from app.sources.client.linear.linear import LinearClient


class LinearDataSource:
    """
    Complete Linear GraphQL API client wrapper
    Auto-generated wrapper for Linear GraphQL operations.
    This class provides unified access to ALL Linear GraphQL operations while
    maintaining proper typing and error handling.
    """

    def __init__(self, linear_client: LinearClient) -> None:
        """
        Initialize the Linear GraphQL data source.
        Args:
            linear_client (LinearClient): Linear client instance
        """
        self._linear_client = linear_client

    # =============================================================================
    # QUERY OPERATIONS
    # =============================================================================

    # USER & AUTHENTICATION QUERIES
    async def viewer(self) -> GraphQLResponse:
        """Get current user information"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "viewer")
        variables = {}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="viewer"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query viewer: {str(e)}")

    async def user(self, id: str) -> GraphQLResponse:
        """Get user by ID"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "user")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="user"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query user: {str(e)}")
```

**Pattern from your codebase** (Linear Action example - see `app/agents/actions/linear/linear.py`):

```python
# File: backend/python/app/agents/actions/linear/linear.py
import asyncio
import json
import logging
from typing import Optional, Tuple

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.external.linear.linear import LinearDataSource

logger = logging.getLogger(__name__)


class Linear:
    """Linear tool exposed to the agents"""

    def __init__(self, client: object) -> None:
        """Initialize the Linear tool
        Args:
            client: Linear client object
        Returns:
            None
        """
        self.client = LinearDataSource(client)

    def _run_async(self, coro):
        """Helper method to run async operations in sync context"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If we're already in an async context, we need to use a thread pool
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, coro)
                    return future.result()
            else:
                return loop.run_until_complete(coro)
        except Exception as e:
            logger.error(f"Error running async operation: {e}")
            raise

    @tool(
        app_name="linear",
        tool_name="get_viewer",
        description="Get current user information",
        parameters=[]
    )
    def get_viewer(self) -> Tuple[bool, str]:
        """Get current user information"""
        """
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use LinearDataSource method
            response = self._run_async(self.client.viewer())

            if response.success:
                return True, json.dumps({"data": response.data})
            else:
                return False, json.dumps({"error": response.message})
        except Exception as e:
            logger.error(f"Error getting viewer: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="linear",
        tool_name="get_teams",
        description="Get teams",
        parameters=[
            ToolParameter(
                name="first",
                type=ParameterType.INTEGER,
                description="Number of teams to return",
                required=False
            ),
            ToolParameter(
                name="after",
                type=ParameterType.STRING,
                description="Cursor for pagination",
                required=False
            )
        ]
    )
    def get_teams(
        self,
        first: Optional[int] = None,
        after: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Get teams"""
        """
        Args:
            first: Number of teams to return
            after: Cursor for pagination
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use LinearDataSource method
            response = self._run_async(self.client.teams(first=first, after=after))

            if response.success:
                return True, json.dumps({"data": response.data})
            else:
                return False, json.dumps({"error": response.message})
        except Exception as e:
            logger.error(f"Error getting teams: {e}")
            return False, json.dumps({"error": str(e)})
```

**Key Points**:
- **DataSource**: Async methods that call `client.get_client().execute()` and return `GraphQLResponse` or similar response objects
- **Action**: Sync methods decorated with `@tool`, use `_run_async` helper to call DataSource methods, return `(success, json_str)` tuples
- **Parameters**: Use `ToolParameter` for complex types, otherwise auto-inferred from type hints
- **Error Handling**: Always return structured responses for LLM consumption

---

### 2) Add a Client Factory (Recommended)

Factories standardize how action classes receive clients (tokens, configs). The runtime uses `ClientFactoryRegistry` to instantiate your action classes.

**Pattern from your codebase** (Linear example - see `app/agents/tools/factories/linear.py`):

```python
# File: backend/python/app/agents/tools/factories/linear.py

from app.agents.tools.factories.base import ClientFactory
from app.config.configuration_service import ConfigurationService
from app.sources.client.linear.linear import LinearClient


class LinearClientFactory(ClientFactory):
    """Factory for creating Linear clients"""

    async def create_client(self, config_service: ConfigurationService, logger: Any) -> LinearClient:
        """
        Create and return a Linear client instance asynchronously.

        Args:
            config_service: Configuration service instance
            logger: Logger instance

        Returns:
            LinearClient instance
        """
        # Build via central config (etcd) similar to Google/Jira
        client = await LinearClient.build_from_services(logger=logger, config_service=config_service)
        return client
```

**Pattern from your codebase** (Google Meet example - see `app/agents/tools/factories/google_meet.py`):

```python
# File: backend/python/app/agents/tools/factories/google_meet.py
from typing import Any, List

from app.agents.tools.factories.base import ClientFactory
from app.config.configuration_service import ConfigurationService
from app.sources.client.google.google import GoogleClient


class GoogleMeetClientFactory(ClientFactory):
    """Factory for creating Google Meet clients"""

    async def create_client(self, config_service: ConfigurationService, logger: Any) -> object:
        """
        Create and return a Google Meet client instance asynchronously.

        Args:
            config_service: Configuration service instance
            logger: Logger instance

        Returns:
            Google Meet SDK client object
        """
        scopes: List[str] = [
            "https://www.googleapis.com/auth/meetings.space.created",
            "https://www.googleapis.com/auth/meetings.space.settings",
            "https://www.googleapis.com/auth/meetings.space.readonly",
        ]
        google_client = await GoogleClient.build_from_services(
            service_name="meet",
            version="v2",
            logger=logger,
            config_service=config_service,
            scopes=scopes,
        )
        return google_client.get_client()  # return raw SDK for DataSource
```

**Additional examples** (from your existing `app/agents/tools/factories/`):

```python
# File: backend/python/app/agents/tools/factories/airtable.py
from app.agents.tools.factories.base import ClientFactory
from app.sources.client.airtable.airtable import AirtableClient

class AirtableClientFactory(ClientFactory):
    async def create_client(self, config_service, logger):
        return await AirtableClient.build_from_services(logger=logger, config_service=config_service)

# File: backend/python/app/agents/tools/factories/zendesk.py
from app.agents.tools.factories.base import ClientFactory
from app.sources.client.zendesk.zendesk import ZendeskClient

class ZendeskClientFactory(ClientFactory):
    async def create_client(self, config_service, logger):
        return await ZendeskClient.build_from_services(logger=logger, config_service=config_service)

# File: backend/python/app/agents/tools/factories/s3.py
from app.agents.tools.factories.base import ClientFactory
from app.sources.client.s3.s3 import S3Client

class S3ClientFactory(ClientFactory):
    async def create_client(self, config_service, logger):
        return await S3Client.build_from_services(logger=logger, config_service=config_service)
```

**Key Requirements**:
- Implement `async create_client(self, config_service, logger)` method
- Use `build_from_services(logger, config_service, ...)` from your client class
- Return either the wrapper client (for DataSources expecting `Client` objects) or raw SDK client (for DataSources expecting raw SDK)
- Only factories may import from `app/sources/client/*`

---

### 3) Register the App and (Optionally) Sub-Services in Discovery Config

Add your app in `ToolDiscoveryConfig.APP_CONFIGS` so discovery and factories know about it.

```python
# File: backend/python/app/agents/tools/config.py
from app.agents.tools.config import AppConfiguration

ToolDiscoveryConfig.APP_CONFIGS.update({
    "linear": AppConfiguration(
        app_name="linear",
        client_builder="LinearClient",
    )
})
```

If you have sub-services, set `subdirectories=["foo", "bar"]` and optional `service_configs` similar to `google`.

Optionally mark essential tools via either:
- Setting `is_essential=True` on individual `@tool` decorators, and/or
- Adding a pattern to `ToolDiscoveryConfig.ESSENTIAL_TOOL_PATTERNS` like `"linear."`.

---

### 4) Register the Factory

Import and register your factory in the global registry. The registry auto-initializes default factories based on `ToolDiscoveryConfig`.

```python
# File: backend/python/app/agents/tools/factories/registry.py
from app.agents.tools.factories.linear import LinearClientFactory  # add import

# Inside ClientFactoryRegistry.initialize_default_factories():
# after other elif blocks
elif app_name == "linear":
    cls.register(app_name, LinearClientFactory())

# Reference: How Google and Jira are registered
# - Google: the registry iterates ToolDiscoveryConfig.APP_CONFIGS and, for google, registers
#   factories per sub-service using service name/version (gmail, drive, calendar, meet).
# - Jira: a single factory is registered for the app.
```

If your app has sub-services, register each explicitly similar to `microsoft` or `google` handling.

---

### 5) Wire the Action Class to the Factory (Automatic)

At runtime, the tool wrapper resolves methods to action instances using the factory registry. No extra wiring needed if steps above are complete.

Behavior summary:
- The LangChain wrapper calls your method via `RegistryToolWrapper`.
- The wrapper asks `ClientFactoryRegistry.get_factory(app_name)` for a client.
- The factory builds the client using `retrieval_service.config_service` and injects it into your action class.

Google-style notes:
- When using Google, a factory may internally call `GoogleClient.build_from_services(service_name, version, scopes, config_service, logger)` and pass the returned `.get_client()` to your DataSource.
  - Example scopes for Meet: `https://www.googleapis.com/auth/meetings.space.readonly`, etc.
Jira-style notes:
- The Jira factory should resolve tokens and base URL (optionally via cloud ID APIs) using the config service, then build the low-level HTTP client and wrap it in a `JiraClient` with `.get_client()`.

---

### 6) Testing and Verification

Quick ways to confirm your tools are registered and usable:

```python
# Anywhere in backend runtime context
from app.modules.agents.qna.tool_registry import get_all_available_tool_names, get_tool_by_name
from app.modules.agents.qna.chat_state import ChatState

state = ChatState()
names = get_all_available_tool_names()
print(names)  # Ensure you see entries like "linear.get_viewer", "linear.get_teams"

tool = get_tool_by_name("linear.get_viewer", state)
print(tool.description)  # Should show: "Get current user information"
```

- Execute via LLM or directly through the wrapper:

```python
result = tool._run()  # returns a string result for the LLM
print(result)  # Should return JSON like: {"data": {"id": "...", "name": "...", "email": "..."}}
```

**Verification Checklist**:
- [ ] Tools appear in `get_all_available_tool_names()`
- [ ] Individual tools can be retrieved via `get_tool_by_name()`
- [ ] Tool execution returns proper `(success, json_string)` format
- [ ] No import errors when loading the application

---

### 7) Optional: Standalone Tool Function Pattern

If you prefer a pure function tool (no client), use:

```python
# File: backend/python/app/agents/actions/linear/utils.py
from app.agents.tools.decorator import tool
from app.agents.tools.config import ToolCategory


@tool(
    app_name="linear",
    tool_name="echo",
    description="Echo back a message",
    category=ToolCategory.UTILITY,
)
def echo(message: str) -> str:
    return message
```

This registers immediately and requires no factory or client setup.

---

### 8) LLM Tool Schema Generation (FYI)

The registry auto-generates schemas (OpenAI/Anthropic) from your tool definitions and type hints. Use precise parameter names, types, and descriptions for best LLM UX.

How schemas are generated (summary):
- OpenAI functions: via `ToolRegistry.generate_openai_schema()` inspecting `Tool.parameters` (name, type, required, enums).
- Anthropic tools: via `ToolRegistry.generate_anthropic_schema()` with `input_schema` built from your parameters.
- Parameter types: inferred automatically when not supplied using the `@tool` decorator (`_extract_parameters`). Prefer explicit `ToolParameter` for complex shapes.

---

### 9) Checklist
- [ ] Created action class and decorated tool methods under `agents/actions/<app>/...`
- [ ] Implemented `ClientFactory` for the app (if API-backed)
- [ ] Added app entry to `ToolDiscoveryConfig.APP_CONFIGS`
- [ ] Registered factory in `factories/registry.py`
- [ ] Verified exposure via `get_all_available_tool_names()`
- [ ] Executed a tool via `get_tool_by_name(...)._run(**kwargs)`
- [ ] Confirmed Actions do not import from `app/sources/client/*`
- [ ] Confirmed DataSources only depend on the injected client

---

### 10) Environment and Secrets
- Provide required secrets via your existing `config_service` (e.g., `LINEAR_TOKEN`).
- If Dockerized, add env vars to the appropriate `docker-compose.*.yml` or service config.

**Linear-specific examples** (from your existing implementation):
- Token: `LINEAR_TOKEN` - Your Linear API token
- Timeout: `LINEAR_TIMEOUT` (optional, defaults to 30)

**Google-specific examples** (for Meet, from your code):
- Service name/version: `service_name="meet", version="v2"`
- Scopes (minimum):
  - `https://www.googleapis.com/auth/meetings.space.created`
  - `https://www.googleapis.com/auth/meetings.space.settings`
  - `https://www.googleapis.com/auth/meetings.space.readonly`

**Jira-specific examples**:
- Token: `JIRA_TOKEN` - Your Jira bearer token
- Base URL: `JIRA_BASE_URL` - Your Jira instance URL

Store secrets in your configuration service (etcd); client builders retrieve them via `ConfigurationService`.

---

### Troubleshooting

**Common Issues and Solutions**:

1. **Tool not visible in `get_all_available_tool_names()`**:
   - Check `@tool` decorator `app_name`/`tool_name` match your registration
   - Ensure module is importable (no syntax errors)
   - Verify factory registration in `registry.py`

2. **Factory not found**:
   - Ensure factory class is imported in `registry.py`
   - Check factory is registered in `initialize_default_factories()`
   - Verify `config_service` has required secrets

3. **Missing secrets/configuration**:
   - Confirm secret names in `config_service.get_secret()` match your etcd keys
   - Check etcd configuration: `/services/connectors/linear/config` for Linear
   - Ensure Docker environment variables are properly mapped

4. **Method not called by LLM**:
   - Verify method name in decorator matches function name
   - Check parameter types match LLM schema expectations
   - Ensure return format is `(bool, json_string)`

5. **Import errors in Actions/DataSources**:
   - Never import from `app/sources/client/*` - only factories should do this
   - Use injected client from factory in Action constructor
   - Pass client to DataSource constructor

6. **Async/sync issues**:
   - Use `_run_async` helper in Actions for calling async DataSource methods
   - Ensure event loop handling in your async helpers (like Linear's implementation)

7. **Authentication failures**:
   - Check token format and validity
   - Verify scopes/permissions for your API calls
   - Check network connectivity to external services

**Debug Commands**:
```python
# Check if tools are registered
from app.modules.agents.qna.tool_registry import get_all_available_tool_names
print(get_all_available_tool_names())

# Test tool retrieval
from app.modules.agents.qna.chat_state import ChatState
from app.modules.agents.qna.tool_registry import get_tool_by_name
state = ChatState()
tool = get_tool_by_name("linear.get_viewer", state)
print(tool.description)

# Test tool execution
result = tool._run()
print(result)
```

**File Structure Reminder**:
```
backend/python/app/
├── sources/
│   ├── client/           # Client classes (factories only import from here)
│   │   └── linear/
│   │       └── linear.py # LinearClient, build_from_services()
│   └── external/         # DataSources (wrap clients)
│       └── linear/
│           └── linear.py # LinearDataSource (async methods)
└── agents/
    ├── actions/          # Tool classes (sync, use DataSources)
    │   └── linear/
    │       └── linear.py # Linear (with @tool methods)
    └── tools/
        ├── factories/    # Factories (create clients)
        │   └── linear.py # LinearClientFactory
        ├── config.py     # Discovery config
        └── factories/
            └── registry.py # Factory registry
```


