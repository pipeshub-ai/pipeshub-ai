import type {
  AllowedRepositoryHost,
  AllowedWebsite,
  RepositoryPolicy,
  WebsitePolicy,
} from '../../control-plane';
import { envFlag } from '../../../libs/utils/env-flags';

type Env = Readonly<Record<string, string | undefined>>;

/** Whether the egress proxy opens host-level tunnels (`EGRESS_CONNECT_TUNNELS`): a site may then be host-level only. */
export const deploymentTunnels = (env: Env = process.env): boolean =>
  envFlag('EGRESS_CONNECT_TUNNELS', env);

/** The deployment's cap on allowed websites (WP-63): what any org may list. */
export const WEBSITES_CAP_ENV = 'HARNESS_ALLOWED_WEBSITES_CAP';
/** The deployment's cap on allowed git hosts (WP-63 part A): what any org may list for repository_fetch. */
export const REPOSITORIES_CAP_ENV = 'HARNESS_ALLOWED_REPOSITORIES_CAP';

/**
 * `HARNESS_ALLOWED_REPOSITORIES_CAP`: the deployment's cap, comma-separated
 * `host` (every public repository on it) or `host/owner/` and
 * `host/owner/repo` entries (only those); `none` lets no org list any git
 * host. Unset or blank: no cap. An entry that cannot be read throws, so Node
 * refuses to start rather than cap less than the operator meant.
 */
export function deploymentRepositoryCap(
  env: Env = process.env,
): RepositoryPolicy | null {
  const raw = (env[REPOSITORIES_CAP_ENV] ?? '').trim();
  if (raw === '') return null;
  if (raw.toLowerCase() === 'none') return { hosts: [] };
  const byHost = new Map<string, Set<string> | null>();
  for (const part of raw.split(',')) {
    const text = part.trim();
    const slash = text.indexOf('/');
    const host = (slash === -1 ? text : text.slice(0, slash)).toLowerCase();
    const prefix = slash === -1 ? null : text.slice(slash + 1);
    if (
      !/^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]*$/.test(host)
    ) {
      throw new Error(
        `${REPOSITORIES_CAP_ENV}: ${JSON.stringify(part)} is not a git host name`,
      );
    }
    if (
      prefix !== null &&
      !/^[A-Za-z0-9_.-]+(?:\/(?:[A-Za-z0-9_.-]+)?)?$/.test(prefix)
    ) {
      throw new Error(
        `${REPOSITORIES_CAP_ENV}: ${JSON.stringify(part)} is not host, host/owner/ or host/owner/repo`,
      );
    }
    const known = byHost.get(host);
    if (prefix === null || known === null) byHost.set(host, null);
    else byHost.set(host, new Set([...(known ?? []), prefix]));
  }
  const hosts: AllowedRepositoryHost[] = [...byHost].map(([host, prefixes]) =>
    prefixes === null ? { host } : { host, prefixes: [...prefixes] },
  );
  return { hosts };
}

/**
 * `HARNESS_ALLOWED_WEBSITES_CAP`: the deployment's cap, comma-separated
 * `host` (read only), `host=read-write` or `host=host-level` (an opaque
 * tunnel may be listed) entries, `*.domain` for
 * subdomains; `none` lets no org list any site. Unset or blank: no cap. An
 * entry that cannot be read throws, so Node refuses to start rather than cap
 * less than the operator meant.
 */
export function deploymentWebsiteCap(
  env: Env = process.env,
): WebsitePolicy | null {
  const raw = (env[WEBSITES_CAP_ENV] ?? '').trim();
  if (raw === '') return null;
  if (raw.toLowerCase() === 'none') return { sites: [] };
  const sites: AllowedWebsite[] = [];
  for (const part of raw.split(',')) {
    const [hostText, accessText] = part.trim().split('=', 2);
    const host = (hostText ?? '').trim().toLowerCase();
    const access = (accessText ?? 'read').trim();
    if (!/^(?:\*\.)?[a-z0-9.-]+\.[a-z][a-z0-9-]*$/.test(host)) {
      throw new Error(
        `${WEBSITES_CAP_ENV}: ${JSON.stringify(part)} is not a host name`,
      );
    }
    if (
      access !== 'read' &&
      access !== 'read-write' &&
      access !== 'host-level'
    ) {
      throw new Error(
        `${WEBSITES_CAP_ENV}: ${JSON.stringify(part)} access is read, read-write or host-level`,
      );
    }
    sites.push(
      access === 'read'
        ? { host, access }
        : {
            host,
            access: 'read-write',
            readWriteConfirmed: true,
            ...(access === 'host-level' ? { opaque: true as const } : {}),
          },
    );
  }
  return { sites };
}
