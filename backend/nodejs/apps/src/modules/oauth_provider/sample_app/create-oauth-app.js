/**
 * Script to create an OAuth application in PipesHub
 *
 * Prerequisites:
 * 1. Backend server running on http://localhost:3000
 * 2. An admin user JWT token (set as ADMIN_JWT_TOKEN environment variable)
 *
 * Usage:
 *   ADMIN_JWT_TOKEN=your_jwt_token node create-oauth-app.js
 *
 * Or create a .env file with ADMIN_JWT_TOKEN=your_jwt_token
 */

const http = require('http')

// Configuration
const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:3000'
const ADMIN_JWT_TOKEN = process.env.ADMIN_JWT_TOKEN

if (!ADMIN_JWT_TOKEN) {
  console.error('Error: ADMIN_JWT_TOKEN environment variable is required')
  console.error('Usage: ADMIN_JWT_TOKEN=your_jwt_token node create-oauth-app.js')
  process.exit(1)
}

// OAuth app configuration
const oauthAppConfig = {
  name: 'Sample OAuth Client',
  description: 'A sample application demonstrating PipesHub OAuth flow',
  redirectUris: ['http://localhost:8888/callback'],
  allowedGrantTypes: ['authorization_code', 'refresh_token'],
  allowedScopes: ['org:read', 'user:read', 'openid', 'profile', 'email', 'offline_access'],
  isConfidential: true,
  accessTokenLifetime: 3600,
  refreshTokenLifetime: 2592000,
}

async function createOAuthApp() {
  console.log('Creating OAuth application...')
  console.log('Backend URL:', BACKEND_URL)

  const url = new URL('/api/v1/oauth-clients', BACKEND_URL)

  const postData = JSON.stringify(oauthAppConfig)

  const options = {
    hostname: url.hostname,
    port: url.port || 80,
    path: url.pathname,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Content-Length': Buffer.byteLength(postData),
      'Authorization': `Bearer ${ADMIN_JWT_TOKEN}`,
    },
  }

  return new Promise((resolve, reject) => {
    const req = http.request(options, (res) => {
      let data = ''

      res.on('data', (chunk) => {
        data += chunk
      })

      res.on('end', () => {
        try {
          const result = JSON.parse(data)

          if (res.statusCode >= 200 && res.statusCode < 300) {
            resolve(result)
          } else {
            reject(new Error(`HTTP ${res.statusCode}: ${JSON.stringify(result)}`))
          }
        } catch (e) {
          reject(new Error(`Failed to parse response: ${data}`))
        }
      })
    })

    req.on('error', (error) => {
      if (error.code === 'ECONNREFUSED') {
        reject(new Error(`Connection refused. Is the backend running at ${BACKEND_URL}?`))
      } else {
        reject(error)
      }
    })

    req.write(postData)
    req.end()
  })
}

/**
 * Mask sensitive data for logging (shows first 8 chars + ...)
 */
function maskSecret(secret) {
  if (!secret || secret.length <= 8) return '***'
  return secret.substring(0, 8) + '...'
}

async function main() {
  try {
    const result = await createOAuthApp()

    // Extract app data (handle both { app: {...} } and direct { clientId, ... } formats)
    const app = result.app || result

    console.log('\n========================================')
    console.log('OAuth Application Created Successfully!')
    console.log('========================================\n')
    console.log('Client ID:', app.clientId)
    console.log('Client Secret:', maskSecret(app.clientSecret), '(full secret saved to .env)')
    console.log('')

    // Save credentials to .env file instead of logging them in clear text
    const fs = require('fs')
    const path = require('path')
    const envContent = `# OAuth Client Credentials - Generated ${new Date().toISOString()}
CLIENT_ID=${app.clientId}
CLIENT_SECRET=${app.clientSecret}
BACKEND_URL=http://localhost:3000
FRONTEND_URL=http://localhost:3001
`
    const envPath = path.join(__dirname, '.env')
    fs.writeFileSync(envPath, envContent)
    console.log('Credentials saved to:', envPath)
    console.log('\nTo run the sample client:')
    console.log('  npm start')
    console.log('\n(Credentials will be loaded from .env file)')
  } catch (error) {
    console.error('Failed to create OAuth app:', error.message)
    process.exit(1)
  }
}

main()
