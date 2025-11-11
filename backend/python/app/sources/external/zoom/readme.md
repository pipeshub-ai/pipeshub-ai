#  Zoom Integration for PipesHub

This module adds integration with **Zoom** using the official REST APIs via a **Server-to-Server OAuth** authentication flow.

It includes:
- `ZoomClient` — an HTTP client handling Zoom API authentication and requests.
- `ZoomDataSource` — the data source layer for interacting with Zoom REST APIs.
- `example.py` — demonstrates how to use both to fetch user info, list meetings, and create a meeting.

---

##  Setup Instructions

### 1. Create a Server-to-Server OAuth App

1. Go to [Zoom Marketplace](https://marketplace.zoom.us/)
2. Click **Develop → Build App → Server-to-Server OAuth**
3. Fill in the required information and activate your app.
4. Copy your credentials:
   - **Account ID**
   - **Client ID**
   - **Client Secret**

---

### 2. Add Required Scopes

Under **Scopes**, click **+ Add Scopes**, then add the following permissions:

| Scope | Purpose |
|-------|----------|
| `user:read:admin` | Read user info |
| `meeting:read:admin` | View all user meetings |
| `meeting:write:admin` | Create and edit meetings |

Once done, click **Done → Continue → Save → Activate**.

---

### 3. Generate an Access Token

Run this script in Python (replace with your own credentials):
```python
import requests, base64

account_id = "YOUR_ACCOUNT_ID"
client_id = "YOUR_CLIENT_ID"
client_secret = "YOUR_CLIENT_SECRET"

auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
url = f"https://zoom.us/oauth/token?grant_type=account_credentials&account_id={account_id}"
headers = {"Authorization": f"Basic {auth}"}

response = requests.post(url, headers=headers)
print(response.json())
```

Copy the `"access_token"` value from the response.

Tokens are valid for about 1 hour.

---

### 4. Run the Example Script

Paste your token in `example.py`:
```python
access_token = "YOUR_ACCESS_TOKEN"
```

Then run:
```bash
python -m app.sources.external.zoom.example
```

---

##  Example Output
```json
Fetching user info:
{
  "id": "me",
  "first_name": "Siddhant",
  "email": "roysiddhant2003@gmail.com"
}

Listing meetings:
{
  "meetings": []
}

Creating test meeting:
{
  "topic": "Demo Meeting",
  "join_url": "https://us04web.zoom.us/j/..."
}
```

---

##  Technologies Used

- Python 3.11+
- httpx — Async HTTP client for REST calls
- Ruff — Linter and code formatter
- requests — Used for token generation

---

##  File Structure
```bash
zoom/
├── zoom.py         # ZoomDataSource (main logic)
├── example.py      # Demonstration of Zoom API usage
└── README.md       # Documentation for setup and usage
```

---

##  Validation

- ✅ Successfully tested with live Zoom REST API
- ✅ Fetched user details successfully
- ✅ Created and listed meetings via API
- ✅ Passed all Ruff checks (`ruff check --fix`)
- ✅ Ready for merge into main PipesHub backend

---

##  Contributor

**Siddhant Roy**  
 Email: roysiddhant2003@gmail.com  
 GitHub: [https://github.com/roy-sid]

---

##  Notes

- This integration uses the **Server-to-Server OAuth** model (no user consent screen).
- Always regenerate your token before running `example.py`, as Zoom access tokens expire every 60 minutes.
- If you encounter `invalid_client`, double-check:
  - You're using the **Server-to-Server OAuth** app type
  - The `account_id`, `client_id`, and `client_secret` are from the same app
  - The app is activated in Zoom Marketplace

---

 This integration enables PipesHub to securely communicate with Zoom APIs for data synchronization, meeting management, and workflow automation.