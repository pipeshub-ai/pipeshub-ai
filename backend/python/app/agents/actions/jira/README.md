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
| `create_issue` | Create a new issue | `project_key`, `summary`, `issue_type_name`, `description (optional)`, `assignee_account_id (optional)`, `assignee_query (optional)`, `priority_name (optional)`, `labels (optional)`, `components (optional)`, `attachment_record_ids (optional)`, `attachment_display (optional)` |
| `update_issue` | Update an existing issue | `issue_key`, any field to change, `attachment_record_ids (optional)`, `attachment_display (optional)` |
| `add_attachments` | Attach files to an existing issue | `issue_key`, `attachment_record_ids`, `attachment_display (optional)` |
| `get_projects` | Get all projects | - |
| `get_project` | Get project details | `project_key` |
| `get_issues` | Get issues for a project | `project_key` |
| `get_issue` | Get a specific issue | `issue_key` |
| `search_issues` | Search issues via JQL | `jql` |
| `add_comment` | Add a comment to issue | `issue_key`, `comment`, `attachment_record_ids (optional)`, `attachment_display (optional)` |
| `get_comments` | List comments on issue | `issue_key` |
| `search_users` | Search users | `query`, `max_results (optional)` |
| `get_assignable_users` | Get assignable users | `project_key`, `query (optional)`, `max_results (optional)` |
| `get_project_metadata` | Get project metadata | `project_key` |

**Response:** Every tool returns two things - a boolean indicating success or failure, and a JSON string with the actual data or error details.

### Attachments
-----
`create_issue`, `update_issue`, `add_comment` and `add_attachments` (Jira Cloud and Jira Data Center) take `attachment_record_ids`: PipesHub record IDs of files the user shared in chat or Slack, artifacts, or knowledge base files. Each file is checked against the user's access, then uploaded to the issue with `POST /rest/api/{3|2}/issue/{key}/attachments` ([`attachments.py`](./attachments.py)).

- Files are checked before anything is written. If none can be sent, or attachments are turned off on the site, the issue is not created.
- Jira can't take files in the create call, so the issue is created first and the files attached after. If some uploads fail, the tool still succeeds and returns `"partial": true` with `attachments.failed`, so the agent doesn't create the issue again.
- `attachment_display: "inline"` also puts the files in the description (or the comment). Write `{{attachment:<record id or file name>}}` where a file should go; files without a placeholder go at the end.
  - Jira Data Center: images show with `!name|thumbnail!`, other files link with `[^name]`.
  - Jira Cloud: links to the attachments. Jira Cloud can't show an API-uploaded image inside the text ([JRACLOUD-96384](https://jira.atlassian.com/browse/JRACLOUD-96384)).
- Limits: 10 files and 25 MiB per call, and the site's own per-file limit from `GET /rest/api/{3|2}/attachment/meta`.
- OAuth: the existing `write:jira-work` scope covers uploads.

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


