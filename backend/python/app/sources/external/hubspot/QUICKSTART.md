# 🚀 Quick Start Guide - HubSpot OAuth Testing

Follow these steps to set up and test your HubSpot integration:

## 📋 Prerequisites

You need:
1. A temporary email (from https://temp-mail.org/en/)
2. 10 minutes of your time

## 🎯 Step-by-Step Instructions

### 1️⃣ Create HubSpot Developer Account

```bash
# Open in browser:
https://app.hubspot.com/signup-hubspot/developers

# Use email from temp-mail.org
# Complete signup and verify email
```

### 2️⃣ Create HubSpot App

```bash
# Go to:
https://app.hubspot.com/developers

# Click "Create app"
# Fill in:
  Name: HubSpot Test Integration
  Description: Testing API

# Go to "Auth" tab:
  Redirect URL: http://localhost:8000/callback
  
# Go to "Scopes" section, select:
  ✅ crm.objects.contacts.read
  ✅ crm.objects.contacts.write
  ✅ crm.objects.companies.read
  ✅ crm.objects.companies.write
  ✅ crm.objects.deals.read
  ✅ crm.objects.deals.write
  ✅ crm.objects.tickets.read
  ✅ crm.objects.tickets.write

# Copy:
  - Client ID
  - Client Secret
```

### 3️⃣ Run Automated Setup

```bash
cd /Users/ayusharyakashyap/Desktop/Internships/Pipeshub/pipeshub-ai/backend/python/app/sources/external/hubspot

# Run the setup script (it does everything for you!)
./setup.sh
```

**The script will:**
- ✅ Check Python version
- ✅ Create/activate virtual environment
- ✅ Install all dependencies
- ✅ Create .env file
- ✅ Ask you to add credentials
- ✅ Run OAuth flow automatically
- ✅ Save tokens to files

### 4️⃣ Add Your Credentials

When the script asks, edit `.env`:

```bash
nano .env  # or use your preferred editor
```

Update with your credentials from step 2:
```
HUBSPOT_CLIENT_ID=paste_your_client_id_here
HUBSPOT_CLIENT_SECRET=paste_your_client_secret_here
HUBSPOT_REDIRECT_URI=http://localhost:8000/callback
```

Save and close (Ctrl+X, Y, Enter for nano)

### 5️⃣ Complete OAuth Flow

The script will:
1. Open your browser automatically
2. Ask you to authorize the app
3. Click "Connect app"
4. You'll be redirected back (it captures the code automatically)
5. Tokens will be saved to `hubspot_token.txt` and `.env`

### 6️⃣ Test the Integration!

```bash
# Load the token
export HUBSPOT_ACCESS_TOKEN="$(grep 'Access Token:' hubspot_token.txt | cut -d' ' -f3)"

# Run the example script
source ../../../venv/bin/activate
PYTHONPATH=../.. python3 example.py
```

You should see:
```
🚀 Starting HubSpot Data Source Examples
==================================================

📡 Initializing HubSpot client...
✅ HubSpot client initialized
📊 Creating HubSpot data source...
✅ HubSpot data source created

========== CONTACTS ==========

1. Getting contacts...
✅ Retrieved 5 contacts

2. Creating a new contact...
✅ Created contact with ID: 12345

... (more output)

==================================================
✅ All examples completed successfully!
```

## 🎉 Success!

You've now:
- ✅ Set up OAuth authentication
- ✅ Generated access tokens
- ✅ Tested all 22 API methods
- ✅ Verified the integration works with real HubSpot API

## 📸 Take Screenshots!

Take screenshots of:
1. The successful OAuth authorization
2. The example.py output showing successful API calls
3. Post them as a comment on your PR to show it's fully tested!

## 🔧 Alternative: Manual OAuth (if setup.sh fails)

If the automated script doesn't work:

```bash
# 1. Install dependencies manually
cd /Users/ayusharyakashyap/Desktop/Internships/Pipeshub/pipeshub-ai/backend/python
python3.11 -m venv venv
source venv/bin/activate
pip install -r app/sources/external/hubspot/requirements.txt

# 2. Create .env manually
cd app/sources/external/hubspot
cp .env.template .env
nano .env  # add your credentials

# 3. Run OAuth manually
python3 hubspot_oauth.py

# 4. Test
export HUBSPOT_ACCESS_TOKEN="$(grep 'Access Token:' hubspot_token.txt | cut -d' ' -f3)"
PYTHONPATH=../.. python3 example.py
```

## 🆘 Troubleshooting

### "Module not found" error
```bash
source ../../../venv/bin/activate
pip install -r requirements.txt
```

### "Invalid client_id" error
- Check `.env` file has correct credentials
- No extra spaces or quotes
- Copy directly from HubSpot developer portal

### Browser doesn't open
- Copy the URL from terminal
- Paste in browser manually
- Continue the flow

## 📚 Resources

- Full setup guide: `OAUTH_SETUP.md`
- OAuth script: `hubspot_oauth.py`
- Example usage: `example.py`
- HubSpot docs: https://developers.hubspot.com/docs/api/oauth-quickstart-guide
