import { ToolsetOAuthCallbackClient } from './toolset-oauth-callback-client';

/**
 * Single toolset OAuth callback page for static export (no per-slug HTML at build time).
 * Slugs in the path are handled via `next.config` rewrites (dev) and `public/_redirects` (Netlify).
 */
export default function ToolsetOAuthCallbackPage() {
  return <ToolsetOAuthCallbackClient />;
}
