import { OAuthScopes } from "../../../modules/oauth_provider/config/scopes.config";

class OAuthUtils {
    
    static isOAuthToken(decoded: Record<string, any> | null): boolean {
        return (
        decoded !== null &&
        decoded.tokenType === 'oauth' &&
        typeof decoded.client_id === 'string' &&
        typeof decoded.iss === 'string'
        );
    }

    /**
     * Determine which OAuth scopes are required for the given HTTP method and path.
     * Returns an array of scope names that cover this endpoint.
     */
    static getRequiredScopesForEndpoint(method: string, path: string): string[] {
        const requiredScopes: string[] = [];
        const upperMethod = method.toUpperCase();

        for (const [scopeName, scopeDef] of Object.entries(OAuthScopes)) {
        for (const endpointPattern of scopeDef.endpoints) {
            if (OAuthUtils.matchEndpointPattern(upperMethod, path, endpointPattern)) {
                requiredScopes.push(scopeName);
                break; // one match per scope
            }
        }
        }

        return requiredScopes;
    }

    /**
     * Match an HTTP method+path against a scope endpoint pattern.
     * Patterns: "GET /api/v1/users", "GET /api/v1/users/:id", "GET /api/v1/knowledgeBase/*"
     */
    static matchEndpointPattern(method: string, requestPath: string, pattern: string): boolean {
        const spaceIndex = pattern.indexOf(' ');
        if (spaceIndex === -1) return false;

        const patternMethod = pattern.substring(0, spaceIndex).toUpperCase();
        const patternPath = pattern.substring(spaceIndex + 1);

        if (patternMethod !== method) return false;

        const normalizedPath = (requestPath.split('?')[0] || requestPath).replace(/\/+$/, '');
        const normalizedPattern = patternPath.replace(/\/+$/, '');

        const pathSegments = normalizedPath.split('/').filter(Boolean);
        const patternSegments = normalizedPattern.split('/').filter(Boolean);

        for (let i = 0; i < patternSegments.length; i++) {
        const pSeg = patternSegments[i]!;

        if (pSeg === '*') {
            return true;
        }

        if (i >= pathSegments.length) {
            return false;
        }

        if (pSeg.startsWith(':')) {
            continue;
        }

        if (pSeg !== pathSegments[i]) {
            return false;
        }
        }

        return pathSegments.length === patternSegments.length;
    }
}

export default OAuthUtils;