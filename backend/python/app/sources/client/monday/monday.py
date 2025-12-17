#!/usr/bin/env python3
"""
Monday.com OAuth 2.0 Test Script
This script implements OAuth 2.0 authentication flow for Monday.com API
"""
import os
import requests
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlencode, parse_qs, urlparse
import threading
import secrets

# Configuration
CLIENT_ID = os.getenv("MONDAY_CLIENT_ID")
CLIENT_SECRET = os.getenv("MONDAY_CLIENT_SECRET")
CALLBACK_URL = "http://localhost:8080/callback"
print(CLIENT_ID, CLIENT_SECRET)

# Monday.com OAuth URLs
AUTHORIZE_URL = "https://auth.monday.com/oauth2/authorize"
TOKEN_URL = "https://auth.monday.com/oauth2/token"
API_URL = "https://api.monday.com/v2"

# Global variable to store the authorization code
auth_code = None
auth_state = None


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP request handler for OAuth callback"""
    
    def do_GET(self):
        """Handle GET request to callback URL"""
        global auth_code, auth_state
        
        # Parse the query parameters
        query_components = parse_qs(urlparse(self.path).query)
        
        if 'code' in query_components:
            auth_code = query_components['code'][0]
            returned_state = query_components.get('state', [None])[0]
            
            # Send success response
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            
            success_html = """
            <html>
                <head><title>Authorization Successful</title></head>
                <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
                    <h1 style="color: #00CA72;">✓ Authorization Successful!</h1>
                    <p>You can close this window and return to the terminal.</p>
                </body>
            </html>
            """
            self.wfile.write(success_html.encode())
            
        elif 'error' in query_components:
            error = query_components['error'][0]
            error_description = query_components.get('error_description', ['Unknown error'])[0]
            
            self.send_response(400)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            
            error_html = f"""
            <html>
                <head><title>Authorization Failed</title></head>
                <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
                    <h1 style="color: #E2445C;">✗ Authorization Failed</h1>
                    <p><strong>Error:</strong> {error}</p>
                    <p>{error_description}</p>
                </body>
            </html>
            """
            self.wfile.write(error_html.encode())
        
    def log_message(self, format, *args):
        """Suppress default logging"""
        pass


def start_callback_server():
    """Start local HTTP server for OAuth callback"""
    server = HTTPServer(('localhost', 8080), OAuthCallbackHandler)
    thread = threading.Thread(target=server.handle_request)
    thread.daemon = True
    thread.start()
    return server


def get_authorization_url(state):
    """Generate authorization URL"""
    params = {
        'client_id': CLIENT_ID,
        'state': state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code_for_token(code):
    """Exchange authorization code for access token"""
    
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
    }
    
    data = {
        'code': code,
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'redirect_uri': CALLBACK_URL,
    }
    
    try:
        response = requests.post(TOKEN_URL, headers=headers, data=data)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error exchanging code for token: {e}")
        if hasattr(e.response, 'text'):
            print(f"Response: {e.response.text}")
        return None


def test_api_access(access_token):
    """Test API access with the obtained token"""
    headers = {
        'Authorization': access_token,
        'Content-Type': 'application/json',
    }
    
    # Test query: Get current user and boards
    query = """
    query {
        me {
            id
            name
            email
            account {
                id
                name
            }
        }
        boards(limit: 5) {
            id
            name
            state
        }
    }
    """
    
    payload = {'query': query}
    
    try:
        response = requests.post(API_URL, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        
        if 'errors' in data:
            print("\n✗ API Access Test Failed!")
            for error in data['errors']:
                print(f"  Error: {error.get('message')}")
            return False
        
        user_data = data.get('data', {}).get('me', {})
        boards_data = data.get('data', {}).get('boards', [])
        
        print("\n✓ API Access Test Successful!")
        print(f"  Name: {user_data.get('name')}")
        print(f"  Email: {user_data.get('email')}")
        print(f"  User ID: {user_data.get('id')}")
        
        if user_data.get('account'):
            print(f"  Account: {user_data['account'].get('name')}")
        
        print(f"\n  Boards Count: {len(boards_data)}")
        for board in boards_data[:3]:
            print(f"    - {board.get('name')} (ID: {board.get('id')})")
        
        return True
    except requests.exceptions.RequestException as e:
        print(f"\n✗ API Access Test Failed: {e}")
        if hasattr(e.response, 'text'):
            print(f"  Response: {e.response.text}")
        return False


def test_graphql_queries(access_token):
    """Test various GraphQL queries"""
    headers = {
        'Authorization': access_token,
        'Content-Type': 'application/json',
    }
    
    print("\n" + "=" * 60)
    print("Testing GraphQL Queries")
    print("=" * 60)
    
    # Test 1: Get boards
    print("\nTest 1: Fetching boards...")
    query_boards = """
    query {
        boards(limit: 3) {
            id
            name
            description
            state
            board_kind
        }
    }
    """
    
    try:
        response = requests.post(API_URL, headers=headers, json={'query': query_boards})
        response.raise_for_status()
        data = response.json()
        
        if 'errors' not in data:
            boards = data.get('data', {}).get('boards', [])
            print(f"✓ Found {len(boards)} boards")
            for board in boards:
                print(f"  - {board.get('name')} (Type: {board.get('board_kind')})")
        else:
            print("✗ Query failed:", data['errors'])
    except Exception as e:
        print(f"✗ Error: {e}")
    
    # Test 2: Get items
    print("\nTest 2: Fetching items...")
    query_items = """
    query {
        items(limit: 5) {
            id
            name
            state
            created_at
        }
    }
    """
    
    try:
        response = requests.post(API_URL, headers=headers, json={'query': query_items})
        response.raise_for_status()
        data = response.json()
        
        if 'errors' not in data:
            items = data.get('data', {}).get('items', [])
            print(f"✓ Found {len(items)} items")
            for item in items[:3]:
                print(f"  - {item.get('name')} (State: {item.get('state')})")
        else:
            print("✗ Query failed:", data['errors'])
    except Exception as e:
        print(f"✗ Error: {e}")
    
    # Test 3: Get complexity data
    print("\nTest 3: Checking API complexity...")
    query_complexity = """
    query {
        complexity {
            query
            reset_in_x_seconds
        }
    }
    """
    
    try:
        response = requests.post(API_URL, headers=headers, json={'query': query_complexity})
        response.raise_for_status()
        data = response.json()
        
        if 'errors' not in data:
            complexity = data.get('data', {}).get('complexity', {})
            print(f"✓ Query complexity: {complexity.get('query')}")
            print(f"  Resets in: {complexity.get('reset_in_x_seconds')} seconds")
        else:
            print("✗ Query failed:", data['errors'])
    except Exception as e:
        print(f"✗ Error: {e}")


def main():
    """Main OAuth flow"""
    global auth_code
    
    print("=" * 60)
    print("Monday.com OAuth 2.0 Test Script")
    print("=" * 60)
    
    # Validate configuration
    if not CLIENT_ID or not CLIENT_SECRET:
        print("\n⚠ ERROR: Please set MONDAY_CLIENT_ID and MONDAY_CLIENT_SECRET!")
        print(f"\nCurrent CLIENT_ID: {CLIENT_ID}")
        print(f"Current CLIENT_SECRET: {CLIENT_SECRET}")
        print("\nTo set up:")
        print("1. Go to https://monday.com/developers/apps")
        print("2. Create a new app or select existing one")
        print("3. Navigate to 'OAuth & Permissions'")
        print(f"4. Set redirect URL to: {CALLBACK_URL}")
        print("5. Copy Client ID and Client Secret")
        print("\nThen run:")
        print(f"  export MONDAY_CLIENT_ID='your_client_id'")
        print(f"  export MONDAY_CLIENT_SECRET='your_client_secret'")
        return
    
    # Generate state for CSRF protection
    state = secrets.token_urlsafe(32)
    
    print(f"\nStep 1: Starting callback server on {CALLBACK_URL}")
    server = start_callback_server()
    print("✓ Callback server started")
    
    print("\nStep 2: Opening authorization URL in browser...")
    auth_url = get_authorization_url(state)
    print(f"Authorization URL: {auth_url}")
    webbrowser.open(auth_url)
    
    print("\nWaiting for authorization callback...")
    print("(Please authorize the application in your browser)")
    
    # Wait for callback (timeout after 5 minutes)
    import time
    timeout = 300
    start_time = time.time()
    
    while auth_code is None and (time.time() - start_time) < timeout:
        time.sleep(0.5)
    
    if auth_code is None:
        print("\n✗ Timeout waiting for authorization")
        return
    
    print(f"\n✓ Authorization code received: {auth_code[:20]}...")
    
    print("\nStep 3: Exchanging authorization code for access token...")
    token_data = exchange_code_for_token(auth_code)
    
    if token_data:
        print("✓ Access token obtained successfully!")
        print(f"\nToken Information:")
        print(f"  Access Token: {token_data.get('access_token', 'N/A')[:30]}...")
        print(f"  Token Type: {token_data.get('token_type', 'N/A')}")
        print(f"  Scope: {token_data.get('scope', 'N/A')}")
        
        # Test API access
        print("\nStep 4: Testing API access...")
        access_token = token_data.get('access_token')
        test_api_access(access_token)
        
        # Test GraphQL queries
        print("\nStep 5: Testing GraphQL queries...")
        test_graphql_queries(access_token)
        
        print("\n" + "=" * 60)
        print("OAuth Flow Completed Successfully!")
        print("=" * 60)
        
        # Save token to file for future use
        print("\nSaving token to 'monday_token.txt'...")
        token_file = os.path.join(os.path.dirname(__file__), 'monday_token.txt')
        with open(token_file, 'w') as f:
            f.write(f"Access Token: {token_data.get('access_token')}\n")
            f.write(f"Token Type: {token_data.get('token_type')}\n")
            f.write(f"Scope: {token_data.get('scope')}\n")
        print("✓ Token saved")
        
    else:
        print("\n✗ Failed to obtain access token")


if __name__ == "__main__":
    main()