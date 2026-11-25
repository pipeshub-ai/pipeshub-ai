import type { HarnessRun } from './execution';

// A leaf module: run-spec needs it at runtime, and run-spec importing execution's values would close the
// run-spec -> execution -> child-runs -> worker-compat -> run-spec cycle, leaving worker-compat's constants
// undefined whenever run-spec loads first.

/** Why a root run exists: a person's turn, a schedule's firing (P8a), or a person's `/run` (SLASH3). */
export type AdmittedRunKind = 'interactive' | 'scheduled' | 'command';

/** The kind `run.admitted` and the pinned spec name for a root run. */
export const admittedRunKind = (
  run: Pick<HarnessRun, 'input'>,
  scheduled: boolean,
): AdmittedRunKind =>
  scheduled
    ? 'scheduled'
    : run.input?.sandboxCommand !== undefined
      ? 'command'
      : 'interactive';
