import { z } from 'zod'

// OAuth Provider Validators

/**
 * RFC 7636 PKCE code_verifier validation
 * Must be 43-128 characters, only [A-Za-z0-9-._~] allowed
 */
const codeVerifierPattern = /^[A-Za-z0-9\-._~]{43,128}$/

/**
 * RFC 7636 PKCE code_challenge validation (base64url encoded)
 * Must be 43-128 characters, only [A-Za-z0-9-_] allowed (no padding)
 */
const codeChallengePattern = /^[A-Za-z0-9\-_]{43,128}$/

export const authorizeQuerySchema = z.object({
  query: z.object({
    response_type: z.enum(['code']),
    client_id: z.string().min(1),
    redirect_uri: z.string().url(),
    scope: z.string().min(1),
    state: z.string().min(1),
    // RFC 7636: code_challenge must be base64url encoded (no padding)
    code_challenge: z.string().regex(codeChallengePattern, 'Invalid code_challenge format').optional(),
    code_challenge_method: z.enum(['S256', 'plain']).optional(),
    nonce: z.string().optional(),
  }),
})

export const authorizeConsentSchema = z.object({
  body: z.object({
    client_id: z.string().min(1),
    redirect_uri: z.string().url(),
    scope: z.string().min(1),
    state: z.string().min(1),
    consent: z.enum(['granted', 'denied']),
    // RFC 7636: code_challenge must be base64url encoded (no padding)
    code_challenge: z.string().regex(codeChallengePattern, 'Invalid code_challenge format').optional(),
    code_challenge_method: z.enum(['S256', 'plain']).optional(),
  }),
})

export const tokenSchema = z.object({
  body: z.object({
    grant_type: z.enum([
      'authorization_code',
      'client_credentials',
      'refresh_token',
    ]),
    code: z.string().optional(),
    redirect_uri: z.string().url().optional(),
    // RFC 6749: client_id is required but can also come from Authorization header
    client_id: z.string().min(1).optional(),
    client_secret: z.string().optional(),
    refresh_token: z.string().optional(),
    scope: z.string().optional(),
    // RFC 7636: code_verifier must match the pattern [A-Za-z0-9-._~]{43,128}
    code_verifier: z.string().regex(codeVerifierPattern, 'Invalid code_verifier format').optional(),
  }),
})

export const revokeSchema = z.object({
  body: z.object({
    token: z.string().min(1),
    token_type_hint: z.enum(['access_token', 'refresh_token']).optional(),
    client_id: z.string().min(1),
    client_secret: z.string().optional(),
  }),
})

export const introspectSchema = z.object({
  body: z.object({
    token: z.string().min(1),
    token_type_hint: z.enum(['access_token', 'refresh_token']).optional(),
    client_id: z.string().min(1),
    client_secret: z.string().optional(),
  }),
})

// Developer Apps Validators

const mongoIdRegex = /^[a-fA-F0-9]{24}$/

export const appIdParamsSchema = z.object({
  params: z.object({
    appId: z.string().regex(mongoIdRegex, 'Invalid App ID'),
  }),
})

export const createAppSchema = z.object({
  body: z.object({
    name: z.string().min(1).max(100),
    description: z.string().max(500).optional(),
    redirectUris: z.array(z.string().url()).min(1).max(10),
    allowedGrantTypes: z
      .array(
        z.enum(['authorization_code', 'client_credentials', 'refresh_token']),
      )
      .optional(),
    allowedScopes: z.array(z.string()).min(1),
    homepageUrl: z.string().url().optional(),
    privacyPolicyUrl: z.string().url().optional(),
    termsOfServiceUrl: z.string().url().optional(),
    isConfidential: z.boolean().optional(),
    accessTokenLifetime: z.number().int().min(300).max(86400).optional(),
    refreshTokenLifetime: z.number().int().min(3600).max(31536000).optional(),
  }),
})

export const updateAppSchema = z.object({
  params: z.object({
    appId: z.string().regex(mongoIdRegex, 'Invalid App ID'),
  }),
  body: z.object({
    name: z.string().min(1).max(100).optional(),
    description: z.string().max(500).optional(),
    redirectUris: z.array(z.string().url()).min(1).max(10).optional(),
    allowedGrantTypes: z
      .array(
        z.enum(['authorization_code', 'client_credentials', 'refresh_token']),
      )
      .optional(),
    allowedScopes: z.array(z.string()).min(1).optional(),
    homepageUrl: z.string().url().optional().nullable(),
    privacyPolicyUrl: z.string().url().optional().nullable(),
    termsOfServiceUrl: z.string().url().optional().nullable(),
    accessTokenLifetime: z.number().int().min(300).max(86400).optional(),
    refreshTokenLifetime: z.number().int().min(3600).max(31536000).optional(),
  }),
})

export const listAppsQuerySchema = z.object({
  query: z.object({
    page: z
      .preprocess(
        (arg) => (arg === '' || arg === undefined ? 1 : Number(arg)),
        z.number().int().min(1),
      )
      .optional(),
    limit: z
      .preprocess(
        (arg) => (arg === '' || arg === undefined ? 20 : Number(arg)),
        z.number().int().min(1).max(100),
      )
      .optional(),
    status: z.enum(['active', 'suspended', 'revoked']).optional(),
    search: z.string().optional(),
  }),
})
