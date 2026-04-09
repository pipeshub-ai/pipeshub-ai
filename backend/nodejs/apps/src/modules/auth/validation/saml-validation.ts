import { z } from 'zod';

/** GET /signIn — optional query used to build RelayState (see SamlController.signInViaSAML). */
export const SamlSignInValidationSchema = z.object({
  body: z.object({}),
  query: z
    .object({
      email: z.string().optional(),
      sessionToken: z.string().optional(),
    })
    .passthrough(),
  params: z.object({}),
  headers: z.object({}),
});

/** POST /signIn/callback — SP ACS: form POST with SAMLResponse (and optional RelayState). */
export const SamlSignInCallbackValidationSchema = z.object({
  body: z
    .object({
      SAMLResponse: z.string().min(1, 'SAMLResponse is required'),
      RelayState: z.string().optional(),
    })
    .passthrough(),
  query: z.object({}).passthrough(),
  params: z.object({}),
  headers: z.object({}),
});

/** POST /updateAppConfig — reload app config; no JSON body (scoped token in Authorization). */
export const SamlUpdateAppConfigValidationSchema = z.object({
  body: z.object({}),
  query: z.object({}),
  params: z.object({}),
  headers: z.object({}),
});
