# GitLab Connector
## Overview
[`GitLab`](https://gitlab.com/) is a DevOps platform that provides source control, issues, and CI/CD in a single application. It enables collaboration across the software lifecycle.

<br></br>
<br></br>
<div align="center">
  <img src="https://raw.githubusercontent.com/pipeshub-ai/documentation/refs/heads/main/logo/gitlab.png" alt="GitLab Logo" width="180"/>
</div>


<br></br>
## PipesHub Actions 

It has three distinct layers:
- [`GitLab Client`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/client/gitlab/gitlab.py) - creates GitLab client.
<!--([`Local`](/backend/python/app/sources/client/gitlab/gitlab.py))-->

- [`GitLab APIs`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/gitlab/gitlab_.py) - provides methods to connect to GitLab APIs.
<!--([`Local`](/backend/python/app/sources/external/gitlab/gitlab_.py))-->

- [`GitLab Actions`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/gitlab/gitlab.py) - actions that AI agents can do on GitLab (marked with the `@tool` decorator)
<!--([`Local`](/backend/python/app/agents/actions/gitlab/gitlab.py))-->

<br></br>
### Supported Actions
-----
Here's what's available out of the box:
| Tool | What It Does | Parameters |
|------|---------------|------------|
| `create_project` | Create a project | `name`, `description (Optional)`, `visibility (Optional)`, `namespace_id (Optional)`, `initialize_with_readme (Optional)` |
| `get_project` | Get project details | `project_id` |
| `update_project` | Update a project | `project_id`, `name (Optional)`, `description (Optional)`, `visibility (Optional)` |
| `delete_project` | Delete a project | `project_id` |
| `create_issue` | Create an issue | `project_id`, `title`, `description (Optional)`, `assignee_ids (Optional)`, `labels (Optional)`, `milestone_id (Optional)` |
| `get_issue` | Get issue details | `project_id`, `issue_iid` |
| `update_issue` | Update an issue | `project_id`, `issue_iid`, `title (Optional)`, `description (Optional)`, `labels (Optional)`, `state_event (Optional)` |
| `delete_issue` | Delete an issue | `project_id`, `issue_iid` |
| `create_merge_request` | Create a merge request | `project_id`, `title`, `source_branch`, `target_branch`, `description (Optional)`, `assignee_id (Optional)`, `labels (Optional)` |
| `get_merge_request` | Get merge request details | `project_id`, `merge_request_iid` |
| `merge_merge_request` | Merge a merge request | `project_id`, `merge_request_iid`, `merge_when_pipeline_succeeds (Optional)`, `squash (Optional)` |
| `search_projects` | Search projects | `query` |

**Response:** Every tool returns two things - a boolean indicating success or failure, and a JSON string with the actual data or error details.

<br></br>
### How to expose new GitLab action to Agent
-----
#### 1. Go to [`GitLab Data Source`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/gitlab/gitlab_.py)
Find the operation (method) you want to expose to the PipesHub Agent. For example, to get a project:
```python
def get_project(self, project_id: Union[int, str]) -> GitLabResponse:
    ...
```

#### 2. Add the tool in this [`GitLab Tool`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/gitlab/gitlab.py) like below:
```python
@tool(
    app_name="gitlab",
    tool_name="get_project",
    description="Get details of a specific project from GitLab",
    parameters=[
        ToolParameter(
            name="project_id",
            type=ParameterType.STRING,
            description="The ID or path of the project (required)",
        ),
    ],
    returns="JSON with project details",
)
def get_project(self, project_id: str) -> Tuple[bool, str]:
    """Get details of a specific project from GitLab."""
    try:
        response = self._run_async(self.client.get_project(project_id=project_id))
        return self._handle_response(response, "Project fetched successfully")
    except Exception as e:
        logger.error(f"Error getting project: {e}")
        return False, json.dumps({"error": str(e)})
```

#### 3. How to decorate the method
- `app_name` - App Name, e.g. `gitlab`
- `tool_name` - intent of the action, e.g. `create_issue`
- `description` - what it does in details, must be descriptive for agent to read
- `parameters` - parameters with type and purpose
- `returns` - expected response type


