# HubSpot OAuth Setup Guide

This guide will help you set up OAuth authentication and test the HubSpot integration.

## Quick Setup (5 minutes)

### Step 1: Create HubSpot Developer Account

1. **Get a temporary email** (optional but recommended for testing):
   - Go to https://temp-mail.org/en/
   - Copy the generated email address

2. **Sign up for HubSpot**:
   - Visit: https://app.hubspot.com/signup-hubspot/developers
   - Use your temp email or personal email
   - Complete the signup process
   - Verify your email

### Step 2: Create a HubSpot App

1. **Go to Apps**:
   - Visit: https://app.hubspot.com/developers
   - Click **"Create app"**

2. **Configure Basic Info**:
   - App name: `HubSpot Test Integration`
   - Description: `Testing HubSpot API integration`

3. **Configure Auth Settings**:
   - Go to **"Auth"** tab
   - Set **Redirect URL**: `http://localhost:8000/callback`
   - Click **"Add"**

4. **Set Required Scopes**:
   - In **"Scopes"** section, select:
     - ✅ `crm.objects.contacts.read`
     - ✅ `crm.objects.contacts.write`
     - ✅ `crm.objects.companies.read`
     - ✅ `crm.objects.companies.write`
     - ✅ `crm.objects.deals.read`
     - ✅ `crm.objects.deals.write`
     - ✅ `crm.objects.tickets.read`
     - ✅ `crm.objects.tickets.write`

5. **Copy Credentials**:
   - Copy **Client ID**
   - Copy **Client Secret**
   - Save them for next step

### Step 3: Set Up Local Environment

```bash
# Navigate to hubspot directory
cd /Users/ayusharyakashyap/Desktop/Internships/Pipeshub/pipeshub-ai/backend/python/app/sources/external/hubspot

# Activate virtual environment
source ../../../venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Create .env file from template
cp .env.template .env

# Edit .env file and add your credentials
nano .env  # or use your preferred editor
```

Update `.env` with your credentials:
```
HUBSPOT_CLIENT_ID=your_client_id_from_step2
HUBSPOT_CLIENT_SECRET=your_client_secret_from_step2
HUBSPOT_REDIRECT_URI=http://localhost:8000/callback
```

### Step 4: Get OAuth Tokens

```bash
# Run the OAuth flow
python3 hubspot_oauth.py
```

This will:
1. Open your browser to HubSpot's authorization page
2. Ask you to grant permissions
3. Capture the authorization code
4. Exchange it for access & refresh tokens
5. Save tokens to `hubspot_token.txt` and `.env`

### Step 5: Test the Integration

```bash
# Set the access token in your environment
export HUBSPOT_ACCESS_TOKEN="$(grep 'Access Token:' hubspot_token.txt | cut -d' ' -f3)"

# Run the example script
PYTHONPATH=../.. python3 example.py
```

This will test:
- ✅ Creating/reading/updating/deleting contacts
- ✅ Creating/reading/updating/deleting companies
- ✅ Creating/reading/updating/deleting deals
- ✅ Creating/reading/updating/deleting tickets
- ✅ Search functionality

## Troubleshooting

### Issue: "Module not found" error
```bash
# Make sure you're in venv
source ../../../venv/bin/activate

# Install requirements
pip install -r requirements.txt
```

### Issue: "Invalid client_id" error
- Double-check your Client ID in `.env`
- Make sure there are no extra spaces
- Verify the app exists in your HubSpot developer account

### Issue: "Redirect URI mismatch" error
- Ensure redirect URI in `.env` matches HubSpot app settings
- Should be exactly: `http://localhost:8000/callback`

### Issue: "Insufficient permissions" error
- Check that all required scopes are enabled in your HubSpot app
- You may need to re-authorize after adding scopes

## Files Created

- ✅ `requirements.txt` - Python dependencies
- ✅ `.env.template` - Environment variable template
- ✅ `.env` - Your credentials (gitignored)
- ✅ `hubspot_oauth.py` - OAuth token generator
- ✅ `hubspot_token.txt` - Generated tokens (gitignored)
- ✅ `OAUTH_SETUP.md` - This guide

## Using Tokens in Your Code

```python
import os
from dotenv import load_dotenv
from app.sources.client.hubspot.hubspot_ import HubSpotClient, HubSpotTokenConfig
from app.sources.external.hubspot.hubspot_ import HubSpotDataSource

# Load tokens
load_dotenv()
token = os.getenv("HUBSPOT_ACCESS_TOKEN")

# Initialize client
client = HubSpotClient.build_with_config(HubSpotTokenConfig(token=token))
data_source = HubSpotDataSource(client)

# Use the API
contacts = await data_source.get_contacts(limit=10)
```

## Token Refresh

Access tokens expire after a few hours. To refresh:

```python
import requests

refresh_token = os.getenv("HUBSPOT_REFRESH_TOKEN")
client_id = os.getenv("HUBSPOT_CLIENT_ID")
client_secret = os.getenv("HUBSPOT_CLIENT_SECRET")

response = requests.post(
    "https://api.hubapi.com/oauth/v1/token",
    data={
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }
)

new_tokens = response.json()
# Update .env with new_tokens['access_token']
```

## Resources

- HubSpot OAuth Guide: https://developers.hubspot.com/docs/api/oauth-quickstart-guide
- HubSpot API Reference: https://developers.hubspot.com/docs/api/overview
- CRM API Docs: https://developers.hubspot.com/docs/api/crm/understanding-the-crm
