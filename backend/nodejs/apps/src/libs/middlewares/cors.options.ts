import type { CorsOptions } from 'cors';
import { HttpMethod } from '../enums/http-methods.enum';

/** The app's CORS rule; `ALLOWED_ORIGINS` is read when the app is built. */
export const corsOptions = (
  env: Readonly<Record<string, string | undefined>> = process.env,
): CorsOptions => ({
  origin: env.ALLOWED_ORIGINS?.split(',') || ['http://localhost:3001'],
  credentials: true,
  exposedHeaders: ['x-session-token', 'content-disposition', 'etag'],
  methods: [
    HttpMethod.DELETE,
    HttpMethod.GET,
    HttpMethod.OPTIONS,
    HttpMethod.PATCH,
    HttpMethod.POST,
    HttpMethod.PUT,
  ],
  allowedHeaders: [
    'Content-Type',
    'Authorization',
    'x-session-token',
    'x-request-id',
    'client-name',
    // Revisions of control-plane documents (CPD-11): editors send what they read.
    'if-match',
    'if-none-match',
    'idempotency-key',
    // WP-100: the revision of the durable stream's frames the web app reads.
    'x-pipeshub-frames',
    // ADR-WEB-02: the web app asks for its refresh token as a cookie.
    'x-pipeshub-session',
    // ADR-WEB-02: which organisation's session a refresh or sign-out is for.
    'x-pipeshub-session-org',
  ],
});
