/**
 * The security profile (ADR-OPS-07): one switch, `PIPESHUB_HARDENED`, decides
 * whether this install runs the hardened defaults the security programme added
 * or keeps the behaviour of a release without them (compatibility). Unset
 * reads compatibility, so an upgraded install, a plain `docker compose up`
 * with an older `.env`, and a from-source setup behave as before; `install.sh`
 * writes `true` on a fresh install. Each behaviour the switch decides is in
 * docs/agent-harness-v2/guides/HARNESS-OFF.md ("Security profile").
 *
 * The switch itself never stops a process: a value that is not a boolean
 * reads compatibility with a warning, because refusing to start is the kind of
 * upgrade break the profile exists to prevent. Python's twin is
 * backend/python/app/utils/security_profile.py.
 */

export const HARDENED_ENV = 'PIPESHUB_HARDENED';

type Env = Readonly<Record<string, string | undefined>>;

const TRUE = new Set(['1', 'true', 'yes', 'on']);
const FALSE = new Set(['0', 'false', 'no', 'off']);

const warned = new Set<string>();

/** One process-wide warning per message: a compatibility fallback is logged once, not per request. */
export function warnOnce(message: string): void {
  if (warned.has(message)) return;
  warned.add(message);
  process.emitWarning(message, { code: 'PIPESHUB_COMPAT' });
}

/** Tests only: forget which warnings were given. */
export function resetCompatWarningsForTests(): void {
  warned.clear();
}

export function hardenedProfile(env: Env = process.env): boolean {
  const raw = (env[HARDENED_ENV] ?? '').trim().toLowerCase();
  if (raw === '' || FALSE.has(raw)) return false;
  if (TRUE.has(raw)) return true;
  warnOnce(
    `${HARDENED_ENV}=${JSON.stringify(env[HARDENED_ENV] ?? '')} is not a boolean: running the compatibility profile. ` +
      'Set it to true to adopt the hardened profile.',
  );
  return false;
}

export type SecurityProfile = 'hardened' | 'compatibility';

export const securityProfile = (env: Env = process.env): SecurityProfile =>
  hardenedProfile(env) ? 'hardened' : 'compatibility';
