/**
 * Exception-safe process.env snapshot/restore for unit tests.
 *
 * Usage:
 *   import { envGuard } from '../helpers/env-guard'
 *
 *   describe('my suite', () => {
 *     const env = envGuard()
 *     beforeEach(() => env.snapshot())
 *     afterEach(() => env.restore())
 *     // tests can freely modify process.env — always cleaned up
 *   })
 */
export function envGuard() {
  let snap: Record<string, string | undefined> = {};

  return {
    /** Capture current process.env state. Call in beforeEach. */
    snapshot() {
      snap = { ...process.env } as Record<string, string | undefined>;
    },

    /** Restore process.env to the last snapshot. Call in afterEach. */
    restore() {
      // Remove keys that were added after the snapshot
      for (const key of Object.keys(process.env)) {
        if (!(key in snap)) {
          delete process.env[key];
        }
      }
      // Restore original values (including keys that were deleted/changed)
      for (const [key, val] of Object.entries(snap)) {
        if (val === undefined) {
          delete process.env[key];
        } else {
          process.env[key] = val;
        }
      }
    },
  };
}
