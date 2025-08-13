from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from app.agents.client.http import HTTPClient
from app.agents.client.iclient import IClient
from app.config.configuration_service import ConfigurationService

RESOURCE_URL = "https://api.atlassian.com/oauth/token/accessible-resources"
BASE_URL = "https://api.atlassian.com/ex/jira"

@dataclass
class AtlassianCloudResource:
    """Represents an Atlassian Cloud resource (site)
    Args:
        id: The ID of the resource
        name: The name of the resource
        url: The URL of the resource
        scopes: The scopes of the resource
        avatar_url: The avatar URL of the resource
    """
    id: str
    name: str
    url: str
    scopes: List[str]
    avatar_url: Optional[str] = None

class JiraRESTClientViaUsernamePassword(HTTPClient):
    """JIRA REST client via username and password
    Args:
        username: The username to use for authentication
        password: The password to use for authentication
        token_type: The type of token to use for authentication
    """
    def __init__(self, username: str, password: str, token_type: str = "Basic") -> None:
        #TODO: Implement
        pass

class JiraRESTClientViaApiKey(HTTPClient):
    """JIRA REST client via API key
    Args:
        email: The email to use for authentication
        api_key: The API key to use for authentication
    """
    def __init__(self, email: str, api_key: str) -> None:
        #TODO: Implement
        pass

class JiraRESTClientViaToken(HTTPClient):
    def __init__(self, token: str, token_type: str = "Bearer") -> None:
        super().__init__(token, token_type)
        self.accessible_resources = None
        self.cloud_id = None

    async def initialize(self) -> None:
        if self.accessible_resources is None:
            self.accessible_resources = await self._get_accessible_resources()
            if self.accessible_resources:
                self.cloud_id = self.accessible_resources[0].id
            else:
                raise Exception("No accessible resources found")

    async def _get_accessible_resources(self) -> List[AtlassianCloudResource]:
        """
        Get list of Atlassian sites (Confluence/Jira instances) accessible to the user
        Args:
            None
        Returns:
            List of accessible Atlassian Cloud resources
        """
        response = await self.get(RESOURCE_URL)
        return [
            AtlassianCloudResource(
                id=resource["id"],
                name=resource.get("name", ""),
                url=resource["url"],
                scopes=resource.get("scopes", []),
                avatar_url=resource.get("avatarUrl")
            )
            for resource in response
        ]

    async def get_projects(
        self,
        expand: Optional[str] = None,
        recent: Optional[int] = None,
        properties: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get all JIRA projects
        Args:
            expand: Use expand to include additional information about projects
            recent: Returns the user's most recently accessed projects
            properties: A list of project properties to return for the project
        Returns:
            List of JIRA projects
        """
        await self.initialize()
        base_url = f"{BASE_URL}/{self.cloud_id}"
        url = f"{base_url}/rest/api/3/project"

        params = {}
        if expand:
            params["expand"] = expand
        if recent:
            params["recent"] = recent
        if properties:
            params["properties"] = ",".join(properties)

        projects = []
        while True:
            projects_batch = await self.get(url, params=params) # type: ignore
            if isinstance(projects_batch, list):
                projects.extend(projects_batch)
                break
            else:
                projects.extend(projects_batch.get("values", []))
                if not projects_batch.get("nextPage"):
                    break
                url = projects_batch.get("nextPage")
                params = {}  # URL already contains params

        return projects

    async def get_project(
        self,
        project_key_or_id: str,
        expand: Optional[str] = None,
        properties: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Get details of a specific JIRA project
        Args:
            project_key_or_id: The project ID or project key
            expand: Use expand to include additional information
            properties: A list of project properties to return
        Returns:
            Project details
        """
        await self.initialize()
        base_url = f"{BASE_URL}/{self.cloud_id}"
        url = f"{base_url}/rest/api/3/project/{project_key_or_id}"

        params = {}
        if expand:
            params["expand"] = expand
        if properties:
            params["properties"] = ",".join(properties)

        return await self.get(url, params=params)

    async def search_issues(
        self,
        jql: str,
        start_at: int = 0,
        max_results: int = 50,
        validate_query: bool = True,
        fields: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Search for issues using JQL (Jira Query Language)
        Args:
            jql: The JQL query string
            start_at: The index of the first issue to return
            max_results: The maximum number of issues to return
            validate_query: Whether to validate the JQL query
            fields: A list of fields to return for each issue
            expand: A list of the parameters to expand
        Returns:
            Search results containing issues and metadata
        """
        await self.initialize()
        base_url = f"{BASE_URL}/{self.cloud_id}"
        url = f"{base_url}/rest/api/3/search"

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

        payload = {
            "jql": jql,
            "startAt": start_at,
            "maxResults": max_results,
            "validateQuery": validate_query
        }

        if fields:
            payload["fields"] = fields
        if expand:
            payload["expand"] = expand

        return await self.post(url, payload, headers=headers)

    async def get_issue(
        self,
        issue_key_or_id: str,
        fields: Optional[List[str]] = None,
        expand: Optional[str] = None,
        properties: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Get details of a specific issue
        Args:
            issue_key_or_id: The issue ID or issue key
            fields: A list of fields to return
            expand: Use expand to include additional information
            properties: A list of issue properties to return
        Returns:
            Issue details
        """
        await self.initialize()
        base_url = f"{BASE_URL}/{self.cloud_id}"
        url = f"{base_url}/rest/api/3/issue/{issue_key_or_id}"

        params = {}
        if fields:
            params["fields"] = ",".join(fields)
        if expand:
            params["expand"] = expand
        if properties:
            params["properties"] = ",".join(properties)

        return await self.get(url, params=params)

    async def create_issue(
        self,
        project_key: str,
        summary: str,
        issue_type_name: str,
        description: Optional[str] = None,
        assignee_account_id: Optional[str] = None,
        reporter_account_id: Optional[str] = None,
        priority_name: Optional[str] = None,
        labels: Optional[List[str]] = None,
        components: Optional[List[str]] = None,
        custom_fields: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create a new issue in JIRA
        Args:
            project_key: The key of the project to create the issue in
            summary: The summary/title of the issue
            issue_type_name: The name of the issue type
            description: The description of the issue
            assignee_account_id: The account ID of the assignee
            reporter_account_id: The account ID of the reporter
            priority_name: The name of the priority
            labels: List of labels to add to the issue
            components: List of component names
            custom_fields: Dictionary of custom field IDs and values
        Returns:
            Created issue details
        """
        await self.initialize()
        base_url = f"{BASE_URL}/{self.cloud_id}"
        url = f"{base_url}/rest/api/3/issue"

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

        fields = {
            "project": {"key": project_key},
            "summary": summary,
            "issuetype": {"name": issue_type_name}
        }

        if description:
            fields["description"] = {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "text": description,
                                "type": "text"
                            }
                        ]
                    }
                ]
            }

        if assignee_account_id:
            fields["assignee"] = {"accountId": assignee_account_id}

        if reporter_account_id:
            fields["reporter"] = {"accountId": reporter_account_id}

        if priority_name:
            fields["priority"] = {"name": priority_name}

        if labels:
            fields["labels"] = labels

        if components:
            fields["components"] = [{"name": comp} for comp in components]

        if custom_fields:
            fields.update(custom_fields)

        payload = {"fields": fields}

        return await self.post(url, payload, headers=headers)

    async def update_issue(
        self,
        issue_key_or_id: str,
        fields: Dict[str, Any],
        notify_users: bool = True,
    ) -> None:
        """
        Update an existing issue
        Args:
            issue_key_or_id: The issue ID or issue key
            fields: Dictionary of fields to update
            notify_users: Whether to send notifications to users
        """
        await self.initialize()
        base_url = f"{BASE_URL}/{self.cloud_id}"
        url = f"{base_url}/rest/api/3/issue/{issue_key_or_id}"

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

        payload = {
            "fields": fields,
            "notifyUsers": notify_users
        }

        await self.put(url, payload, headers=headers)

    async def add_comment(
        self,
        issue_key_or_id: str,
        comment_body: str,
        visibility: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Add a comment to an issue
        Args:
            issue_key_or_id: The issue ID or issue key
            comment_body: The comment text
            visibility: Visibility restrictions for the comment
        Returns:
            Created comment details
        """
        await self.initialize()
        base_url = f"{BASE_URL}/{self.cloud_id}"
        url = f"{base_url}/rest/api/3/issue/{issue_key_or_id}/comment"

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

        body_content = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "text": comment_body,
                            "type": "text"
                        }
                    ]
                }
            ]
        }

        payload = {"body": body_content}

        if visibility:
            payload["visibility"] = visibility

        return await self.post(url, payload, headers=headers)

    async def get_comments(
        self,
        issue_key_or_id: str,
        start_at: int = 0,
        max_results: int = 50,
        order_by: str = "created",
        expand: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get comments for an issue
        Args:
            issue_key_or_id: The issue ID or issue key
            start_at: The index of the first comment to return
            max_results: The maximum number of comments to return
            order_by: The order to return comments (created, updated)
            expand: Use expand to include additional information
        Returns:
            Comments data
        """
        await self.initialize()
        base_url = f"{BASE_URL}/{self.cloud_id}"
        url = f"{base_url}/rest/api/3/issue/{issue_key_or_id}/comment"

        params = {
            "startAt": start_at,
            "maxResults": max_results,
            "orderBy": order_by
        }

        if expand:
            params["expand"] = expand

        return await self.get(url, params=params)

    async def transition_issue(
        self,
        issue_key_or_id: str,
        transition_id: str,
        fields: Optional[Dict[str, Any]] = None,
        comment: Optional[str] = None,
    ) -> None:
        """
        Transition an issue through workflow
        Args:
            issue_key_or_id: The issue ID or issue key
            transition_id: The ID of the transition to perform
            fields: Dictionary of fields to update during transition
            comment: Optional comment to add during transition
        """
        await self.initialize()
        base_url = f"{BASE_URL}/{self.cloud_id}"
        url = f"{base_url}/rest/api/3/issue/{issue_key_or_id}/transitions"

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

        payload = {
            "transition": {"id": transition_id}
        }

        if fields:
            payload["fields"] = fields

        if comment:
            payload["update"] = {
                "comment": [
                    {
                        "add": {
                            "body": {
                                "type": "doc",
                                "version": 1,
                                "content": [
                                    {
                                        "type": "paragraph",
                                        "content": [
                                            {
                                                "text": comment,
                                                "type": "text"
                                            }
                                        ]
                                    }
                                ]
                            }
                        }
                    }
                ]
            }

        await self.post(url, payload, headers=headers)

    async def get_transitions(
        self,
        issue_key_or_id: str,
        expand: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get available transitions for an issue
        Args:
            issue_key_or_id: The issue ID or issue key
            expand: Use expand to include additional information
        Returns:
            Available transitions
        """
        await self.initialize()
        base_url = f"{BASE_URL}/{self.cloud_id}"
        url = f"{base_url}/rest/api/3/issue/{issue_key_or_id}/transitions"

        params = {}
        if expand:
            params["expand"] = expand

        return await self.get(url, params=params)

    async def assign_issue(
        self,
        issue_key_or_id: str,
        assignee_account_id: Optional[str] = None,
    ) -> None:
        """
        Assign an issue to a user
        Args:
            issue_key_or_id: The issue ID or issue key
            assignee_account_id: The account ID of the assignee (None to unassign)
        """
        await self.initialize()
        base_url = f"{BASE_URL}/{self.cloud_id}"
        url = f"{base_url}/rest/api/3/issue/{issue_key_or_id}/assignee"

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

        if assignee_account_id:
            payload = {"accountId": assignee_account_id}
        else:
            payload = {"accountId": None}  # Unassign

        await self.put(url, payload, headers=headers)

    async def get_dashboards(
        self,
        filter: Optional[str] = None,
        start_at: int = 0,
        max_results: int = 20,
    ) -> Dict[str, Any]:
        """
        Get dashboards accessible to the user
        Args:
            filter: Filter for dashboard names
            start_at: The index of the first dashboard to return
            max_results: The maximum number of dashboards to return
        Returns:
            List of dashboards
        """
        await self.initialize()
        base_url = f"{BASE_URL}/{self.cloud_id}"
        url = f"{base_url}/rest/api/3/dashboard"

        params = {
            "startAt": start_at,
            "maxResults": max_results
        }

        if filter:
            params["filter"] = filter

        return await self.get(url, params=params)

    async def get_users(
        self,
        account_id: Optional[str] = None,
        username: Optional[str] = None,
        start_at: int = 0,
        max_results: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Get users from JIRA
        Args:
            account_id: Filter by account ID
            username: Filter by username (deprecated in Cloud)
            start_at: The index of the first user to return
            max_results: The maximum number of users to return
        Returns:
            List of users
        """
        await self.initialize()
        base_url = f"{BASE_URL}/{self.cloud_id}"

        if account_id:
            url = f"{base_url}/rest/api/3/user"
            params = {"accountId": account_id}
            user = await self.get(url, params=params)
            return [user] if user else []
        else:
            url = f"{base_url}/rest/api/3/users/search"
            params = {
                "startAt": start_at,
                "maxResults": max_results
            }
            if username:
                params["username"] = username

            return await self.get(url, params=params)

    async def get_issue_types(
        self,
        project_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get issue types
        Args:
            project_id: Filter by project ID
        Returns:
            List of issue types
        """
        await self.initialize()
        base_url = f"{BASE_URL}/{self.cloud_id}"

        if project_id:
            url = f"{base_url}/rest/api/3/issue/createmeta/{project_id}/issuetypes"
        else:
            url = f"{base_url}/rest/api/3/issuetype"

        response = await self.get(url)

        # Handle different response formats
        if isinstance(response, list):
            return response
        else:
            return response.get("issueTypes", response.get("values", []))

@dataclass
class JiraUsernamePasswordConfig():
    """Configuration for JIRA REST client via username and password
    Args:
        base_url: The base URL of the JIRA instance
        username: The username to use for authentication
        password: The password to use for authentication
        ssl: Whether to use SSL
    """
    base_url: str
    username: str
    password: str
    ssl: bool = False

    def create_client(self) -> JiraRESTClientViaUsernamePassword:
        return JiraRESTClientViaUsernamePassword(self.username, self.password, "Basic")

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

@dataclass
class JiraTokenConfig():
    """Configuration for JIRA REST client via token
    Args:
        base_url: The base URL of the JIRA instance
        token: The token to use for authentication
        ssl: Whether to use SSL
    """
    base_url: str
    token: str
    ssl: bool = False

    def create_client(self) -> JiraRESTClientViaToken:
        return JiraRESTClientViaToken(self.token)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

@dataclass
class JiraApiKeyConfig():
    """Configuration for JIRA REST client via API key
    Args:
        base_url: The base URL of the JIRA instance
        email: The email to use for authentication
        api_key: The API key to use for authentication
        ssl: Whether to use SSL
    """
    base_url: str
    email: str
    api_key: str
    ssl: bool = False

    def create_client(self) -> JiraRESTClientViaApiKey:
        return JiraRESTClientViaApiKey(self.email, self.api_key)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

class JiraClient(IClient):
    """Builder class for JIRA clients with different construction methods"""

    def __init__(self, client: object) -> None:
        """Initialize with a JIRA client object"""
        self.client = client

    def get_client(self) -> object:
        """Return the JIRA client object"""
        return self.client

    @classmethod
    def build_with_client(cls, client: object) -> 'JiraClient':
        """
        Build JiraClient with an already authenticated client
        Args:
            client: Authenticated JIRA client object
        Returns:
            JiraClient instance
        """
        return cls(client)

    @classmethod
    def build_with_config(cls, config: JiraUsernamePasswordConfig | JiraTokenConfig | JiraApiKeyConfig) -> 'JiraClient':
        """
        Build JiraClient with configuration (placeholder for future OAuth2/enterprise support)
        Args:
            config: JiraConfigBase instance
        Returns:
            JiraClient instance with placeholder implementation
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger,
        config_service: ConfigurationService,
        arango_service,
        org_id: str,
        user_id: str,
    ) -> 'JiraClient':
        """
        Build JiraClient using configuration service and arango service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
            arango_service: ArangoDB service instance
            org_id: Organization ID
            user_id: User ID
        Returns:
            JiraClient instance
        """
        return cls(client=None)
