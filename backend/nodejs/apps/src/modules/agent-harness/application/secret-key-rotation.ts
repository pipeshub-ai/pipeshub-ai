import { detachedInterval } from '../../../libs/context/orgContext';
import type { LeaderLease } from '../ports/leader-lease';

/**
 * Rotating `SECRET_KEY` (ADR-KEY-01): one command moves everything the
 * deployment sealed under `SECRET_KEY_PREVIOUS` to `SECRET_KEY`, step by step.
 * No step keeps a checkpoint: each one finds what is left from the data itself
 * (a wrap's `kekId`, the key a ciphertext opens under) and moves it with a
 * compare-and-set, so a run stopped anywhere is resumed by running it again,
 * and two runs that overlap can only repeat each other's work, never undo it.
 */

export type RotationMode = 'dry-run' | 'rotate' | 'verify' | 'retire';

/** What one step found; counts only, never a value or a key. */
export interface StepReport {
  readonly step: string;
  /** Items the step holds that are sealed under a key at all. */
  total: number;
  /** Under `SECRET_KEY`. */
  current: number;
  /** Still under `SECRET_KEY_PREVIOUS` (what a rotate moves). */
  previous: number;
  /** Moved to `SECRET_KEY` by this run. */
  moved: number;
  /** Under neither key: a wrong `SECRET_KEY_PREVIOUS`, or data from another deployment. */
  unreadable: number;
  /** Under the previous key and not this command's to move; they end on their own (a running sandbox's route). */
  draining: number;
}

export const emptyReport = (step: string): StepReport => ({
  step,
  total: 0,
  current: 0,
  previous: 0,
  moved: 0,
  unreadable: 0,
  draining: 0,
});

export interface StepContext {
  /** false: count only, write nothing. */
  readonly apply: boolean;
  /** Throws `RotationLockLost` once another holder took the lock; steps call it between items. */
  checkpoint(): void;
  progress(report: StepReport): void;
}

export interface RotationStep {
  readonly name: string;
  run(context: StepContext): Promise<StepReport>;
}

/** Which keys the process holds; only roles, never the keys. */
export interface RingState {
  other: 'previous' | 'next' | null;
  /**
   * SEC-D-05 (ADR-KEY-02 D5): the platform cipher still reads values its old SHA-256 key sealed, so `rotate` with no
   * previous key moves them to the HKDF key (the same steps, the same compare-and-sets).
   */
  cipherMove?: boolean;
}

export interface RotationResult {
  mode: RotationMode;
  /** The mode's own success: nothing left under the previous key (verify), nothing that needs it (retire). */
  ok: boolean;
  refused: string | null;
  steps: StepReport[];
}

export class RotationLockLost extends Error {
  constructor() {
    super('another run of the rotation took the lock: this one stopped');
    this.name = 'RotationLockLost';
  }
}

export const ROTATION_LEASE = 'secret-key-rotation';
const LEASE_TTL_MS = 60_000;
const RENEW_EVERY_MS = 20_000;

export interface SecretKeyRotationOptions {
  steps: readonly RotationStep[];
  ring: RingState;
  lease: LeaderLease;
  holder: string;
  /** `--accept-unreadable N`: the operator's acknowledgement of exactly N items under neither key. */
  acceptUnreadable?: number;
  leaseTtlMs?: number;
  renewEveryMs?: number;
  onProgress?: (report: StepReport) => void;
}

const sum = (
  steps: readonly StepReport[],
  field: keyof Omit<StepReport, 'step'>,
): number => steps.reduce((total, step) => total + step[field], 0);

export class SecretKeyRotation {
  private readonly ttlMs: number;
  private readonly renewMs: number;

  constructor(private readonly options: SecretKeyRotationOptions) {
    this.ttlMs = options.leaseTtlMs ?? LEASE_TTL_MS;
    this.renewMs = options.renewEveryMs ?? RENEW_EVERY_MS;
  }

  async run(mode: RotationMode): Promise<RotationResult> {
    const refused = this.precondition(mode);
    if (refused !== null) return { mode, ok: false, refused, steps: [] };
    if (mode === 'dry-run' || mode === 'verify') {
      const steps = await this.survey();
      return this.judge(mode, steps);
    }
    return this.locked(mode);
  }

  private precondition(mode: RotationMode): string | null {
    if (mode !== 'rotate' && mode !== 'retire') return null;
    if (this.options.ring.other === 'next') {
      return 'SECRET_KEY_NEXT is set: finish the switch first (SECRET_KEY the new key, SECRET_KEY_PREVIOUS the old one) and restart every service';
    }
    if (
      mode === 'rotate' &&
      this.options.ring.other === null &&
      this.options.ring.cipherMove !== true
    ) {
      return 'SECRET_KEY_PREVIOUS is not set: set it to the key being retired, next to the new SECRET_KEY, on every service';
    }
    return null;
  }

  private async locked(mode: 'rotate' | 'retire'): Promise<RotationResult> {
    const { lease, holder } = this.options;
    if (!(await lease.tryAcquire(ROTATION_LEASE, holder, this.ttlMs))) {
      return {
        mode,
        ok: false,
        refused:
          'another run of the rotation holds the lock; wait for it, or for its lock to lapse (one minute)',
        steps: [],
      };
    }
    let lost = false;
    // Renewals run outside any org's context: the lease is the deployment's.
    const renewing = detachedInterval(() => {
      lease.tryAcquire(ROTATION_LEASE, holder, this.ttlMs).then(
        (held) => {
          if (!held) lost = true;
        },
        () => {
          // A renewal that failed to reach the store is not a takeover; the next one decides.
        },
      );
    }, this.renewMs);
    const checkpoint = (): void => {
      if (lost) throw new RotationLockLost();
    };
    try {
      if (mode === 'retire')
        return this.judge(mode, await this.survey(checkpoint));
      const before = await this.survey(checkpoint);
      const unreadable = this.unacknowledged(before);
      if (unreadable !== null)
        return { mode, ok: false, refused: unreadable, steps: before };
      const moved: StepReport[] = [];
      for (const step of this.options.steps) {
        moved.push(await step.run(this.context(true, checkpoint)));
      }
      const after = await this.survey(checkpoint);
      const result = this.judge('verify', after);
      return {
        mode,
        ok: result.ok,
        refused: null,
        steps: after.map((report, index) => ({
          ...report,
          moved: moved[index]?.moved ?? 0,
        })),
      };
    } finally {
      clearInterval(renewing);
      await lease.release(ROTATION_LEASE, holder).catch(() => undefined);
    }
  }

  private async survey(
    checkpoint: () => void = () => undefined,
  ): Promise<StepReport[]> {
    const reports: StepReport[] = [];
    for (const step of this.options.steps) {
      reports.push(await step.run(this.context(false, checkpoint)));
    }
    return reports;
  }

  private context(apply: boolean, checkpoint: () => void): StepContext {
    return {
      apply,
      checkpoint,
      progress: (report) => this.options.onProgress?.({ ...report }),
    };
  }

  /** null when every unreadable item is acknowledged by count; else why the run refuses. */
  private unacknowledged(steps: readonly StepReport[]): string | null {
    const unreadable = sum(steps, 'unreadable');
    if (unreadable === 0 || unreadable === this.options.acceptUnreadable)
      return null;
    return (
      `${String(unreadable)} item(s) open under neither SECRET_KEY nor SECRET_KEY_PREVIOUS: check that SECRET_KEY_PREVIOUS ` +
      'is the key they were written under. If they are known to be unreadable (written by another deployment), ' +
      `run again with --accept-unreadable ${String(unreadable)}`
    );
  }

  private judge(mode: RotationMode, steps: StepReport[]): RotationResult {
    if (mode === 'dry-run') return { mode, ok: true, refused: null, steps };
    const unreadable = this.unacknowledged(steps);
    const previous = sum(steps, 'previous');
    const draining = sum(steps, 'draining');
    if (mode === 'verify') {
      return {
        mode,
        ok: previous === 0 && unreadable === null,
        refused: null,
        steps,
      };
    }
    // retire: nothing may still need the previous key, and an unreadable item may be one a wrong key hides.
    const needs: string[] = [];
    if (previous > 0)
      needs.push(
        `${String(previous)} item(s) are still under SECRET_KEY_PREVIOUS or the old SHA-256 cipher key: run rotate`,
      );
    if (draining > 0) {
      needs.push(
        `${String(draining)} running sandbox route(s) still hold a credential sealed under SECRET_KEY_PREVIOUS; they end with their runs`,
      );
    }
    if (unreadable !== null) needs.push(unreadable);
    return {
      mode,
      ok: needs.length === 0,
      refused:
        needs.length === 0
          ? null
          : `SECRET_KEY_PREVIOUS cannot be retired yet: ${needs.join('; ')}`,
      steps,
    };
  }
}
