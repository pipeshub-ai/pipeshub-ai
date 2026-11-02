/**
 * Identity helpers for product-behaviour events.
 *
 * Events are tagged with the caller's email-domain so adoption / funnel /
 * engagement dashboards can exclude internal (PipesHub) traffic. Email/domain
 * are identity, not document/query content, so they are allowed by the
 * collector.
 */

import type { IncomingHttpHeaders } from 'http';

/** Lower-cased domain part of an email, or `"unknown"` when absent. */
export function domainFromEmail(email: string | undefined | null): string {
  if (!email || !email.includes('@')) return 'unknown';
  return email.split('@').pop()?.trim().toLowerCase() || 'unknown';
}

export function normalizeOrgId(orgId: unknown): string {
  if (orgId == null) return 'unknown';
  const str = String(orgId);
  return str !== '' && str !== '[object Object]' ? str : 'unknown';
}

/** The first product token of a User-Agent (`claude-code/2.1.0`), never the
 *  whole header, which can carry machine and OS details. */
function productToken(header: unknown): string | undefined {
  if (typeof header !== 'string') return undefined;
  const token = header.trim().split(/\s+/)[0] ?? '';
  // Name with at most one `/version`: no `@` (an address) and no extra path.
  return /^(?=.{1,80}$)[\w.+-]+(?:\/[\w.+-]+)?$/.test(token)
    ? token
    : undefined;
}

/** A channel tag (`X-PipesHub-Channel`, or `?ref=` on a signup link), set by
 *  PipesHub's own clients and links. Anything else is dropped rather than
 *  stored as typed. */
export function channelOf(value: unknown): string | undefined {
  return typeof value === 'string' && /^[a-z0-9][a-z0-9-]{0,39}$/.test(value)
    ? value
    : undefined;
}

/** Which client and which of our channels sent a request, as event props. */
export function clientTags(headers: IncomingHttpHeaders): {
  user_agent?: string;
  channel?: string;
} {
  const userAgent = productToken(headers['user-agent']);
  const channel = channelOf(headers['x-pipeshub-channel']);
  return {
    ...(userAgent !== undefined ? { user_agent: userAgent } : {}),
    ...(channel !== undefined ? { channel } : {}),
  };
}

/** Where a signup came from: the link's `ref` tag when it is well formed,
 *  otherwise `direct` with the referring site's domain (never its full URL). */
export function signupSource(body: {
  ref?: unknown;
  referrerDomain?: unknown;
}): { channel: string; referrerDomain?: string } {
  const channel = channelOf(body.ref);
  if (channel !== undefined) return { channel };
  const domain =
    typeof body.referrerDomain === 'string'
      ? body.referrerDomain.toLowerCase()
      : '';
  return /^(?=.{1,253}$)[a-z0-9-]+(?:\.[a-z0-9-]+)+$/.test(domain)
    ? { channel: 'direct', referrerDomain: domain }
    : { channel: 'direct' };
}
