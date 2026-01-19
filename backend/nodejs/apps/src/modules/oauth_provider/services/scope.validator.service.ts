import { injectable } from 'inversify'
import {
  OAuthScopes,
  ScopeDefinition,
  validateScopes,
  getAllScopesGroupedByCategory,
  getScopeDefinition,
} from '../config/scopes.config'
import { InvalidScopeError } from '../../../libs/errors/oauth.errors'

@injectable()
export class ScopeValidatorService {

  /**
   * Validate requested scopes against available scopes
   */
  validateRequestedScopes(requestedScopes: string[]): void {
    const result = validateScopes(requestedScopes)
    if (!result.valid) {
      throw new InvalidScopeError(
        `Invalid scopes: ${result.invalid.join(', ')}`,
        { invalidScopes: result.invalid },
      )
    }
  }

  /**
   * Check if requested scopes are allowed for the OAuth app
   */
  validateScopesForApp(
    requestedScopes: string[],
    allowedScopes: string[],
  ): void {
    // First validate that requested scopes are valid scope names
    this.validateRequestedScopes(requestedScopes)

    // Then check if all requested scopes are in the app's allowed scopes
    const disallowed = requestedScopes.filter(
      (scope) => !allowedScopes.includes(scope),
    )

    if (disallowed.length > 0) {
      throw new InvalidScopeError(
        `Scopes not allowed for this app: ${disallowed.join(', ')}`,
        { disallowedScopes: disallowed },
      )
    }
  }

  /**
   * Parse scope string into array
   */
  parseScopes(scopeString: string): string[] {
    if (!scopeString || scopeString.trim() === '') {
      return []
    }
    return scopeString
      .split(/\s+/)
      .map((s) => s.trim())
      .filter((s) => s.length > 0)
  }

  /**
   * Convert scope array to string
   */
  scopesToString(scopes: string[]): string {
    return scopes.join(' ')
  }

  /**
   * Get all available scopes
   */
  getAllScopes(): ScopeDefinition[] {
    return Object.values(OAuthScopes)
  }

  /**
   * Get scopes grouped by category
   */
  getScopesGroupedByCategory(): Record<string, ScopeDefinition[]> {
    return getAllScopesGroupedByCategory()
  }

  /**
   * Get scope definitions for specific scope names
   */
  getScopeDefinitions(scopes: string[]): ScopeDefinition[] {
    return scopes
      .map((scope) => getScopeDefinition(scope))
      .filter((def): def is ScopeDefinition => def !== undefined)
  }

  /**
   * Check if scope requires user consent
   */
  requiresConsent(scopes: string[]): boolean {
    return scopes.some((scope) => {
      const def = getScopeDefinition(scope)
      return def?.requiresUserConsent ?? true
    })
  }

  /**
   * Filter scopes to only those that require user consent
   */
  getConsentRequiredScopes(scopes: string[]): string[] {
    return scopes.filter((scope) => {
      const def = getScopeDefinition(scope)
      return def?.requiresUserConsent ?? true
    })
  }

  /**
   * Check if token has required scope
   */
  hasScope(tokenScopes: string[], requiredScope: string): boolean {
    return tokenScopes.includes(requiredScope)
  }

  /**
   * Check if token has all required scopes
   */
  hasAllScopes(tokenScopes: string[], requiredScopes: string[]): boolean {
    return requiredScopes.every((scope) => tokenScopes.includes(scope))
  }

  /**
   * Check if token has any of the required scopes
   */
  hasAnyScope(tokenScopes: string[], requiredScopes: string[]): boolean {
    return requiredScopes.some((scope) => tokenScopes.includes(scope))
  }

  /**
   * Get intersection of requested and allowed scopes
   */
  getGrantedScopes(
    requestedScopes: string[],
    allowedScopes: string[],
  ): string[] {
    return requestedScopes.filter((scope) => allowedScopes.includes(scope))
  }
}
