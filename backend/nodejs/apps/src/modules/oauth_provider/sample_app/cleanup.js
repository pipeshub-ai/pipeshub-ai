/**
 * Cleanup Script for OAuth Sample App
 *
 * This script:
 * 1. Deletes the OAuth application from PipesHub
 * 2. Stops the sample server if running
 *
 * Usage:
 *   ADMIN_JWT_TOKEN=your_token CLIENT_ID=your_client_id npm run cleanup
 *
 * Or to just stop the server:
 *   npm run stop
 */

const http = require('http')
const { execSync } = require('child_process')

// Configuration
const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:3000'
const ADMIN_JWT_TOKEN = process.env.ADMIN_JWT_TOKEN
const CLIENT_ID = process.env.CLIENT_ID

/**
 * Make HTTP request
 */
function makeRequest(url, options = {}) {
  return new Promise((resolve, reject) => {
    const parsedUrl = new URL(url)

    const requestOptions = {
      hostname: parsedUrl.hostname,
      port: parsedUrl.port || 80,
      path: parsedUrl.pathname + parsedUrl.search,
      method: options.method || 'GET',
      headers: options.headers || {},
    }

    const req = http.request(requestOptions, (res) => {
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
 * Find and get OAuth app ID by client ID
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
 * Stop the sample server
 */
function stopServer() {
  try {
    // Find and kill process on port 8888
    const platform = process.platform

    if (platform === 'darwin' || platform === 'linux') {
      try {
        execSync('lsof -ti:8888 | xargs kill -9 2>/dev/null', { stdio: 'ignore' })
        console.log('✓ Sample server stopped (port 8888)')
      } catch {
        console.log('ℹ No server running on port 8888')
      }
    } else if (platform === 'win32') {
      try {
        execSync('FOR /F "tokens=5" %a IN (\'netstat -aon ^| find ":8888"\') DO taskkill /F /PID %a', {
          stdio: 'ignore',
          shell: true,
        })
        console.log('✓ Sample server stopped (port 8888)')
      } catch {
        console.log('ℹ No server running on port 8888')
      }
    }
  } catch (error) {
    console.log('ℹ Could not stop server:', error.message)
  }
}

/**
 * Main cleanup function
 */
async function main() {
  const args = process.argv.slice(2)
  const stopOnly = args.includes('--stop-only') || args.includes('-s')

  console.log('🧹 OAuth Sample App Cleanup\n')

  // Always try to stop the server
  stopServer()

  if (stopOnly) {
    console.log('\n✅ Cleanup complete (server only)')
    return
  }

  // Delete OAuth app if credentials provided
  if (!ADMIN_JWT_TOKEN) {
    console.log('\nℹ ADMIN_JWT_TOKEN not provided - skipping OAuth app deletion')
    console.log('  To delete the OAuth app, run:')
    console.log('  ADMIN_JWT_TOKEN=xxx CLIENT_ID=xxx npm run cleanup')
    return
  }

  if (!CLIENT_ID) {
    console.log('\nℹ CLIENT_ID not provided - skipping OAuth app deletion')
    console.log('  To delete the OAuth app, run:')
    console.log('  ADMIN_JWT_TOKEN=xxx CLIENT_ID=xxx npm run cleanup')
    return
  }

  /**
   * Mask sensitive data for display
   */
  function maskSecret(secret) {
    if (!secret || secret.length <= 8) return '***'
    return secret.substring(0, 8) + '...'
  }

  try {
    console.log(`\nDeleting OAuth app (clientId: ${maskSecret(CLIENT_ID)})...`)

    // Get app ID from client ID
    const appId = await getAppIdByClientId(CLIENT_ID, ADMIN_JWT_TOKEN)
    console.log(`  Found app ID: ${maskSecret(appId)}`)

    // Delete the app
    await deleteOAuthApp(appId, ADMIN_JWT_TOKEN)
    console.log('✓ OAuth application deleted')

    console.log('\n✅ Cleanup complete!')
  } catch (error) {
    console.error('✗ Failed to delete OAuth app:', error.message)
    process.exit(1)
  }
}

main()
