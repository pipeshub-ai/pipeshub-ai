/**
 * fast-check's side of the Hypothesis profiles (QA-13): PRs replay the same
 * examples (seed 0), the nightly lane (`HARNESS_PROPERTY_PROFILE=nightly`)
 * searches 5,000 random ones; a failure prints its seed and path to replay.
 */
import type fc from 'fast-check';

export const NIGHTLY_RUNS = 5_000;

export const nightly = (): boolean =>
  process.env.HARNESS_PROPERTY_PROFILE === 'nightly';

/**
 * `ciRuns` on a PR, seeded, so the examples (and the coverage gate's figure)
 * are the same every run; `nightlyRuns` (5,000 unless a real store makes that
 * too slow) random ones nightly. `FC_SEED` (and `FC_PATH`) replay the run a
 * failure printed, at either profile.
 */
export function propertyRuns(
  ciRuns: number,
  nightlyRuns = NIGHTLY_RUNS,
): Pick<fc.Parameters<unknown>, 'numRuns' | 'seed' | 'path'> {
  const numRuns = nightly() ? nightlyRuns : ciRuns;
  const replay = process.env.FC_SEED;
  if (replay !== undefined && replay !== '') {
    const path = process.env.FC_PATH;
    return { numRuns, seed: Number(replay), ...(path ? { path } : {}) };
  }
  if (!nightly()) return { numRuns, seed: 0 };
  // Drawn here rather than by fast-check so a nightly run that passes can still be replayed.
  const seed = Math.floor(Math.random() * 2 ** 31);
  // eslint-disable-next-line no-console
  console.log(`fast-check seed ${seed} (replay with FC_SEED=${seed})`);
  return { numRuns, seed };
}
