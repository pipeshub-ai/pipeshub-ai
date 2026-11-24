/**
 * Identity helpers for product-behaviour events.
 *
 * Events are tagged with the caller's email-domain so adoption / funnel /
 * engagement dashboards can exclude internal (PipesHub) traffic. Email/domain
 * are identity, not document/query content, so they are allowed by the
 * collector.
 */
import type { RequestClient } from '../../libs/platform-events/events';

export { emailDomain as domainFromEmail } from '../../libs/utils/request-client.utils';

export function normalizeOrgId(orgId: unknown): string {
  if (orgId == null) return 'unknown';
  const str = String(orgId);
  return str !== '' && str !== '[object Object]' ? str : 'unknown';
}

/** Which client and which of our channels sent a request, as event props. */
export function clientTags(client: RequestClient): {
  user_agent?: string;
  channel?: string;
} {
  return {
    ...(client.userAgent !== undefined ? { user_agent: client.userAgent } : {}),
    ...(client.channel !== undefined ? { channel: client.channel } : {}),
  };
}
