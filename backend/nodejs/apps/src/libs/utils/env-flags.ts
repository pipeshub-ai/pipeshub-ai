/**
 * The one boolean parse for the environment flags the agent harness depends on
 * (F3, contracts/harness/flags.json). `1/true/yes/on` and `0/false/no/off` in
 * any case; unset or blank is the flag's default (Compose renders `${X:-}` as
 * blank); anything else throws InvalidEnvFlagError naming the flag.
 * `validateEnvFlags` runs at startup so a typo stops the process instead of
 * reading as the default. Python's twin is backend/python/app/utils/env_flags.py.
 */

import { hardenedProfile, warnOnce } from './security-profile';

export type EnvFlagEnvironment = Readonly<Record<string, string | undefined>>;

/** Name -> the default when unset or blank. C-16 keeps this equal to flags.json. */
export const ENV_FLAG_DEFAULTS = {
  REPLICA_SET_AVAILABLE: false,
  AGENT_HARNESS_RELAY_ENABLED: true,
  // The agent harness's switch (ADR-OPS-06, amended 2026-11-20), on by default: false runs a release without it.
  AGENT_HARNESS_WORKER_ENABLED: true,
  HARNESS_OTEL: false,
  HARNESS_RECONCILE: false,
  HARNESS_FLOW_GUARD: true,
  HARNESS_RUN_HISTORY_BY_REFERENCE: true,
  // The instance-wide incident stop (CTL-5): on, every new chat takes the standard path; runs in flight finish.
  AGENT_HARNESS_KILL_SWITCH: false,
  // The egress proxy opens host-level tunnels (P5.4b); read by Node only as a deployment fact (`deploymentTunnels`).
  EGRESS_CONNECT_TUNNELS: false,
} as const;

export type EnvFlagName = keyof typeof ENV_FLAG_DEFAULTS;

/**
 * How a release without F3 read a value outside the boolean words, for the flags it had: on the compatibility
 * profile (ADR-OPS-07) such a value keeps that reading, with a warning, instead of stopping the process.
 * REPLICA_SET_AVAILABLE was `=== 'true'`. A flag not listed reads its default.
 */
const LEGACY_UNRECOGNISED: Readonly<Record<string, boolean>> = {
  REPLICA_SET_AVAILABLE: false,
};

const TRUE = new Set(['1', 'true', 'yes', 'on']);
const FALSE = new Set(['0', 'false', 'no', 'off']);

export class InvalidEnvFlagError extends Error {
  readonly reasonCode = 'invalid_env_flag';

  constructor(
    readonly flag: string,
    value: string,
  ) {
    super(
      `${flag}=${JSON.stringify(value)} is not a boolean: use one of 1/true/yes/on or 0/false/no/off`,
    );
    this.name = 'InvalidEnvFlagError';
  }
}

export function parseEnvFlag(
  name: string,
  raw: string | undefined,
  fallback: boolean,
  env: EnvFlagEnvironment = process.env,
): boolean {
  const value = (raw ?? '').trim().toLowerCase();
  if (value === '') return fallback;
  if (TRUE.has(value)) return true;
  if (FALSE.has(value)) return false;
  const error = new InvalidEnvFlagError(name, raw ?? '');
  if (hardenedProfile(env)) throw error;
  const reading = LEGACY_UNRECOGNISED[name] ?? fallback;
  warnOnce(
    `${error.message}. Read as ${String(reading)} because PIPESHUB_HARDENED is not set; the hardened profile refuses to start.`,
  );
  return reading;
}

export function envFlag(
  name: EnvFlagName,
  env: EnvFlagEnvironment = process.env,
): boolean {
  return parseEnvFlag(name, env[name], ENV_FLAG_DEFAULTS[name], env);
}

/** Refuses to start on a malformed flag on the hardened profile (throws InvalidEnvFlagError); warns otherwise. */
export function validateEnvFlags(env: EnvFlagEnvironment = process.env): void {
  for (const name of Object.keys(ENV_FLAG_DEFAULTS) as EnvFlagName[]) {
    envFlag(name, env);
  }
}
