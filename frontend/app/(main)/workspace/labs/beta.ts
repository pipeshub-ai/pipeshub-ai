import type { AvailableFlag } from './api';

/** Labs marks a flag Beta when it says it is, and every switch of the agent harness (which is in beta). */
export const isBetaFlag = (flag: Pick<AvailableFlag, 'beta' | 'requiresAgentHarness'>): boolean =>
  flag.beta === true || flag.requiresAgentHarness === true;
