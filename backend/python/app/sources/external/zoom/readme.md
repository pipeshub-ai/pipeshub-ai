# 🧩 Zoom Integration for PipesHub

This integration enables PipesHub to interact with the Zoom REST API using Server-to-Server OAuth.
It provides a clean client interface and data source for retrieving user information, listing meetings, and creating meetings programmatically.

## ⚙️ Setup & Configuration

### 1️⃣ Prerequisites

- Python 3.10 or above
- httpx, requests, and python-dotenv libraries
- Active Zoom Developer Account
- A Server-to-Server OAuth App created on the Zoom Marketplace

### 2️⃣ Create Your Zoom App

1. Visit the [Zoom App Marketplace](https://marketplace.zoom.us/).
2. Click **Build App** → **Server-to-Server OAuth**.
3. Copy the following credentials:
   - Account ID
   - Client ID
   - Client Secret
4. Under **Scopes**, enable at least:
   - `user:read:admin`
   - `meeting:read:admin`
   - `meeting:write:admin`
5. Activate your app.

### 3️⃣ Export Your Credentials

In your terminal (Git Bash), run the following commands to set up environment variables:
```bash
export ZOOM_ACCOUNT_ID="your_account_id"
export ZOOM_CLIENT_ID="your_client_id"
export ZOOM_CLIENT_SECRET="your_client_secret"
```

### 4️⃣ Generate an Access Token

Run the provided script to generate a new token securely:
```bash
python generate_token.py
```

If successful, you'll see a JSON response like:
```json
{
  "access_token": "eyJzdiI6IjAwMDAwMiIsImFsZyI6...",
  "token_type": "bearer",
  "expires_in": 3599
}
```

Now export your access token:
```bash
export ZOOM_ACCESS_TOKEN="paste_your_access_token_here"
```

### 5️⃣ Run the Example Script

Execute the example to test your integration:
```bash
python -m app.sources.external.zoom.example
```

#### ✅ Expected Output:
```
Fetching user info:
{
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
  "join_url": "https://us04web.zoom.us/j/75602141985?pwd=..."
}
```

If you see similar output, your Zoom integration is working perfectly 🎉

## 📂 File Structure
```
backend/
 └── python/
      ├── app/
      │    ├── sources/
      │    │    ├── client/
      │    │    │    └── zoom/
      │    │    │         └── zoom.py          # Zoom client using HTTPClient
      │    │    └── external/
      │    │         └── zoom/
      │    │              ├── zoom.py          # ZoomDataSource implementation
      │    │              ├── example.py       # Example script
      │    │              └── readme.md        # This file
      │    └── config/
      │         └── configuration_service.py
      └── code-generator/
           └── zoom.py                         # Zoom code generator script
```

## 🧠 Implementation Details

### 🔹 ZoomClient

A thin wrapper built on the in-house HTTPClient class for secure and reusable Zoom API communication.

**Key Features:**
- Bearer token authentication
- Asynchronous HTTP requests via httpx
- JSON decoding with error handling

### 🔹 ZoomDataSource

High-level interface built on top of ZoomClient to expose core Zoom APIs:

| Method | Endpoint | Description |
|--------|----------|-------------|
| `get_user_info(user_id)` | `/users/{user_id}` | Retrieve user profile |
| `list_meetings(user_id)` | `/users/{user_id}/meetings` | List all meetings for a user |
| `create_meeting(user_id, data)` | `/users/{user_id}/meetings` | Create a new meeting |

### 🔹 generate_token.py

Utility script to securely generate OAuth access tokens using:
- `ZOOM_ACCOUNT_ID`
- `ZOOM_CLIENT_ID`
- `ZOOM_CLIENT_SECRET`

## 🔒 Security Practices

- No credentials or tokens are hardcoded.
- All secrets are loaded from environment variables.
- Never commit your access tokens or credentials to Git.
- Access tokens expire automatically — regenerate them using `generate_token.py`.

## 🧩 Testing Checklist

| Test | Expected Result |
|------|-----------------|
| Fetch user info | ✅ Returns valid user JSON |
| List meetings | ✅ Returns empty or populated meeting list |
| Create meeting | ✅ Returns join_url and meeting details |
| Invalid token | ⚠️ Returns "Invalid access token" error |

## 🧾 Example Commands Recap
```bash
# Set credentials
export ZOOM_ACCOUNT_ID="your_account_id"
export ZOOM_CLIENT_ID="your_client_id"
export ZOOM_CLIENT_SECRET="your_client_secret"

# Generate access token
python generate_token.py

# Set access token
export ZOOM_ACCESS_TOKEN="your_generated_token"

# Run example
python -m app.sources.external.zoom.example
```

## 💬 Notes for Reviewers

- The integration uses PipesHub's in-built HTTPClient (similar to Confluence).
- ZoomClient and ZoomDataSource follow the same class structure as other clients.
- Ruff lint checks (`ruff check --fix`) pass successfully.
- All changes follow the official PipesHub data source generation playbook.

## 🧩 Credits

- **Contributor:** Siddhant Roy 
- **Mail ID:** roysiddhant2003@gmail.com 
- **Integration:** Zoom API (Server-to-Server OAuth)
- **Tested APIs:** User Info, Meetings, Meeting Creation
- **Framework:** PipesHub Python Backend