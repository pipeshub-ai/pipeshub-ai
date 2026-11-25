import type { SiteActivityView } from './site-activity';

/**
 * WP-63 part A: a repository the worker fetched for the run, or one the
 * person refused, as a plain activity line beside the run's website lines
 * ("Cloned github.com/onyx-dot-app/onyx (42 MB, read-only)", "Clone denied
 * by you"). Made from `x.pipeshub.repository.fetched` and the denial of a
 * `repository_fetch` approval; never an id, a commit or raw codes.
 */
export const REPOSITORY_FETCHED = 'x.pipeshub.repository.fetched';
/** A clone refused because its host is not on the org's list: the chat's "Clone not available" card. */
export const REPOSITORY_UNAVAILABLE = 'x.pipeshub.repository.unavailable';

/** The "Clone not available" card's facts: the git host and the repository's path (`/owner/repo`). */
export interface RepositoryUnavailableView {
  host: string;
  path: string;
  reason: 'host_not_allowed';
}

/** The card for a refused clone, or null for a malformed record. */
export function unavailableRepository(
  data: Record<string, unknown>,
): RepositoryUnavailableView | null {
  const { host, path, reason } = data;
  if (
    typeof host !== 'string' ||
    typeof path !== 'string' ||
    reason !== 'host_not_allowed'
  )
    return null;
  return { host, path, reason };
}
export const REPOSITORY_FETCH_TOOL = 'repository_fetch';
export const REPOSITORY_PUSH_TOOL = 'repository_push';

const MIB = 1024 * 1024;

const megabytes = (bytes: number): string =>
  `${String(Math.max(1, Math.round(bytes / MIB)))} MB`;

/** The line for a fetched repository, or null for a malformed record. */
export function fetchedRepositoryLine(
  data: Record<string, unknown>,
): SiteActivityView | null {
  const host = data['host'];
  const repository = data['repository'];
  const bytes = data['bytes'];
  if (
    typeof host !== 'string' ||
    typeof repository !== 'string' ||
    typeof bytes !== 'number'
  )
    return null;
  return {
    kind: 'cloned',
    host,
    path: `/${repository}`,
    access: 'read',
    use: 'read',
    outcome: 'allowed',
    bytes,
    label: `Cloned ${host}/${repository} (${megabytes(bytes)}, read-only)`,
  };
}

/** Whether an approval's tool is the repository fetch (an engine names it bare or with its server's prefix). */
export const isRepositoryTool = (
  toolName: string | undefined,
  tool: string,
): boolean =>
  toolName !== undefined &&
  (toolName === tool ||
    toolName.endsWith(`__${tool}`) ||
    toolName.endsWith(`_${tool}`));

/**
 * The line for a clone or push the person refused on its card. The card's
 * destination names the repository ("github.com/o/r (public, read-only, …)",
 * or "pipeshub/session-… on github.com/o/r" for a push): its host and path.
 */
export function deniedRepositoryLine(
  tool: string,
  destination: string | undefined,
): SiteActivityView {
  const named =
    (destination ?? '').split(' on ').at(-1)?.split(' (')[0]?.trim() ?? '';
  const slash = named.indexOf('/');
  const host = slash === -1 ? named : named.slice(0, slash);
  const path = slash === -1 ? undefined : named.slice(slash);
  const push = tool === REPOSITORY_PUSH_TOOL;
  return {
    kind: push ? 'push_denied' : 'clone_denied',
    host,
    ...(path !== undefined && path.length > 1 ? { path } : {}),
    access: push ? 'read-write' : 'read',
    outcome: 'blocked',
    reason: 'interaction_denied',
    label: push ? 'Push denied by you' : 'Clone denied by you',
  };
}
