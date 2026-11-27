/**
 * Coding engines' sandbox floor (`policy/release.json` `engineIsolation`),
 * as Python's `harness_core.engines.isolation`.
 * The release floor is gVisor: a production install whose broker offers no
 * runtime at or above it refuses engines with the admin's sentence naming the
 * install guide. The documented dev/test override (its variable exactly
 * `true`) lowers the floor; admins then see a persistent warning.
 */
import {
  POLICY_DIMENSIONS,
  RELEASE_DEFAULTS,
} from '../contracts/generated/core/policy-data';

const ISOLATION = RELEASE_DEFAULTS.engineIsolation;

export type IsolationMode = typeof RELEASE_DEFAULTS.engineProfile.isolationMin;

/** The variable that turns the dev/test override on. */
export const ENGINES_WITHOUT_GVISOR_ENV = ISOLATION.devOverride.env;
export const GVISOR_INSTALL_GUIDE = ISOLATION.installGuide;
export const GVISOR_REQUIRED_MESSAGE = `Coding engines need the gVisor sandbox runtime on this server — install guide: ${GVISOR_INSTALL_GUIDE}`;

/** Whether `env` turns the override on: exactly `true` (any case), so a typo stays production. */
export function enginesWithoutGvisor(
  env: Readonly<Record<string, string | undefined>> = process.env,
): boolean {
  return env[ENGINES_WITHOUT_GVISOR_ENV]?.trim().toLowerCase() === 'true';
}

/** The release's floor, before any override: gVisor. */
export const RELEASE_ISOLATION_FLOOR: IsolationMode =
  RELEASE_DEFAULTS.engineProfile.isolationMin;

/** The floor every release engine profile carries on this install. */
export function releaseIsolationFloor(
  env: Readonly<Record<string, string | undefined>> = process.env,
): IsolationMode {
  return enginesWithoutGvisor(env)
    ? ISOLATION.devOverride.isolationMin
    : RELEASE_DEFAULTS.engineProfile.isolationMin;
}

const ISOLATION_RANKS: ReadonlyMap<string, number> = (() => {
  const row = POLICY_DIMENSIONS.dimensions.find(
    (d) => d.path === 'engineProfile.isolationMin',
  );
  const ranks = new Map<string, number>();
  (row?.order?.ranks ?? []).forEach((group, index) => {
    for (const mode of group) ranks.set(mode, index);
  });
  return ranks;
})();

/**
 * Whether a broker offering `offered` can run an engine whose floor is
 * `floor`: some offered runtime ranks at or above it (never a weaker one).
 */
export function offersFloor(
  offered: ReadonlySet<string>,
  floor: string,
): boolean {
  const needed = ISOLATION_RANKS.get(floor);
  if (needed === undefined) return false;
  return [...offered].some(
    (mode) => (ISOLATION_RANKS.get(mode) ?? -1) >= needed,
  );
}
