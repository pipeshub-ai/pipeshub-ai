/**
 * Identity helpers for product-behaviour events.
 *
 * Events are tagged with the caller's email-domain so adoption / funnel /
 * engagement dashboards can exclude internal (PipesHub) traffic. Email/domain
 * are identity, not document/query content, so they are allowed by the
 * collector.
 */

/** Lower-cased domain part of an email, or `"unknown"` when absent. */
export function domainFromEmail(email: string | undefined | null): string {
  if (!email || !email.includes('@')) return 'unknown';
  return email.split('@').pop()?.trim().toLowerCase() || 'unknown';
}
