import { OAuthTokenService } from '../../modules/oauth_provider/services/oauth_token.service'

/**
 * Factory provider for OAuthTokenService.
 *
 * Solves the cross-container dependency problem: AuthMiddleware is instantiated
 * in ~12 separate Inversify containers, but OAuthTokenService lives only in
 * the oauthProviderContainer. Instead of using a static property on
 * AuthMiddleware (which bypasses DI), this provider is passed to AuthMiddleware
 * via constructor injection as a factory function.
 *
 * Lifecycle:
 * 1. Containers create AuthMiddleware with `resolveOAuthTokenService` as factory
 * 2. After oauthProviderContainer is initialized, `registerOAuthTokenService` is called
 * 3. Subsequent calls to `resolveOAuthTokenService()` return the registered instance
 */

let oauthTokenServiceInstance: OAuthTokenService | null = null

/**
 * Factory function — injected into AuthMiddleware constructor.
 * Returns the registered OAuthTokenService or null if not yet initialized.
 */
export function resolveOAuthTokenService(): OAuthTokenService | null {
  return oauthTokenServiceInstance
}

/**
 * Called once during app initialization after the OAuth provider container
 * is created. Registers the OAuthTokenService instance for the factory.
 */
export function registerOAuthTokenService(service: OAuthTokenService): void {
  oauthTokenServiceInstance = service
}
