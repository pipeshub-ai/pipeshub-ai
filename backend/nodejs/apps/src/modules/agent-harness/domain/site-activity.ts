/**
 * WP-63: a run's reach to the outside as its activity shows it, one plain
 * line per allowed website request and per refused one ("Read
 * docs.example.com/api: allowed, read-only", "Blocked upload to
 * docs.example.com/api (read-only)"). Made from `egress.allowed` and
 * the egress proxy's `gateway.refused`; never an id, a query or raw codes.
 */

export type SiteAccessShown = 'read' | 'read-write' | 'host-level';
export type SiteUse = 'read' | 'write';
/**
 * What the line says, for a frontend that words it in the person's language
 * (`label` is the English fallback): a read, a write or a host-level tunnel
 * at an allowed website; a block; an upload held, allowed or denied (the
 * upload card); a repository cloned; a clone or push the person denied.
 */
export type SiteActivityKind =
  | 'read'
  | 'write'
  | 'host_level'
  | 'blocked'
  | 'held'
  | 'upload_allowed'
  | 'upload_denied'
  | 'cloned'
  | 'clone_denied'
  | 'push_denied';

export interface SiteActivityView {
  kind: SiteActivityKind;
  host: string;
  /** The path as the proxy reported it (no query); absent for a refusal before a site was matched. */
  path?: string;
  access?: SiteAccessShown;
  use?: SiteUse;
  outcome: 'allowed' | 'blocked';
  /** The egress proxy's reason code of a block (shown as `label`, kept for filters). */
  reason?: string;
  /** A clone's size in bytes (`cloned`). */
  bytes?: number;
  /** The English line, for a frontend that has no wording of `kind` (and the logs). */
  label: string;
}

const where = (host: string, path?: string): string =>
  path === undefined || path === '/' ? host : `${host}${path}`;

const ACCESS_WORDS: Readonly<Record<SiteAccessShown, string>> = {
  read: 'read-only',
  'read-write': 'read and write',
  'host-level': 'host-level only',
};

/** The line for a request the proxy let through to an allowed website. */
export function allowedSiteLine(
  host: string,
  site: { path: string; access: SiteAccessShown; use: SiteUse },
): SiteActivityView {
  const at = where(host, site.path);
  const label =
    site.access === 'host-level'
      ? `Connected to ${host}: host-level only, not inspected`
      : site.use === 'write'
        ? `Sent data to ${at}: allowed, ${ACCESS_WORDS[site.access]}`
        : `Read ${at}: allowed, ${ACCESS_WORDS[site.access]}`;
  const kind: SiteActivityKind =
    site.access === 'host-level'
      ? 'host_level'
      : site.use === 'write'
        ? 'write'
        : 'read';
  return { kind, host, ...site, outcome: 'allowed', label };
}

/** The proxy's holds after a private read: the person is asked on the site card, nothing is refused for good. */
const HELD_REASONS: ReadonlySet<string> = new Set([
  'site_upload_held',
  'site_request_held',
]);

// A Map, not an object: a reason read from the proxy is never resolved through the prototype chain.
const BLOCKED: ReadonlyMap<string, (at: string) => string> = new Map(
  Object.entries({
    site_read_only: (at) => `Blocked upload to ${at} (read-only)`,
    site_path_not_allowed: (at) => `Blocked ${at}: outside the allowed paths`,
    host_mismatch: (at) => `Blocked a request to ${at} that named another site`,
    site_git_refused: (at) =>
      `Blocked git at ${at}: clone repositories with ph git clone`,
    // The site card: a site held after a private read, until the person allows it for the run (SEC-A-01).
    site_upload_held: (at) =>
      `Upload to ${at} is held until you allow it: this conversation read private data`,
    site_request_held: (at) =>
      `Request to ${at} is held until you allow it: this conversation read private data`,
    site_relay: (at) =>
      `Blocked ${at}: it passes on or shows what it is sent, so it needs read and write access`,
    destination_not_allowed: (at) => `Blocked ${at}: not an allowed website`,
    destination_private_address: (at) =>
      `Blocked ${at}: it points to a private address`,
    destination_unresolvable: (at) =>
      `Could not reach ${at}: the name does not resolve`,
    tunnel_ip_literal: (at) =>
      `Blocked ${at}: sites are reached by name, not address`,
    tunnel_refused: (at) => `Blocked ${at}: this connection is not allowed`,
    volume_cap_exceeded: (at) =>
      `Blocked ${at}: the run moved more data than allowed`,
    forbidden_pattern: (at) =>
      `Blocked a request to ${at} that carried a credential`,
    token_replayed: (at) =>
      `Blocked a request to ${at} that carried a credential`,
  } satisfies Record<string, (at: string) => string>),
);

/**
 * The line for a request the egress proxy refused, or null for one the
 * person needs no line for (a gateway's own refusals, a refusal with no host).
 */
export function blockedSiteLine(
  reason: string,
  host: string | undefined,
  site?: { path: string; access: SiteAccessShown; use: SiteUse },
): SiteActivityView | null {
  if (host === undefined) return null;
  const words = BLOCKED.get(reason);
  const at = where(host, site?.path);
  const label =
    typeof words === 'function' ? words(at) : `Blocked a request to ${at}`;
  const kind: SiteActivityKind = HELD_REASONS.has(reason) ? 'held' : 'blocked';
  return { kind, host, ...(site ?? {}), outcome: 'blocked', reason, label };
}

/** The site card's tool (WP-63, SEC-A-01): a held site the person allows for the run (or conversation), or not. */
export const SITE_UPLOAD_TOOL = 'site_upload';

/** The line for the person's answer on a site card. */
export function siteUploadDecisionLine(
  host: string,
  approved: boolean,
): SiteActivityView {
  return approved
    ? {
        kind: 'upload_allowed',
        host,
        access: 'read-write',
        outcome: 'allowed',
        label: `Requests to ${host} allowed`,
      }
    : {
        kind: 'upload_denied',
        host,
        access: 'read-write',
        outcome: 'blocked',
        reason: 'interaction_denied',
        label: `Requests to ${host} denied by you`,
      };
}
