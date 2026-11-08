# Confluence Connector
## Overview
[`Confluence`](https://www.atlassian.com/software/confluence) is a team workspace where knowledge and collaboration meet. It lets teams create, organize, and discuss work in one place with powerful page editing and space organization.

<br></br>
<br></br>
<div align="center">
  <img src="https://raw.githubusercontent.com/pipeshub-ai/documentation/refs/heads/main/logo/confluence.png" alt="Confluence Logo" width="200"/>
</div>


<br></br>
## PipesHub Actions 

It has three distinct layers:
- [`Confluence Client`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/client/confluence/confluence.py) - creates Confluence client.
<!--([`Local`](/backend/python/app/sources/client/confluence/confluence.py))-->

- [`Confluence APIs`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/confluence/confluence.py) - provides methods to connect to Confluence APIs.
<!--([`Local`](/backend/python/app/sources/external/confluence/confluence.py))-->

- [`Confluence Actions`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/confluence/confluence.py) - actions that AI agents can do on Confluence (marked with the `@tool` decorator)
<!--([`Local`](/backend/python/app/agents/actions/confluence/confluence.py))-->

<br></br>
### Supported Actions
-----
Here's what's available out of the box:
| Tool | What It Does | Parameters |
|------|---------------|------------|
| `create_page` | Create a page | `space_id`, `page_title`, `page_content`, `attachment_record_ids (optional)`, `attachment_display (optional)` |
| `update_page` | Update a page's title and/or content | `page_id`, `page_title (optional)`, `page_content (optional)`, `attachment_record_ids (optional)`, `attachment_display (optional)` |
| `add_attachments` | Attach files to an existing page | `page_id`, `attachment_record_ids`, `attachment_display (optional)` |
| `comment_on_page` | Comment on a page | `page_id`, `comment_text`, `parent_comment_id (optional)`, `attachment_record_ids (optional)`, `attachment_display (optional)` |
| `get_page_content` | Get page content by ID | `page_id` |
| `get_pages_in_space` | Get all pages in a space | `space_id` |
| `update_page_title` | Update the title of a page | `page_id`, `new_title` |
| `get_child_pages` | Get child pages of a page | `page_id` |
| `search_pages` | Search pages by title (optionally within a space) | `title`, `space_id (Optional)` |
| `get_spaces` | Get all spaces accessible to the user | - |
| `get_space` | Get a space by ID | `space_id` |
| `get_page_versions` | Get versions of a page | `page_id` |
| `invite_user` | Invite a user by email | `email` |

**Response:** Every tool returns two things - a boolean indicating success or failure, and a JSON string with the actual data or error details.

### Attachments
-----
`create_page`, `update_page`, `add_attachments` and the comment tool (Confluence Cloud and Confluence Data Center) take `attachment_record_ids`: PipesHub record IDs of files the user shared in chat or Slack, artifacts, or knowledge base files. Each file is checked against the user's access, then uploaded to the page with the v1 endpoint `POST /rest/api/content/{id}/child/attachment` (v2 has no upload) ([`attachments.py`](./attachments.py)).

- Files are checked before anything is written. If none can be sent, the page is not created.
- The page is created first and the files attached after. If some uploads fail, the tool still succeeds and returns `"partial": true` with `attachments.failed`.
- An existing attachment is never overwritten or versioned: if the page already has `diagram.png`, the new file is stored as `diagram (2).png`.
- `attachment_display: "inline"` also shows the files in the page: images with `<ac:image>`, other files as `<ac:link>`. Write `{{attachment:<record id or file name>}}` in `page_content` where a file should go; files without a placeholder go at the end. In a comment, files are attached to the page and the comment names them.
- Limits: 10 files and 25 MiB per call, plus the site's own attachment limit.
- OAuth: uploads need the `write:attachment:confluence` scope. PipesHub requests it only when the Confluence toolset's OAuth app has **Allow file uploads** set to Yes (default No), because Atlassian refuses sign-in when an app is asked for a scope it doesn't have. To enable: add the scope to the app in the Atlassian developer console, set Allow file uploads to Yes, and reconnect. API tokens work without it.

<br></br>
### How to expose new Confluence action to Agent
-----
#### 1. Go to [`Confluence Data Source`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/confluence/confluence.py)
Find the operation (method) you want to expose to the PipesHub Agent. For example, to get page content by ID:
```python
async def get_page_by_id(self, id: int, body_format: str) -> HTTPResponse:
    ...
```

#### 2. Add the tool in this [`Confluence Tool`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/confluence/confluence.py) like below:
```python
@tool(
    app_name="confluence",
    tool_name="get_page_content",
    description="Get the content of a page in Confluence",
    parameters=[
        ToolParameter(
            name="page_id",
            type=ParameterType.STRING,
            description="The ID of the page to get"
        ),
    ],
    returns="JSON with page content and metadata"
)
def get_page_content(self, page_id: str) -> Tuple[bool, str]:
    """Get the content of a page in Confluence.
    Args:
        page_id: The ID of the page
    Returns:
        Tuple of (success, json_response)
    """
    try:
        # Convert page_id to int with proper error handling
        try:
            page_id_int = int(page_id)
        except ValueError:
            return False, json.dumps({"error": f"Invalid page_id format: '{page_id}' is not a valid integer"})

        response = self._run_async(
            self.client.get_page_by_id(
                id=page_id_int,
                body_format="storage"
            )
        )
        return self._handle_response(response, "Page content fetched successfully")

    except Exception as e:
        logger.error(f"Error getting page content: {e}")
        return False, json.dumps({"error": str(e)})
```

#### 3. How to decorate the method
- `app_name` - App Name, e.g. `confluence`
- `tool_name` - intent of the action, e.g. `get_page_content`
- `description` - what it does in details, must be descriptive for agent to read
- `parameters` - parameters with type and purpose
- `returns` - expected response type
