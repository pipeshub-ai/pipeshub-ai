# Jira Connector
## Overview
[`Jira`](https://www.atlassian.com/software/jira) is a work management tool for agile teams to plan, track, and ship software. It supports project planning, issue tracking, and reporting with powerful APIs.

<br></br>
<br></br>
<div align="center">
  <img src="https://raw.githubusercontent.com/pipeshub-ai/documentation/refs/heads/main/logo/jira.png" alt="Jira Logo" width="200"/>
</div>


<br></br>
## PipesHub Actions 

It has three distinct layers:
- [`Jira Client`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/client/jira/jira.py) - creates Jira client.
<!--([`Local`](/backend/python/app/sources/client/jira/jira.py))-->

- [`Jira APIs`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/jira/jira.py) - provides methods to connect to Jira APIs.
<!--([`Local`](/backend/python/app/sources/external/jira/jira.py))-->

- [`Jira Actions`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/jira/jira.py) - actions that AI agents can do on Jira (marked with the `@tool` decorator)
<!--([`Local`](/backend/python/app/agents/actions/jira/jira.py))-->

<br></br>
### Supported Actions
-----
Here's what's available out of the box:
| Tool | What It Does | Parameters |
|------|---------------|------------|
| `validate_connection` | Validate Jira connection and diagnostics | - |
| `convert_text_to_adf` | Convert plain text to ADF | `text` |
| `create_issue` | Create a new issue | `project_key`, `summary`, `issue_type_name`, `description (optional)`, `assignee_account_id (optional)`, `assignee_query (optional)`, `priority_name (optional)`, `labels (optional)`, `components (optional)` |
| `get_projects` | Get all projects | - |
| `get_project` | Get project details | `project_key` |
| `get_issues` | Get issues for a project | `project_key` |
| `get_issue` | Get a specific issue | `issue_key` |
| `search_issues` | Search issues via JQL | `jql` |
| `add_comment` | Add a comment to issue | `issue_key`, `comment` |
| `get_comments` | List comments on issue | `issue_key` |
| `search_users` | Search users | `query`, `max_results (optional)` |
| `get_assignable_users` | Get assignable users | `project_key`, `query (optional)`, `max_results (optional)` |
| `get_project_metadata` | Get project metadata | `project_key` |

**Response:** Every tool returns two things - a boolean indicating success or failure, and a JSON string with the actual data or error details.

<br></br>
### How to expose new Jira action to Agent
-----
#### 1. Go to [`Jira Data Source`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/jira/jira.py)
Find the operation (method) you want to expose to the PipesHub Agent. For example, to get an issue:
```python
async def get_issue(self, issueIdOrKey: str) -> HTTPResponse:
    ...
```

#### 2. Add the tool in this [`Jira Tool`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/jira/jira.py) like below:
```python
@tool(
    app_name="jira",
    tool_name="get_issue",
    description="Get a specific JIRA issue",
    parameters=[
        ToolParameter(name="issue_key", type=ParameterType.STRING, description="Issue key"),
    ],
    returns="Issue details"
)
def get_issue(self, issue_key: str) -> Tuple[bool, str]:
    resp = self._run_async(self.client.get_issue(issueIdOrKey=issue_key))
    return self._handle_response(resp, "Issue fetched successfully")
```

#### 3. How to decorate the method
- `app_name` - App Name, e.g. `jira`
- `tool_name` - intent of the action, e.g. `get_issue`
- `description` - what it does in details, must be descriptive for agent to read
- `parameters` - parameters with type and purpose
- `returns` - expected response type


