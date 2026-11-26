import { envFlag } from '../../../libs/utils/env-flags';

/**
 * `CONTROL_PLANE_PIN` (05 §1 rule 6): `off`, `shadow` (the control plane
 * resolves beside today's resolver and counts disagreements, acting on none)
 * or `on` (the pin decides, CPD-5, and CPD-7's visibility rule applies at
 * admission). Unset is `on` (C4.04): the shadow release recorded every gate
 * of the switch (`CONTROL_PLANE_PIN_GATES`), and `shadow` then `off` are the
 * operator's way back. Read once at boot; a value it does not know refuses to
 * start rather than run with a guess.
 */
export const CONTROL_PLANE_PIN_ENV = 'CONTROL_PLANE_PIN';
export const CONTROL_PLANE_PIN_MODES = ['off', 'shadow', 'on'] as const;
export type ControlPlanePin = (typeof CONTROL_PLANE_PIN_MODES)[number];

export class InvalidControlPlanePinError extends Error {
  readonly reason = 'invalid_control_plane_pin';
}

/**
 * The composition passes its environment: the control plane reads no env of its own (C-16 lists the reader).
 * Unset follows the agent harness's switch (ADR-OPS-06): with the harness off a chat turn takes the path a
 * release without the harness takes, which no pin resolved.
 */
export function parseControlPlanePin(
  env: Readonly<Record<string, string | undefined>>,
  harnessOn: boolean = envFlag('AGENT_HARNESS_WORKER_ENABLED', env),
): ControlPlanePin {
  const value = (env[CONTROL_PLANE_PIN_ENV] ?? '').trim().toLowerCase();
  if (value === '') return harnessOn ? 'on' : 'off';
  if ((CONTROL_PLANE_PIN_MODES as readonly string[]).includes(value)) {
    return value as ControlPlanePin;
  }
  throw new InvalidControlPlanePinError(
    `${CONTROL_PLANE_PIN_ENV}=${JSON.stringify(env[CONTROL_PLANE_PIN_ENV])} is not one of ${CONTROL_PLANE_PIN_MODES.join(', ')}`,
  );
}

/**
 * What had to hold before `on` became the default (05-migration-plan, CPD-5
 * row), each recorded: shadow mismatches 0 on the fault catalog
 * (impl/CPD-10-hot-path.md §12.6), pin-replay determinism (CPD5-23), C-24's
 * wire parity, NFR-1 p95 3.75 ms (CPD-5 PIN-27), and the worker reading the
 * pin (CPD-6).
 */
export const CONTROL_PLANE_PIN_GATES: readonly string[] = [
  'shadow mismatch 0 on the replay corpus and the e2e lanes',
  'pin-replay determinism',
  'C-24 wire parity unchanged',
  'NFR-1 pin benchmark p95 <= 8 ms',
  'CPD-6: the worker reads the pin',
];
