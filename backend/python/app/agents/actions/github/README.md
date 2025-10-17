# GitHub Connector
## Overview
[`GitHub`](https://github.com/) is a platform for hosting code, collaboration, and CI/CD. It provides repositories, issues, pull requests, and automation via workflows and APIs.

<br></br>
<br></br>
<div align="center">
  <img src="https://raw.githubusercontent.com/pipeshub-ai/documentation/refs/heads/main/logo/github.png" alt="GitHub Logo" width="180"/>
</div>


<br></br>
## PipesHub Actions 

It has three distinct layers:
- [`GitHub Client`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/client/github/github.py) - creates GitHub client.
<!--([`Local`](/backend/python/app/sources/client/github/github.py))-->

- [`GitHub APIs`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/github/github_.py) - provides methods to connect to GitHub APIs.
<!--([`Local`](/backend/python/app/sources/external/github/github_.py))-->

- [`GitHub Actions`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/github/github.py) - actions that AI agents can do on GitHub (marked with the `@tool` decorator)
<!--([`Local`](/backend/python/app/agents/actions/github/github.py))-->

<br></br>
### Supported Actions
-----
Here's what's available out of the box:
| Tool | What It Does | Parameters |
|------|---------------|------------|
| `create_repository` | Create a repository | `name`, `private (Optional)`, `description (Optional)`, `auto_init (Optional)` |
| `get_repository` | Get repository details | `owner`, `repo` |
| `update_repository` | Get repository details (placeholder for updates) | `owner`, `repo` |
| `delete_repository` | Not supported via SDK (permission note) | `owner`, `repo` |
| `create_issue` | Create an issue | `owner`, `repo`, `title`, `body (Optional)`, `assignees (Optional)`, `labels (Optional)` |
| `get_issue` | Get issue details | `owner`, `repo`, `number` |
| `close_issue` | Close an issue | `owner`, `repo`, `number` |
| `create_pull_request` | Create a pull request | `owner`, `repo`, `title`, `head`, `base`, `body (Optional)`, `draft (Optional)` |
| `get_pull_request` | Get pull request details | `owner`, `repo`, `number` |
| `merge_pull_request` | Merge a pull request | `owner`, `repo`, `number`, `commit_message (Optional)`, `merge_method (Optional)` |
| `search_repositories` | Search repositories | `query` |

**Response:** Every tool returns two things - a boolean indicating success or failure, and a JSON string with the actual data or error details.

<br></br>
### How to expose new GitHub action to Agent
-----
#### 1. Go to [`GitHub Data Source`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/github/github_.py)
Find the operation (method) you want to expose to the PipesHub Agent. For example, to get a repo:
```python
def get_repo(self, owner: str, repo: str) -> GitHubResponse[Repository]:
    ...
```

#### 2. Add the tool in this [`GitHub Tool`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/github/github.py) like below:
```python
@tool(
    app_name="github",
    tool_name="get_repository",
    description="Get details of a specific repository from GitHub",
    parameters=[
        ToolParameter(
            name="owner",
            type=ParameterType.STRING,
            description="The owner of the repository (username or organization) (required)",
        ),
        ToolParameter(
            name="repo",
            type=ParameterType.STRING,
            description="The name of the repository (required)",
        ),
    ],
    returns="JSON with repository details",
)
def get_repository(self, owner: str, repo: str) -> Tuple[bool, str]:
    """Get details of a specific repository from GitHub."""
    try:
        response = self._run_async(self.client.get_repo(owner=owner, repo=repo))
        return self._handle_response(response, "Repository fetched successfully")
    except Exception as e:
        logger.error(f"Error getting repository: {e}")
        return False, json.dumps({"error": str(e)})
```

#### 3. How to decorate the method
- `app_name` - App Name, e.g. `github`
- `tool_name` - intent of the action, e.g. `create_issue`
- `description` - what it does in details, must be descriptive for agent to read
- `parameters` - parameters with type and purpose
- `returns` - expected response type


