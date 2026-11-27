import type { AcpOptIn } from '../domain/engine-profiles';

/**
 * Each org's engine profile ceiling (E4a), read afresh at every admission of
 * an engine run, so a change reaches the next run and never a pinned one.
 */
export interface EngineProfileCeilings {
  /**
   * The org's ceiling as stored, unparsed (admission refuses one that is not a
   * narrowing); null when the org set none. Throws when it cannot be read.
   */
  read(orgId: string): Promise<unknown>;
  /**
   * Who opts an ACP engine in: `org` when an org's ceiling must name it; `release` (also when absent) when no org
   * can set one, so every released ACP engine is a candidate (the deployment's engine settings and install checks
   * still apply).
   */
  readonly acpOptIn?: AcpOptIn;
}

/**
 * No org ceiling: every engine run gets this release's defaults, narrowed only by its agent and request, and every
 * released ACP engine is opted in.
 */
export const noEngineCeilings: EngineProfileCeilings = Object.freeze({
  read: () => Promise.resolve(null),
  acpOptIn: 'release',
});
