/**
 * Zod schemas for the public OAuth Dynamic Client Registration endpoints
 * (RFC 7591) and the optional client-management endpoints (RFC 7592).
 *
 * Wire format is snake_case throughout — these validate the raw request body
 * coming straight off Cursor / Claude / MCP Inspector. Do NOT translate into
 * camelCase here; that's the controller's job.
 */
import { z } from 'zod'
import { OAuthGrantType } from '../schema/oauth.app.schema'

const TOKEN_ENDPOINT_AUTH_METHODS = [
  'none',
  'client_secret_basic',
  'client_secret_post',
] as const

/**
 * RFC 7591 §2 redirect_uri rules — server enforces full URI validation in
 * `OAuthAppService.validateRedirectUrisPublic`. Here we only check shape.
 */
const redirectUriSchema = z
  .string()
  .min(1)
  .max(2048)
  .refine(
    (uri) => {
      try {
        // Accept any parseable URI; the service does scheme/host validation.
        // eslint-disable-next-line no-new
        new URL(uri)
        return true
      } catch {
        return false
      }
    },
    { message: 'redirect_uri must be a valid absolute URI' },
  )

/**
 * RFC 7591 §3.1 client metadata for POST /register. All fields optional —
 * server fills defaults — but redirect_uris is required when authorization_code
 * grant is in play (cross-checked in the service).
 */
export const registerBodySchema = z.object({
  body: z
    .object({
      client_name: z.string().min(1).max(100).optional(),
      redirect_uris: z.array(redirectUriSchema).min(1).max(10).optional(),
      grant_types: z
        .array(
          z.enum([
            OAuthGrantType.AUTHORIZATION_CODE,
            OAuthGrantType.CLIENT_CREDENTIALS,
            OAuthGrantType.REFRESH_TOKEN,
          ]),
        )
        .min(1)
        .max(5)
        .optional(),
      response_types: z.array(z.enum(['code'])).min(1).max(2).optional(),
      token_endpoint_auth_method: z.enum(TOKEN_ENDPOINT_AUTH_METHODS).optional(),
      scope: z.string().max(2048).optional(),
      client_uri: z.string().url().max(2048).optional(),
      logo_uri: z.string().url().max(2048).optional(),
      policy_uri: z.string().url().max(2048).optional(),
      tos_uri: z.string().url().max(2048).optional(),
      contacts: z.array(z.string().email()).max(10).optional(),
      software_id: z.string().max(100).optional(),
      software_version: z.string().max(50).optional(),
      application_type: z.enum(['native', 'web']).optional(),
    })
    .passthrough(), // RFC 7591 §3.1: server may ignore unknown fields, not reject.
})

const clientIdParamSchema = z.object({
  client_id: z.string().min(1).max(100),
})

export const registrationManagementGetSchema = z.object({
  params: clientIdParamSchema,
})

/** RFC 7592 §2.2 — same metadata shape as POST /register. */
export const registrationManagementPutSchema = z.object({
  params: clientIdParamSchema,
  body: registerBodySchema.shape.body,
})

export const registrationManagementDeleteSchema = z.object({
  params: clientIdParamSchema,
})
