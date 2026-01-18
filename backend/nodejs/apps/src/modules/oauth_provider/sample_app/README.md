# PipesHub OAuth Sample Client

This sample application demonstrates the complete OAuth 2.0 Authorization Code flow with PKCE for PipesHub.

## Prerequisites

1. PipesHub backend running on `http://localhost:3000`
2. PipesHub frontend running on `http://localhost:3001`
3. An admin user JWT token to create the OAuth app

## Quick Start

### Step 1: Install Dependencies

```bash
npm install
```

### Step 2: Create an OAuth Application

You need an admin JWT token. Get this by:
1. Log in to PipesHub frontend
2. Open browser DevTools → Application → Local Storage
3. Copy the `jwt_access_token` value

Then create the OAuth app:

```bash
ADMIN_JWT_TOKEN=your_jwt_token npm run create-app
```

This will output the `CLIENT_ID` and `CLIENT_SECRET`.

### Step 3: Start the Sample Client

```bash
CLIENT_ID=your_client_id CLIENT_SECRET=your_client_secret npm start
```

Or create a `.env` file:
```
CLIENT_ID=your_client_id
CLIENT_SECRET=your_client_secret
BACKEND_URL=http://localhost:3000
```

Then just run:
```bash
npm start
```

### Step 4: Test the OAuth Flow

1. Open `http://localhost:8888` in your browser
2. Click "Login with PipesHub"
3. If not logged in to PipesHub, you'll be redirected to the login page
4. After login, you'll see the consent page
5. Click "Allow" to approve the permissions
6. You'll be redirected back to the sample app with your tokens
7. Test the API calls using the buttons

## OAuth Flow Diagram

```
┌─────────────────┐                           ┌─────────────────┐
│  Sample Client  │                           │    PipesHub     │
│   (port 8888)   │                           │Backend (3000)   │
└────────┬────────┘                           └────────┬────────┘
         │                                             │
         │ 1. User clicks "Login with PipesHub"        │
         │────────────────────────────────────────────►│
         │    GET /api/v1/oauth2/authorize             │
         │    ?client_id=...&redirect_uri=...          │
         │    &response_type=code&scope=...            │
         │    &state=...&code_challenge=...            │
         │                                             │
         │                                    ┌────────┴────────┐
         │                                    │   PipesHub      │
         │                                    │Frontend (3001)  │
         │                                    └────────┬────────┘
         │                                             │
         │ 2. Backend redirects to frontend            │
         │◄────────────────────────────────────────────│
         │    302 → /oauth/authorize?...               │
         │                                             │
         │ 3. Frontend checks if logged in             │
         │    If not, redirects to login page          │
         │    After login, shows consent page          │
         │                                             │
         │ 4. User approves consent                    │
         │                                             │
         │ 5. Frontend calls backend with JWT          │
         │    POST /api/v1/oauth2/authorize            │
         │    {consent: 'granted', ...}                │
         │                                             │
         │ 6. Redirect back with authorization code    │
         │◄────────────────────────────────────────────│
         │    302 → http://localhost:8888/callback     │
         │    ?code=...&state=...                      │
         │                                             │
         │ 7. Exchange code for tokens                 │
         │────────────────────────────────────────────►│
         │    POST /api/v1/oauth2/token                │
         │    grant_type=authorization_code            │
         │    &code=...&code_verifier=...              │
         │                                             │
         │ 8. Receive tokens                           │
         │◄────────────────────────────────────────────│
         │    {access_token, refresh_token, ...}       │
         │                                             │
         │ 9. Use access token to call APIs            │
         │────────────────────────────────────────────►│
         │    GET /api/v1/org                          │
         │    Authorization: Bearer access_token       │
         │                                             │
         │ 10. Receive API response                    │
         │◄────────────────────────────────────────────│
         │    {organization data}                      │
         │                                             │
```

## Endpoints Used

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/oauth2/authorize` | Start OAuth flow, redirects to frontend |
| `POST /api/v1/oauth2/authorize` | Submit user consent (called by frontend) |
| `POST /api/v1/oauth2/token` | Exchange authorization code for tokens |
| `GET /api/v1/oauth2/userinfo` | Get user info (OIDC) |
| `GET /api/v1/org` | Get organization info (protected by OAuth) |

## Scopes

The sample app requests these scopes:
- `org:read` - Read organization data
- `user:read` - Read user profile
- `openid` - OpenID Connect
- `profile` - User profile info
- `email` - User email
- `offline_access` - Get refresh token

## Security Features

- **PKCE (Proof Key for Code Exchange)**: Prevents authorization code interception attacks
- **State Parameter**: Prevents CSRF attacks
- **Confidential Client**: Uses client secret for token exchange

## Cleanup

### Stop the Sample Server

```bash
npm run stop
```

### Delete the OAuth App and Stop Server

```bash
ADMIN_JWT_TOKEN=your_jwt_token CLIENT_ID=your_client_id npm run cleanup
```

### Full Cleanup Example

```bash
# Using the credentials from when you created the app
ADMIN_JWT_TOKEN=eyJhbG... CLIENT_ID=8e273900-43e0-4b1f-a899-9eb38843a8bb npm run cleanup
```

## Troubleshooting

### "Connection refused" error
Make sure both PipesHub backend and frontend are running:
```bash
# Terminal 1: Backend
cd backend/nodejs/apps && npm run dev

# Terminal 2: Frontend
cd frontend && npm run dev
```

### "Invalid client" error
Make sure you're using the correct CLIENT_ID and CLIENT_SECRET from when you created the OAuth app.

### "No token provided" error
Make sure you're logged in to PipesHub. The frontend will redirect you to login if needed.

### "Invalid redirect_uri" error
Make sure the callback URL (`http://localhost:8888/callback`) is registered in the OAuth app's redirect URIs.
