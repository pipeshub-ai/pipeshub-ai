# S3 Connector
## Overview
[`Amazon S3`](https://aws.amazon.com/s3/) is an object storage service offering industry‑leading scalability, data availability, security, and performance.

<br></br>
<br></br>
<div align="center">
  <img src="https://raw.githubusercontent.com/pipeshub-ai/documentation/refs/heads/main/logo/aws-s3.png" alt="Amazon S3 Logo" width="180"/>
</div>


<br></br>
## PipesHub Actions 

It has three distinct layers:
- [`S3 Client`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/client/s3/s3.py) - creates S3 client.
<!--([`Local`](/backend/python/app/sources/client/s3/s3.py))-->

- [`S3 APIs`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/s3/s3.py) - provides methods to connect to S3 APIs.
<!--([`Local`](/backend/python/app/sources/external/s3/s3.py))-->

- [`S3 Actions`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/s3/s3.py) - actions that AI agents can do on S3 (marked with the `@tool` decorator)
<!--([`Local`](/backend/python/app/agents/actions/s3/s3.py))-->

<br></br>
### Supported Actions
-----
Here's what's available out of the box:
| Tool | What It Does | Parameters |
|------|---------------|------------|
| `list_buckets` | List S3 buckets | - |
| `create_bucket` | Create a bucket | `bucket_name`, `region (Optional)` |
| `delete_bucket` | Delete a bucket | `bucket_name` |
| `list_objects` | List objects in a bucket | `bucket_name`, `prefix (Optional)`, `max_keys (Optional)`, `marker (Optional)` |
| `get_object` | Get object | `bucket_name`, `key` |
| `put_object` | Upload object | `bucket_name`, `key`, `body`, `content_type (Optional)` |
| `delete_object` | Delete object | `bucket_name`, `key` |
| `copy_object` | Copy object | `source_bucket`, `source_key`, `dest_bucket`, `dest_key` |

**Response:** Every tool returns two things - a boolean indicating success or failure, and a JSON string with the actual data or error details.

<br></br>
### How to expose new S3 action to Agent
-----
#### 1. Go to [`S3 Data Source`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/s3/s3.py)
Find the operation (method) you want to expose to the PipesHub Agent. For example, to list buckets:
```python
async def list_buckets(self) -> S3Response:
    ...
```

#### 2. Add the tool in this [`S3 Tool`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/s3/s3.py) like below:
```python
@tool(
    app_name="s3",
    tool_name="list_buckets",
    description="List S3 buckets",
    parameters=[]
)
def list_buckets(self) -> Tuple[bool, str]:
    """List S3 buckets"""
    """
    Returns:
        Tuple[bool, str]: True if successful, False otherwise
    """
    try:
        # Use S3DataSource method
        response = self._run_async(self.client.list_buckets())

        if response.success:
            return True, response.to_json()
        else:
            return False, response.to_json()
    except Exception as e:
        logger.error(f"Error listing buckets: {e}")
        return False, json.dumps({"error": str(e)})
```

#### 3. How to decorate the method
- `app_name` - App Name, e.g. `s3`
- `tool_name` - intent of the action, e.g. `list_objects`
- `description` - what it does in details, must be descriptive for agent to read
- `parameters` - parameters with type and purpose
- `returns` - expected response type


