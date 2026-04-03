import { z } from "zod";

const otpGenerationBody = z.object({
    email: z.string().email('Invalid email'),
    'cf-turnstile-response': z.string().optional(), // Add Turnstile token for forgot password
});
  
export const otpGenerationValidationSchema = z.object({
    body: otpGenerationBody,
    query: z.object({}),
    params: z.object({}),
    headers: z.object({}),
});

const initAuthBody = z.object({
email: z
    .string()
    //.min(1, 'Email is required')
    .max(254, 'Email address is too long') // RFC 5321 limit
    .email('Invalid email format').optional(),
});

export const initAuthValidationSchema = z.object({
    body: initAuthBody,
    query: z.object({}),
    params: z.object({}),
    headers: z.object({}),
});

/**
 * Object shape for `credentials` on POST /authenticate. Fields sourced from:
 * - `userAccount.controller.ts` — password/otp; Google `credential` or string token; Azure/Microsoft/OAuth `accessToken` / `idToken`
 * - `azureAdTokenValidation.ts` — `idToken`, `accessToken` for Microsoft / Azure AD validation
 * - `frontend/src/auth/context/jwt/action.ts` — password, otp, Google credential(s), Azure/Microsoft/OAuth payloads
 *
 * `token` / `code` are optional for PKCE-style or legacy clients; not read in `userAccount.controller` today.
 */
const authenticateCredentialsObjectSchema = z
    .object({
        password: z.string().optional(),
        otp: z.string().optional(),
        token: z.string().optional(),
        code: z.string().optional(),
        accessToken: z.string().optional(),
        idToken: z.string().optional(),
        credential: z.string().optional(),
    })

const authenticateBody = z.object({
    method: z.string().min(1, 'Authentication method is required'),
    credentials: z.union([
        z.string().min(1, 'Credentials cannot be empty'),
        authenticateCredentialsObjectSchema,
    ]),
    email: z
        .string()
        .max(254, 'Email address is too long') // RFC 5321 limit
        .email('Invalid email format')
        .optional(),
    'cf-turnstile-response': z.string().optional(), // Add Turnstile token
}).strict();

export const authenticateValidationSchema = z.object({
    body: authenticateBody,
    query: z.object({}),
    params: z.object({}),
    headers: z.object({}),
});

export const resetPasswordValidationSchema = z.object({
body: z.object({
    currentPassword: z.string(),
    newPassword: z.string(),
    'cf-turnstile-response': z.string().optional(),
}),
});

/** POST /oauth/exchange — authorization code exchange (see `exchangeOAuthToken`) */
const oauthExchangeBody = z.object({
  code: z.string().min(1, 'Authorization code is required'),
  provider: z.string().min(1, 'Provider is required'),
  redirectUri: z.string().min(1, 'Redirect URI is required'),
});

export const oauthExchangeValidationSchema = z.object({
  body: oauthExchangeBody,
  query: z.object({}),
  params: z.object({}),
  headers: z.object({}),
});

// ==============================================================
// Responses
// ==============================================================

/**
 * Public auth-provider blobs returned on init / multi-step authenticate.
 * Sources:
 * - `userAccount.controller.ts` — `initAuth`, `authenticate` (multi-step): assigns
 *   `authProviders.<google|microsoft|azuread|oauth|saml>` from CM `getConfig(...).data`;
 *   OAuth JIT responses omit `clientSecret`, `tokenEndpoint`, `userInfoEndpoint`.
 * - `pipeshub-openapi.yaml` — `components.schemas.AuthProviders`
 *
 * `.passthrough()` keeps validation tolerant of extra fields from the configuration service.
 */
const GoogleAuthProviderPublicSchema = z
    .object({
        clientId: z.string().optional(),
        enableJit: z.boolean().optional(),
    })

const MicrosoftOrAzureAdAuthProviderPublicSchema = z
    .object({
        tenantId: z.string().optional(),
        clientId: z.string().optional(),
        enableJit: z.boolean().optional(),
    })

const OauthAuthProviderPublicSchema = z
    .object({
        providerName: z.string(),
        clientId: z.string(),
        tokenEndpoint: z.string(),
        authorizationUrl: z.string(),
        clientSecret: z.string().optional(),
        userInfoEndpoint: z.string().optional(),
        scope: z.string().optional(),
        enableJit: z.boolean().optional(),
    })

/** SAML entry is CM-defined; only require a plain object. */
const SamlAuthProviderPublicSchema = z.object({}).passthrough();

export const AuthProvidersSchema = z
    .object({
        google: GoogleAuthProviderPublicSchema.optional(),
        microsoft: MicrosoftOrAzureAdAuthProviderPublicSchema.optional(),
        azuread: MicrosoftOrAzureAdAuthProviderPublicSchema.optional(),
        oauth: OauthAuthProviderPublicSchema.optional(),
        saml: SamlAuthProviderPublicSchema.optional(),
    })

/** POST /initAuth — 200 JSON response body */
export const InitAuthResponseSchema = z.object({
    currentStep: z.number().int().nonnegative(),
    allowedMethods: z.array(z.string()),
    message: z.string(),
    authProviders: AuthProvidersSchema,
    jitEnabled: z.boolean(),
});

/** POST /authenticate — 200 JSON when more auth steps remain */
export const AuthenticateMultiStepResponseSchema = z.object({
    status: z.literal('success'),
    nextStep: z.number().int().nonnegative(),
    allowedMethods: z.array(z.string()),
    authProviders: AuthProvidersSchema,
});

/** POST /authenticate — 200 JSON when all auth steps are complete */
export const AuthenticateResponseSchema = z.object({
    message: z.string(),
    accessToken: z.string().min(1),
    refreshToken: z.string().min(1),
});

/** POST /login/otp/generate — 200 */
export const LoginOtpGenerateResponseSchema = z.object({
    message: z.string(),
});

/** POST /password/reset — 200 (logged-in user; includes new access token) */
export const ResetPasswordResponseSchema = z.object({
    data: z.string().min(1),
    accessToken: z.string().min(1),
});

/** `{ data: non-empty string }` — POST /password/forgot, POST /password/reset/token */
export const DataStringResponseSchema = z.object({
    data: z.string().min(1),
});

/**
 * User JSON from IAM `getUserById` on POST /refresh/token — matches `users` collection + Mongoose JSON.
 */
export const RefreshTokenUserSchema = z
    .object({
        _id: z.string().min(1),
        orgId: z.string().min(1),
        email: z.string().min(1),
        fullName: z.string(),
        hasLoggedIn: z.boolean(),
        isDeleted: z.boolean(),
        slug: z.string(),
        createdAt: z.string(),
        updatedAt: z.string(),
        __v: z.number(),
    });

/** POST /refresh/token — 200 */
export const RefreshTokenResponseSchema = z.object({
    user: RefreshTokenUserSchema,
    accessToken: z.string().min(1),
});

/** GET /internal/password/check — 200 */
export const HasPasswordMethodResponseSchema = z.object({
    isPasswordAuthEnabled: z.boolean(),
});

/** POST /oauth/exchange — 200 */
export const OAuthExchangeResponseSchema = z.object({
    access_token: z.string().min(1),
    id_token: z.string().optional(),
    token_type: z.string(),
    expires_in: z.number().optional(),
});

/** PUT /validateEmailChange — 200 */
export const ValidateEmailChangeResponseSchema = z.object({
    message: z.string().min(1),
});
