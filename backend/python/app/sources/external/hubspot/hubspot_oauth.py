#!/usr/bin/env python3
"""HubSpot OAuth Token Generator

This script helps you obtain OAuth tokens for HubSpot API access.
It will:
1. Start a local server
2. Open your browser to HubSpot's OAuth authorization page
3. Capture the authorization code
4. Exchange it for access and refresh tokens
5. Save tokens to hubspot_token.txt and .env file

Prerequisites:
    1. Create a HubSpot developer account at https://developers.hubspot.com/
    2. Create an app at https://app.hubspot.com/signup-hubspot/developers
    3. Set OAuth Redirect URL to: http://localhost:8000/callback
    4. Copy your Client ID and Client Secret
    5. Set them in .env file or export as environment variables

Usage:
    export HUBSPOT_CLIENT_ID="your-client-id"
    export HUBSPOT_CLIENT_SECRET="your-client-secret"
    python3 hubspot_oauth.py
"""

import os
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
CLIENT_ID = os.getenv("HUBSPOT_CLIENT_ID")
CLIENT_SECRET = os.getenv("HUBSPOT_CLIENT_SECRET")
REDIRECT_URI = os.getenv("HUBSPOT_REDIRECT_URI", "http://localhost:8000/callback")
PORT = 8000

# HubSpot OAuth URLs
AUTHORIZE_URL = "https://app.hubspot.com/oauth/authorize"
TOKEN_URL = "https://api.hubapi.com/oauth/v1/token"

# Scopes - adjust based on your needs
SCOPES = [
    "crm.objects.contacts.read",
    "crm.objects.contacts.write",
    "crm.objects.companies.read",
    "crm.objects.companies.write",
    "crm.objects.deals.read",
    "crm.objects.deals.write",
    "crm.objects.tickets.read",
    "crm.objects.tickets.write",
]

# Global variable to store the authorization code
auth_code = None


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Handle OAuth callback from HubSpot"""

    def log_message(self, format, *args):
        """Suppress default logging"""
        pass

    def do_GET(self):
        """Handle GET request from OAuth callback"""
        global auth_code

        # Parse the URL and query parameters
        parsed_path = urlparse(self.path)
        query_params = parse_qs(parsed_path.query)

        if "code" in query_params:
            auth_code = query_params["code"][0]
            # Send success response
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"""
                <html>
                <head><title>Authorization Successful</title></head>
                <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
                    <h1 style="color: #00a4bd;">✅ Authorization Successful!</h1>
                    <p>You can close this window and return to the terminal.</p>
                    <p>Your tokens are being generated...</p>
                </body>
                </html>
            """
            )
        else:
            # Handle error
            error = query_params.get("error", ["Unknown error"])[0]
            self.send_response(400)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(
                f"""
                <html>
                <head><title>Authorization Failed</title></head>
                <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
                    <h1 style="color: #ff0000;">❌ Authorization Failed</h1>
                    <p>Error: {error}</p>
                    <p>Please try again.</p>
                </body>
                </html>
            """.encode()
            )


def get_authorization_url():
    """Generate the HubSpot OAuth authorization URL"""
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": " ".join(SCOPES),
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code_for_tokens(code):
    """Exchange authorization code for access and refresh tokens"""
    data = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "code": code,
    }

    response = requests.post(TOKEN_URL, data=data)

    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(f"Token exchange failed: {response.text}")


def save_tokens(tokens):
    """Save tokens to file and update .env"""
    # Save to hubspot_token.txt
    with open("hubspot_token.txt", "w") as f:
        f.write(f"Access Token: {tokens['access_token']}\n")
        f.write(f"Refresh Token: {tokens['refresh_token']}\n")
        f.write(f"Expires In: {tokens['expires_in']} seconds\n")
        f.write(f"\nFor quick use, export this:\n")
        f.write(f"export HUBSPOT_ACCESS_TOKEN=\"{tokens['access_token']}\"\n")

    # Update .env file
    env_content = []
    env_file = ".env"

    # Read existing .env or create from template
    if os.path.exists(env_file):
        with open(env_file, "r") as f:
            env_content = f.readlines()
    elif os.path.exists(".env.template"):
        with open(".env.template", "r") as f:
            env_content = f.readlines()

    # Update tokens in env content
    updated = False
    for i, line in enumerate(env_content):
        if line.startswith("HUBSPOT_ACCESS_TOKEN="):
            env_content[i] = f"HUBSPOT_ACCESS_TOKEN={tokens['access_token']}\n"
            updated = True
        elif line.startswith("HUBSPOT_REFRESH_TOKEN="):
            env_content[i] = f"HUBSPOT_REFRESH_TOKEN={tokens['refresh_token']}\n"

    # If not found, append
    if not updated:
        env_content.append(f"\nHUBSPOT_ACCESS_TOKEN={tokens['access_token']}\n")
        env_content.append(f"HUBSPOT_REFRESH_TOKEN={tokens['refresh_token']}\n")

    with open(env_file, "w") as f:
        f.writelines(env_content)

    print(f"\n✅ Tokens saved to:")
    print(f"   - hubspot_token.txt")
    print(f"   - .env")


def main():
    """Main function to run OAuth flow"""
    print("=" * 70)
    print("HubSpot OAuth Token Generator")
    print("=" * 70)

    # Validate configuration
    if not CLIENT_ID or not CLIENT_SECRET:
        print("\n❌ Error: Missing HubSpot credentials!")
        print("\nPlease set the following environment variables:")
        print("  export HUBSPOT_CLIENT_ID='your-client-id'")
        print("  export HUBSPOT_CLIENT_SECRET='your-client-secret'")
        print("\nOr create a .env file with these values.")
        print("\n📖 Setup Instructions:")
        print("1. Go to https://developers.hubspot.com/")
        print("2. Create an app or select existing app")
        print("3. Go to 'Auth' tab")
        print("4. Set Redirect URL: http://localhost:8000/callback")
        print("5. Copy Client ID and Client Secret")
        sys.exit(1)

    print(f"\n✅ Client ID: {CLIENT_ID[:10]}...")
    print(f"✅ Redirect URI: {REDIRECT_URI}")
    print(f"✅ Scopes: {', '.join(SCOPES)}")

    # Step 1: Generate authorization URL
    auth_url = get_authorization_url()
    print(f"\n📝 Step 1: Opening browser for authorization...")
    print(f"\nIf browser doesn't open, visit this URL:")
    print(f"{auth_url}\n")

    # Step 2: Start local server to capture callback
    print("🚀 Starting local server on port 8000...")
    server = HTTPServer(("localhost", PORT), OAuthCallbackHandler)

    # Open browser
    webbrowser.open(auth_url)

    # Wait for callback (handle one request)
    print("⏳ Waiting for authorization...")
    server.handle_request()
    server.server_close()

    if not auth_code:
        print("\n❌ Authorization failed or was cancelled.")
        sys.exit(1)

    print(f"\n✅ Authorization code received!")

    # Step 3: Exchange code for tokens
    print("🔄 Exchanging code for access token...")
    try:
        tokens = exchange_code_for_tokens(auth_code)
        print("✅ Access token obtained!")

        # Step 4: Save tokens
        save_tokens(tokens)

        print("\n" + "=" * 70)
        print("🎉 Success! You can now use the HubSpot API")
        print("=" * 70)
        print(f"\n📄 Access Token: {tokens['access_token'][:20]}...")
        print(f"📄 Refresh Token: {tokens['refresh_token'][:20]}...")
        print(f"⏰ Expires in: {tokens['expires_in']} seconds")

        print("\n📖 Next Steps:")
        print("1. Open hubspot_token.txt to see your full tokens")
        print("2. Run the example script:")
        print("   source venv/bin/activate")
        print("   export HUBSPOT_ACCESS_TOKEN=\"$(grep 'Access Token:' hubspot_token.txt | cut -d' ' -f3)\"")
        print("   PYTHONPATH=../.. python3 example.py")

    except Exception as e:
        print(f"\n❌ Error exchanging code for tokens: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
