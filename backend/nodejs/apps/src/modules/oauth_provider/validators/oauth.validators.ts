import { z } from 'zod';
import { OAuthGrantType } from '../schema/oauth.app.schema';
import { messageForUnknownOption } from '../../../libs/utils/validation.utils';
import {
  guidCheck,
  integerCheck,
  urlCheck,
} from '../../../libs/utils/zod-formats';

// OAuth Provider Validators

/**
 * RFC 7636 PKCE code_verifier validation
 * Must be 43-128 characters, only [A-Za-z0-9-._~] allowed
 */
const codeVerifierPattern = /^[A-Za-z0-9\-._~]{43,128}$/;

/** Most ids one connected app's limit may hold. The limit is signed into each
 * token it is given, and a token travels in a request header. */
export const MAX_KNOWLEDGE_LIMIT_IDS = 50;

const limitSize = (limit: {
  apps: string[];
  recordGroups: string[];
  records: string[];
}): number =>
  limit.apps.length + limit.recordGroups.length + limit.records.length;

// Graph ids are lowercase; an upper-case copy would match nothing.
const uniqueIds = (ids: string[]): string[] => [
  ...new Set(ids.map((id) => id.toLowerCase())),
];

/** What a connected app may see, as its user picked it. Absent from a request
 * means everything the user can see; sent, it must name something. */
export const knowledgeLimitSchema = z
  .object({
    apps: z.array(z.string().check(guidCheck())).default([]),
    recordGroups: z.array(z.string().check(guidCheck())).default([]),
    records: z.array(z.string().check(guidCheck())).default([]),
  })
  .strict()
  .transform((limit) => ({
    apps: uniqueIds(limit.apps),
    recordGroups: uniqueIds(limit.recordGroups),
    records: uniqueIds(limit.records),
  }))
  .refine((limit) => limitSize(limit) > 0, {
    message:
      'Pick at least one source, or let the app see everything you can see',
  })
  .refine((limit) => limitSize(limit) <= MAX_KNOWLEDGE_LIMIT_IDS, {
    message: `Pick at most ${String(MAX_KNOWLEDGE_LIMIT_IDS)} sources, folders or items, or pick a whole source instead`,
  });

/**
 * RFC 7636 PKCE code_challenge validation (base64url encoded)
 * Must be 43-128 characters, only [A-Za-z0-9-_] allowed (no padding)
 */
const codeChallengePattern = /^[A-Za-z0-9\-_]{43,128}$/;

export const authorizeQuerySchema = z.object({
  query: z.object({
    response_type: z.enum(
      ['code'],
      messageForUnknownOption('Response type must be one of: code.'),
    ),
    client_id: z.string().min(1),
    redirect_uri: z.string().check(urlCheck()),
    scope: z.string().min(1),
    state: z.string().min(1),
    // RFC 7636: code_challenge must be base64url encoded (no padding)
    code_challenge: z
      .string()
      .regex(codeChallengePattern, 'Invalid code_challenge format')
      .optional(),
    // RFC 9700: only S256; `plain` exposes the verifier on the wire
    code_challenge_method: z
      .enum(
        ['S256'],
        messageForUnknownOption('Code challenge method must be one of: S256.'),
      )
      .optional(),
    nonce: z.string().optional(),
  }),
});

export const authorizeConsentSchema = z.object({
  body: z.object({
    client_id: z.string().min(1),
    redirect_uri: z.string().check(urlCheck()),
    scope: z.string().min(1),
    state: z.string().min(1),
    consent: z.enum(['granted', 'denied']),
    // RFC 7636: code_challenge must be base64url encoded (no padding)
    code_challenge: z
      .string()
      .regex(codeChallengePattern, 'Invalid code_challenge format')
      .optional(),
    // RFC 9700: only S256; `plain` exposes the verifier on the wire
    code_challenge_method: z
      .enum(
        ['S256'],
        messageForUnknownOption('Code challenge method must be one of: S256.'),
      )
      .optional(),
  }),
});

export const tokenSchema = z.object({
  body: z.object({
    grant_type: z.enum([
      OAuthGrantType.AUTHORIZATION_CODE,
      OAuthGrantType.CLIENT_CREDENTIALS,
      OAuthGrantType.REFRESH_TOKEN,
      OAuthGrantType.DEVICE_CODE,
      OAuthGrantType.TOKEN_EXCHANGE,
    ]),
    code: z.string().optional(),
    redirect_uri: z.string().check(urlCheck()).optional(),
    // RFC 6749: client_id is required but can also come from Authorization header
    client_id: z.string().min(1).optional(),
    client_secret: z.string().optional(),
    refresh_token: z.string().optional(),
    scope: z.string().optional(),
    // RFC 7636: code_verifier must match the pattern [A-Za-z0-9-._~]{43,128}
    code_verifier: z
      .string()
      .regex(codeVerifierPattern, 'Invalid code_verifier format')
      .optional(),
    device_code: z.string().min(1).optional(),
    // RFC 8693 token exchange. Bounded here so an oversized body is refused
    // before any of it is parsed as a token; `requested_subject` holds a user
    // id or an email address, so it is capped at the schema's email length.
    subject_token: z.string().min(1).max(8192).optional(),
    subject_token_type: z.string().max(128).optional(),
    requested_subject: z.string().min(1).max(320).optional(),
  }),
});

export const dcrRequestSchema = z.object({
  body: z.object({
    redirect_uris: z.array(z.string().min(1)).max(10).optional(),
    token_endpoint_auth_method: z
      .enum(['none', 'client_secret_basic', 'client_secret_post'])
      .optional(),
    grant_types: z.array(z.string()).optional(),
    response_types: z.array(z.string()).optional(),
    client_name: z.string().min(1).max(100).optional(),
    client_uri: z.string().check(urlCheck()).optional(),
    logo_uri: z.string().check(urlCheck()).optional(),
    scope: z.string().optional(),
    contacts: z.array(z.string()).optional(),
    tos_uri: z.string().check(urlCheck()).optional(),
    policy_uri: z.string().check(urlCheck()).optional(),
    software_id: z.string().optional(),
    software_version: z.string().optional(),
  }),
});

export const deviceAuthorizationSchema = z.object({
  body: z.object({
    client_id: z.string().min(1),
    scope: z.string().optional(),
  }),
});

export const deviceUserCodeSchema = z.object({
  body: z.object({
    user_code: z.string().min(1).max(32),
    consent: z.enum(['granted', 'denied']).optional(),
  }),
});

export const deviceConsentSchema = z.object({
  body: z.object({
    user_code: z.string().min(1).max(32),
    consent: z.enum(['granted', 'denied']),
  }),
});

export const revokeSchema = z.object({
  body: z.object({
    token: z.string().min(1),
    token_type_hint: z.enum(['access_token', 'refresh_token']).optional(),
    client_id: z.string().min(1),
    client_secret: z.string().optional(),
  }),
});

export const introspectSchema = z.object({
  body: z.object({
    token: z.string().min(1),
    token_type_hint: z.enum(['access_token', 'refresh_token']).optional(),
    client_id: z.string().min(1),
    client_secret: z.string().optional(),
  }),
});

// Developer Apps Validators

export const mongoIdRegex = /^[a-fA-F0-9]{24}$/;

export const appIdParamsSchema = z.object({
  params: z.object({
    appId: z.string().regex(mongoIdRegex, 'Invalid App ID'),
  }),
});

/**
 * `serviceAccountId: null` puts the app back to acting as its creator, which
 * is why null is accepted rather than the field simply being optional.
 */
export const setAppTokenIdentitySchema = z.object({
  params: z.object({
    appId: z.string().regex(mongoIdRegex, 'Invalid App ID'),
  }),
  body: z.object({
    serviceAccountId: z
      .string()
      .regex(mongoIdRegex, 'Invalid service account ID')
      .nullable(),
  }),
});

export const createAppSchema = z.object({
  body: z
    .object({
      name: z.string().min(1).max(100),
      description: z.string().max(500).optional(),
      redirectUris: z.array(z.string().check(urlCheck())).max(10).optional(),
      allowedGrantTypes: z
        .array(
          z.enum([
            OAuthGrantType.AUTHORIZATION_CODE,
            OAuthGrantType.CLIENT_CREDENTIALS,
            OAuthGrantType.REFRESH_TOKEN,
          ]),
        )
        .optional(),
      allowedScopes: z.array(z.string()).min(1),
      // Checked for real in the service, which can look the account up.
      // Shape only here.
      tokenIdentityUserId: z.string().min(1).optional(),
      homepageUrl: z.string().check(urlCheck()).optional(),
      privacyPolicyUrl: z.string().check(urlCheck()).optional(),
      termsOfServiceUrl: z.string().check(urlCheck()).optional(),
      isConfidential: z.boolean().optional(),
      accessTokenLifetime: z
        .number()
        .check(integerCheck())
        .min(300)
        .max(86400)
        .optional(),
      refreshTokenLifetime: z
        .number()
        .check(integerCheck())
        .min(3600)
        .max(31536000)
        .optional(),
      knowledgeLimit: knowledgeLimitSchema.optional(),
    })
    .refine(
      (data) => {
        const grantTypes = data.allowedGrantTypes || [
          OAuthGrantType.AUTHORIZATION_CODE,
          OAuthGrantType.REFRESH_TOKEN,
        ];
        if (grantTypes.includes(OAuthGrantType.AUTHORIZATION_CODE)) {
          return data.redirectUris && data.redirectUris.length >= 1;
        }
        return true;
      },
      {
        message:
          'At least one redirect URI is required when authorization_code grant type is enabled',
        path: ['redirectUris'],
      },
    ),
});

export const updateAppSchema = z.object({
  params: z.object({
    appId: z.string().regex(mongoIdRegex, 'Invalid App ID'),
  }),
  body: z
    .object({
      name: z.string().min(1).max(100).optional(),
      description: z.string().max(500).optional(),
      redirectUris: z.array(z.string().check(urlCheck())).max(10).optional(),
      allowedGrantTypes: z
        .array(
          z.enum([
            OAuthGrantType.AUTHORIZATION_CODE,
            OAuthGrantType.CLIENT_CREDENTIALS,
            OAuthGrantType.REFRESH_TOKEN,
          ]),
        )
        .optional(),
      allowedScopes: z.array(z.string()).min(1).optional(),
      homepageUrl: z.string().check(urlCheck()).optional().nullable(),
      privacyPolicyUrl: z.string().check(urlCheck()).optional().nullable(),
      termsOfServiceUrl: z.string().check(urlCheck()).optional().nullable(),
      accessTokenLifetime: z
        .number()
        .check(integerCheck())
        .min(300)
        .max(86400)
        .optional(),
      refreshTokenLifetime: z
        .number()
        .check(integerCheck())
        .min(3600)
        .max(31536000)
        .optional(),
      // null puts the app back to everything each user can see.
      knowledgeLimit: knowledgeLimitSchema.nullable().optional(),
    })
    .refine(
      (data) => {
        if (
          data.allowedGrantTypes?.includes(OAuthGrantType.AUTHORIZATION_CODE) &&
          data.redirectUris !== undefined
        ) {
          return data.redirectUris.length >= 1;
        }
        return true;
      },
      {
        message:
          'At least one redirect URI is required when authorization_code grant type is enabled',
        path: ['redirectUris'],
      },
    ),
});

export const listAppsQuerySchema = z.object({
  query: z.object({
    page: z
      .preprocess(
        (arg) => (arg === '' || arg === undefined ? 1 : Number(arg)),
        z.number().check(integerCheck()).min(1),
      )
      .optional(),
    limit: z
      .preprocess(
        (arg) => (arg === '' || arg === undefined ? 20 : Number(arg)),
        z.number().check(integerCheck()).min(1).max(100),
      )
      .optional(),
    status: z.enum(['active', 'suspended', 'revoked']).optional(),
    search: z.string().optional(),
  }),
});
