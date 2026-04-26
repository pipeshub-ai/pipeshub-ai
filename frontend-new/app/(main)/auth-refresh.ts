/**
 * Auth Refresh Module
 *
 * Centralized module for handling token refresh operations.
 * Features:
 * - In-memory lock to prevent simultaneous refresh attempts
 * - Automatic retry of failed requests after successful refresh
 */

import { AppRouterInstance } from 'next/dist/shared/lib/app-router-context.shared-runtime'
import { useAuthStore } from '@/lib/store/auth-store'
import { getApiBaseUrl } from '@/lib/utils/api-base-url'

// In-memory lock to prevent multiple simultaneous refresh attempts
let isRefreshing = false
let refreshPromise: Promise<boolean> | null = null

/**
 * Attempts to refresh the access token using the stored refresh token
 *
 * @returns Promise<boolean> - true if refresh succeeded, false otherwise
 */
export async function refreshAccessToken(): Promise<boolean> {
  // If already refreshing, return the existing promise
  if (isRefreshing && refreshPromise) {
    return refreshPromise
  }

  // Acquire lock
  isRefreshing = true

  // Create the refresh promise
  refreshPromise = (async () => {
    try {
      // Get refresh token from auth store
      const refreshToken = useAuthStore.getState().refreshToken

      if (!refreshToken) {
        console.log('No refresh token available')
        return false
      }

      console.log('Attempting token refresh...')

      // Call the refresh endpoint with refresh_token in Authorization header
      const response = await fetch(`${getApiBaseUrl()}/api/v1/userAccount/refresh/token`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${refreshToken}`,
        },
      })

      if (!response.ok) {
        console.log('Refresh token request failed', response.status)
        return false
      }

      const data = await response.json()

      // Update the access token in auth store
      if (data.accessToken || data.token) {
        const newAccessToken = data.accessToken || data.token
        const newRefreshToken = data.refresh_token || data.refreshToken || refreshToken

        // Update auth store (this will persist to localStorage automatically)
        useAuthStore.getState().setTokens(newAccessToken, newRefreshToken)

        console.log('Token refreshed successfully')
        return true
      }

      return false
    } catch (error) {
      console.error('Error refreshing token:', error)
      return false
    } finally {
      // Release lock
      isRefreshing = false
      refreshPromise = null
    }
  })()

  return refreshPromise
}

/**
 * Clears authentication tokens and redirects to login
 *
 * @param router - Next.js router instance for navigation
 */
export function clearAuthAndRedirectToLogin(router: AppRouterInstance): void {
  // Clear auth store (this will clear localStorage automatically)
  useAuthStore.getState().logout()

  // Redirect to login
  router.push('/login')
}
