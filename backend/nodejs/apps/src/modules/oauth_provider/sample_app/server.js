/**
 * Sample OAuth Client Application
 *
 * This demonstrates the complete OAuth 2.0 Authorization Code flow with PKCE
 * for PipesHub.
 *
 * Flow:
 * 1. User visits http://localhost:8888
 * 2. Clicks "Login with PipesHub" -> redirects to PipesHub authorize URL
 * 3. Backend redirects to frontend if not logged in
 * 4. User logs in to PipesHub (if needed)
 * 5. User sees consent page and approves
 * 6. Redirected back to http://localhost:8888/callback with authorization code
 * 7. Server exchanges code for tokens
 * 8. User is shown their profile info
 *
 * Usage:
 *   CLIENT_ID=xxx CLIENT_SECRET=xxx npm start
 *
 * Or create a .env file with:
 *   CLIENT_ID=xxx
 *   CLIENT_SECRET=xxx
 */

const express = require('express')
const crypto = require('crypto')
const http = require('http')
const https = require('https')
const fs = require('fs')
const path = require('path')

// Load .env file if it exists
const envPath = path.join(__dirname, '.env')
if (fs.existsSync(envPath)) {
  const envContent = fs.readFileSync(envPath, 'utf-8')
  envContent.split('\n').forEach((line) => {
    const trimmed = line.trim()
    if (trimmed && !trimmed.startsWith('#')) {
      const [key, ...valueParts] = trimmed.split('=')
      const value = valueParts.join('=')
      if (key && value && !process.env[key]) {
        process.env[key] = value
      }
    }
  })
}

const app = express()
app.use(express.urlencoded({ extended: true }))
app.use(express.json())

const PORT = process.env.PORT || 8888

// Configuration
const CLIENT_ID = process.env.CLIENT_ID
const CLIENT_SECRET = process.env.CLIENT_SECRET
const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:3000'
const CALLBACK_URL = `http://localhost:${PORT}/callback`

// Admin token for cleanup operations (optional - can be provided via UI)
let adminToken = process.env.ADMIN_JWT_TOKEN || null

// Scopes to request
const SCOPES = 'org:read user:read openid profile email offline_access'

// In-memory storage for PKCE and state (use Redis/DB in production)
const pendingAuthorizations = new Map()

// In-memory storage for user tokens (use session/DB in production)
let currentUserTokens = null

// Server instance for graceful shutdown
let server = null

// Validate configuration
if (!CLIENT_ID || !CLIENT_SECRET) {
  console.error('Error: CLIENT_ID and CLIENT_SECRET environment variables are required')
  console.error('Usage: CLIENT_ID=xxx CLIENT_SECRET=xxx npm start')
  console.error('')
  console.error('To create an OAuth app, run:')
  console.error('  ADMIN_JWT_TOKEN=xxx node create-oauth-app.js')
  process.exit(1)
}

/**
 * Mask sensitive data for display (shows first 8 chars + ...)
 */
function maskSecret(secret) {
  if (!secret || secret.length <= 8) return '***'
  return secret.substring(0, 8) + '...'
}

/**
 * Escape HTML to prevent XSS attacks
 */
function escapeHtml(str) {
  if (!str) return ''
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

console.log('Configuration:')
console.log('  Backend URL:', BACKEND_URL)
console.log('  Client ID:', maskSecret(CLIENT_ID))
console.log('  Callback URL:', CALLBACK_URL)
console.log('')

/**
 * Generate PKCE code verifier (random string)
 */
function generateCodeVerifier() {
  return crypto.randomBytes(32).toString('base64url')
}

/**
 * Generate PKCE code challenge from verifier (SHA256 hash)
 */
function generateCodeChallenge(verifier) {
  return crypto.createHash('sha256').update(verifier).digest('base64url')
}

/**
 * Generate random state parameter
 */
function generateState() {
  return crypto.randomBytes(16).toString('hex')
}

/**
 * Make HTTP request to backend API
 */
function makeRequest(url, options = {}) {
  return new Promise((resolve, reject) => {
    const parsedUrl = new URL(url)
    const isHttps = parsedUrl.protocol === 'https:'
    const lib = isHttps ? https : http

    const requestOptions = {
      hostname: parsedUrl.hostname,
      port: parsedUrl.port || (isHttps ? 443 : 80),
      path: parsedUrl.pathname + parsedUrl.search,
      method: options.method || 'GET',
      headers: options.headers || {},
    }

    const req = lib.request(requestOptions, (res) => {
      let data = ''
      res.on('data', (chunk) => (data += chunk))
      res.on('end', () => {
        try {
          const parsed = JSON.parse(data)
          resolve({ status: res.statusCode, data: parsed })
        } catch {
          resolve({ status: res.statusCode, data })
        }
      })
    })

    req.on('error', (error) => {
      if (error.code === 'ECONNREFUSED') {
        reject(new Error(`Connection refused to ${url}. Is the backend running?`))
      } else {
        reject(error)
      }
    })

    if (options.body) {
      req.write(options.body)
    }

    req.end()
  })
}

/**
 * Home page - shows login button or user info
 */
app.get('/', (req, res) => {
  if (currentUserTokens) {
    // User is logged in, show their info
    res.send(`
      <!DOCTYPE html>
      <html>
      <head>
        <title>Sample OAuth Client - Logged In</title>
        <style>
          body { font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; }
          .card { background: #f5f5f5; padding: 20px; border-radius: 8px; margin: 20px 0; }
          .token { word-break: break-all; font-family: monospace; font-size: 12px; background: #fff; padding: 10px; margin: 10px 0; border-radius: 4px; }
          button { background: #007bff; color: white; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer; margin: 5px; }
          button.danger { background: #dc3545; }
          button:hover { opacity: 0.9; }
        </style>
      </head>
      <body>
        <h1>Logged In!</h1>
        <div class="card">
          <h3>Access Token:</h3>
          <div class="token">${currentUserTokens.access_token}</div>
          <h3>Token Type:</h3>
          <p>${currentUserTokens.token_type}</p>
          <h3>Expires In:</h3>
          <p>${currentUserTokens.expires_in} seconds</p>
          <h3>Scopes:</h3>
          <p>${currentUserTokens.scope}</p>
          ${currentUserTokens.refresh_token ? `
          <h3>Refresh Token:</h3>
          <div class="token">${currentUserTokens.refresh_token}</div>
          ` : ''}
        </div>
        <div>
          <a href="/api/org"><button>Test API: Get Organization</button></a>
          <a href="/api/userinfo"><button>Test API: Get User Info</button></a>
          <a href="/logout"><button class="danger">Logout</button></a>
        </div>
        <br><br>
        <a href="/admin" style="color: #6c757d; font-size: 14px;">Admin / Cleanup</a>
      </body>
      </html>
    `)
  } else {
    // User is not logged in, show login button
    res.send(`
      <!DOCTYPE html>
      <html>
      <head>
        <title>Sample OAuth Client</title>
        <style>
          body { font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; text-align: center; }
          button { background: #007bff; color: white; border: none; padding: 15px 30px; border-radius: 4px; cursor: pointer; font-size: 16px; }
          button:hover { opacity: 0.9; }
          .info { background: #f5f5f5; padding: 20px; border-radius: 8px; margin: 20px 0; text-align: left; }
          code { background: #e9e9e9; padding: 2px 6px; border-radius: 3px; }
        </style>
      </head>
      <body>
        <h1>Sample OAuth Client</h1>
        <p>This demonstrates the OAuth 2.0 Authorization Code flow with PipesHub.</p>

        <a href="/login"><button>Login with PipesHub</button></a>

        <div class="info">
          <h3>How it works:</h3>
          <ol>
            <li>Click "Login with PipesHub"</li>
            <li>You'll be redirected to PipesHub</li>
            <li>Log in (if not already)</li>
            <li>Approve the permissions</li>
            <li>You'll be redirected back here with your tokens</li>
          </ol>
          <h3>Configuration:</h3>
          <p>Backend: <code>${BACKEND_URL}</code></p>
          <p>Client ID: <code>${CLIENT_ID}</code></p>
          <p>Scopes: <code>${SCOPES}</code></p>
        </div>
        <br>
        <a href="/admin" style="color: #6c757d; font-size: 14px;">Admin / Cleanup</a>
      </body>
      </html>
    `)
  }
})

/**
 * Initiate OAuth login flow
 */
app.get('/login', (req, res) => {
  // Generate PKCE values
  const codeVerifier = generateCodeVerifier()
  const codeChallenge = generateCodeChallenge(codeVerifier)
  const state = generateState()

  // Store for later verification
  pendingAuthorizations.set(state, {
    codeVerifier,
    timestamp: Date.now(),
  })

  // Build authorization URL
  const authUrl = new URL('/api/v1/oauth2/authorize', BACKEND_URL)
  authUrl.searchParams.set('client_id', CLIENT_ID)
  authUrl.searchParams.set('redirect_uri', CALLBACK_URL)
  authUrl.searchParams.set('response_type', 'code')
  authUrl.searchParams.set('scope', SCOPES)
  authUrl.searchParams.set('state', state)
  authUrl.searchParams.set('code_challenge', codeChallenge)
  authUrl.searchParams.set('code_challenge_method', 'S256')

  console.log('Redirecting to authorization URL:', authUrl.toString())
  res.redirect(authUrl.toString())
})

/**
 * OAuth callback handler
 */
app.get('/callback', async (req, res) => {
  const { code, state, error, error_description } = req.query

  // Handle error response
  if (error) {
    return res.send(`
      <!DOCTYPE html>
      <html>
      <head><title>OAuth Error</title></head>
      <body>
        <h1>Authorization Failed</h1>
        <p>Error: ${escapeHtml(error)}</p>
        <p>Description: ${escapeHtml(error_description) || 'No description provided'}</p>
        <a href="/">Go Back</a>
      </body>
      </html>
    `)
  }

  // Verify state
  const pending = pendingAuthorizations.get(state)
  if (!pending) {
    return res.status(400).send('Invalid state parameter. Possible CSRF attack.')
  }
  pendingAuthorizations.delete(state)

  // Exchange code for tokens
  try {
    console.log('Exchanging authorization code for tokens...')

    const tokenUrl = new URL('/api/v1/oauth2/token', BACKEND_URL)

    const tokenBody = new URLSearchParams({
      grant_type: 'authorization_code',
      code: code,
      redirect_uri: CALLBACK_URL,
      client_id: CLIENT_ID,
      client_secret: CLIENT_SECRET,
      code_verifier: pending.codeVerifier,
    }).toString()

    const response = await makeRequest(tokenUrl.toString(), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
      },
      body: tokenBody,
    })

    if (response.status !== 200) {
      throw new Error(response.data.error_description || response.data.error || 'Token exchange failed')
    }

    // Store tokens
    currentUserTokens = response.data

    console.log('Successfully obtained tokens!')
    res.redirect('/')
  } catch (err) {
    console.error('Token exchange error:', err.message)
    res.send(`
      <!DOCTYPE html>
      <html>
      <head><title>Token Exchange Error</title></head>
      <body>
        <h1>Token Exchange Failed</h1>
        <p>${escapeHtml(err.message)}</p>
        <a href="/">Go Back</a>
      </body>
      </html>
    `)
  }
})

/**
 * Test API: Get organization info using OAuth token
 */
app.get('/api/org', async (req, res) => {
  if (!currentUserTokens) {
    return res.redirect('/')
  }

  try {
    const response = await makeRequest(`${BACKEND_URL}/api/v1/org`, {
      headers: {
        'Authorization': `Bearer ${currentUserTokens.access_token}`,
      },
    })

    res.send(`
      <!DOCTYPE html>
      <html>
      <head>
        <title>Organization Info</title>
        <style>
          body { font-family: Arial, sans-serif; max-width: 800px; margin: 50px auto; padding: 20px; }
          pre { background: #f5f5f5; padding: 20px; border-radius: 8px; overflow: auto; }
        </style>
      </head>
      <body>
        <h1>Organization Info (via OAuth)</h1>
        <p>Status: ${response.status}</p>
        <pre>${JSON.stringify(response.data, null, 2)}</pre>
        <a href="/">Back</a>
      </body>
      </html>
    `)
  } catch (err) {
    res.send(`Error: ${escapeHtml(err.message)}`)
  }
})

/**
 * Test API: Get user info using OAuth token
 */
app.get('/api/userinfo', async (req, res) => {
  if (!currentUserTokens) {
    return res.redirect('/')
  }

  try {
    const response = await makeRequest(`${BACKEND_URL}/api/v1/oauth2/userinfo`, {
      headers: {
        'Authorization': `Bearer ${currentUserTokens.access_token}`,
      },
    })

    res.send(`
      <!DOCTYPE html>
      <html>
      <head>
        <title>User Info</title>
        <style>
          body { font-family: Arial, sans-serif; max-width: 800px; margin: 50px auto; padding: 20px; }
          pre { background: #f5f5f5; padding: 20px; border-radius: 8px; overflow: auto; }
        </style>
      </head>
      <body>
        <h1>User Info (OIDC /userinfo)</h1>
        <p>Status: ${response.status}</p>
        <pre>${JSON.stringify(response.data, null, 2)}</pre>
        <a href="/">Back</a>
      </body>
      </html>
    `)
  } catch (err) {
    res.send(`Error: ${escapeHtml(err.message)}`)
  }
})

/**
 * Logout - clear tokens
 */
app.get('/logout', (req, res) => {
  currentUserTokens = null
  res.redirect('/')
})

/**
 * Admin/Cleanup page
 */
app.get('/admin', (req, res) => {
  res.send(`
    <!DOCTYPE html>
    <html>
    <head>
      <title>Sample OAuth Client - Admin</title>
      <style>
        body { font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; }
        .card { background: #f5f5f5; padding: 20px; border-radius: 8px; margin: 20px 0; }
        button { padding: 10px 20px; border-radius: 4px; cursor: pointer; margin: 5px; border: none; color: white; }
        button.danger { background: #dc3545; }
        button.warning { background: #ffc107; color: black; }
        button.primary { background: #007bff; }
        button:hover { opacity: 0.9; }
        input[type="text"], input[type="password"] { width: 100%; padding: 10px; margin: 10px 0; border: 1px solid #ddd; border-radius: 4px; box-sizing: border-box; }
        .warning-box { background: #fff3cd; border: 1px solid #ffc107; padding: 15px; border-radius: 4px; margin: 15px 0; }
        .info-box { background: #d1ecf1; border: 1px solid #bee5eb; padding: 15px; border-radius: 4px; margin: 15px 0; }
        code { background: #e9e9e9; padding: 2px 6px; border-radius: 3px; word-break: break-all; }
      </style>
    </head>
    <body>
      <h1>Admin / Cleanup</h1>

      <div class="info-box">
        <strong>Current Configuration:</strong><br>
        Client ID: <code>${CLIENT_ID}</code><br>
        Backend URL: <code>${BACKEND_URL}</code><br>
        Admin Token: <code>${adminToken ? 'Configured' : 'Not configured'}</code>
      </div>

      <div class="card">
        <h3>Delete OAuth Application</h3>
        <p>This will permanently delete the OAuth application from PipesHub.</p>

        <form action="/admin/delete-app" method="POST">
          <label for="token">Admin JWT Token:</label>
          <input type="password" id="token" name="token" placeholder="Paste your admin JWT token here" value="${adminToken || ''}" required>

          <div class="warning-box">
            <strong>Warning:</strong> This action cannot be undone. The OAuth app with Client ID <code>${CLIENT_ID}</code> will be permanently deleted.
          </div>

          <button type="submit" class="danger">Delete OAuth App</button>
        </form>
      </div>

      <div class="card">
        <h3>Stop Sample Server</h3>
        <p>This will stop the sample OAuth client server.</p>
        <form action="/admin/shutdown" method="POST">
          <button type="submit" class="warning">Stop Server</button>
        </form>
      </div>

      <div class="card">
        <h3>Full Cleanup</h3>
        <p>Delete the OAuth app AND stop the server.</p>
        <form action="/admin/full-cleanup" method="POST">
          <label for="token2">Admin JWT Token:</label>
          <input type="password" id="token2" name="token" placeholder="Paste your admin JWT token here" value="${adminToken || ''}" required>
          <button type="submit" class="danger">Full Cleanup</button>
        </form>
      </div>

      <br>
      <a href="/">← Back to Home</a>
    </body>
    </html>
  `)
})

/**
 * Get OAuth app ID by client ID
 */
async function getAppIdByClientId(clientId, token) {
  const response = await makeRequest(`${BACKEND_URL}/api/v1/oauth-clients`, {
    headers: {
      Authorization: `Bearer ${token}`,
    },
  })

  if (response.status !== 200) {
    throw new Error(`Failed to list apps: ${JSON.stringify(response.data)}`)
  }

  // API returns { data: [...] } structure
  const apps = response.data.data || response.data.apps || response.data || []

  if (!Array.isArray(apps)) {
    throw new Error(`Unexpected response format: ${JSON.stringify(response.data)}`)
  }

  const app = apps.find((a) => a.clientId === clientId)

  if (!app) {
    throw new Error(`OAuth app with clientId ${clientId} not found`)
  }

  // API uses 'id' not '_id'
  return app.id || app._id
}

/**
 * Delete OAuth application
 */
async function deleteOAuthApp(appId, token) {
  const response = await makeRequest(`${BACKEND_URL}/api/v1/oauth-clients/${appId}`, {
    method: 'DELETE',
    headers: {
      Authorization: `Bearer ${token}`,
    },
  })

  if (response.status !== 200 && response.status !== 204) {
    throw new Error(`Failed to delete app: ${JSON.stringify(response.data)}`)
  }

  return true
}

/**
 * Delete OAuth App endpoint
 */
app.post('/admin/delete-app', async (req, res) => {
  const token = req.body.token || adminToken

  if (!token) {
    return res.status(400).send(`
      <!DOCTYPE html>
      <html>
      <head><title>Error</title></head>
      <body>
        <h1>Error</h1>
        <p>Admin JWT token is required</p>
        <a href="/admin">← Back</a>
      </body>
      </html>
    `)
  }

  try {
    console.log('Deleting OAuth app...')
    const appId = await getAppIdByClientId(CLIENT_ID, token)
    console.log('Found app ID:', appId)

    await deleteOAuthApp(appId, token)
    console.log('OAuth app deleted successfully')

    res.send(`
      <!DOCTYPE html>
      <html>
      <head><title>Success</title></head>
      <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; text-align: center;">
        <h1 style="color: #28a745;">OAuth App Deleted!</h1>
        <p>The OAuth application has been successfully deleted from PipesHub.</p>
        <p>Client ID: <code>${CLIENT_ID}</code></p>
        <br>
        <p>You can now stop the server or <a href="/admin">go back to admin</a>.</p>
      </body>
      </html>
    `)
  } catch (err) {
    console.error('Failed to delete OAuth app:', err.message)
    res.status(500).send(`
      <!DOCTYPE html>
      <html>
      <head><title>Error</title></head>
      <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px;">
        <h1 style="color: #dc3545;">Failed to Delete OAuth App</h1>
        <p>${escapeHtml(err.message)}</p>
        <a href="/admin">← Back to Admin</a>
      </body>
      </html>
    `)
  }
})

/**
 * Shutdown server endpoint
 */
app.post('/admin/shutdown', (req, res) => {
  res.send(`
    <!DOCTYPE html>
    <html>
    <head><title>Server Stopping</title></head>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; text-align: center;">
      <h1>Server Stopping...</h1>
      <p>The sample OAuth client server is shutting down.</p>
      <p>You can close this window.</p>
    </body>
    </html>
  `)

  console.log('Shutdown requested, stopping server...')
  setTimeout(() => {
    if (server) {
      server.close(() => {
        console.log('Server stopped')
        process.exit(0)
      })
    } else {
      process.exit(0)
    }
  }, 500)
})

/**
 * Full cleanup - delete app and shutdown
 */
app.post('/admin/full-cleanup', async (req, res) => {
  const token = req.body.token || adminToken

  if (!token) {
    return res.status(400).send(`
      <!DOCTYPE html>
      <html>
      <head><title>Error</title></head>
      <body>
        <h1>Error</h1>
        <p>Admin JWT token is required</p>
        <a href="/admin">← Back</a>
      </body>
      </html>
    `)
  }

  try {
    console.log('Full cleanup: Deleting OAuth app...')
    const appId = await getAppIdByClientId(CLIENT_ID, token)
    await deleteOAuthApp(appId, token)
    console.log('OAuth app deleted successfully')

    res.send(`
      <!DOCTYPE html>
      <html>
      <head><title>Cleanup Complete</title></head>
      <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; text-align: center;">
        <h1 style="color: #28a745;">Cleanup Complete!</h1>
        <p>OAuth application deleted and server is stopping.</p>
        <p>You can close this window.</p>
      </body>
      </html>
    `)

    console.log('Shutdown requested, stopping server...')
    setTimeout(() => {
      if (server) {
        server.close(() => {
          console.log('Server stopped')
          process.exit(0)
        })
      } else {
        process.exit(0)
      }
    }, 500)
  } catch (err) {
    console.error('Cleanup failed:', err.message)
    res.status(500).send(`
      <!DOCTYPE html>
      <html>
      <head><title>Error</title></head>
      <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px;">
        <h1 style="color: #dc3545;">Cleanup Failed</h1>
        <p>${escapeHtml(err.message)}</p>
        <a href="/admin">← Back to Admin</a>
      </body>
      </html>
    `)
  }
})

// Start server
server = app.listen(PORT, () => {
  console.log(`Sample OAuth Client running at http://localhost:${PORT}`)
  console.log('')
  console.log('Steps to test:')
  console.log('1. Make sure PipesHub backend is running on', BACKEND_URL)
  console.log('2. Make sure PipesHub frontend is running on http://localhost:3001')
  console.log('3. Open http://localhost:' + PORT + ' in your browser')
  console.log('4. Click "Login with PipesHub"')
  console.log('')
})
