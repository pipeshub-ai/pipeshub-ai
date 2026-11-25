'use client';

import type { EnvironmentDraft } from '../../agent-environment';

export interface AgentEnvironmentExtrasProps {
  draft: EnvironmentDraft;
  /** Applies a change to the draft and saves it on the agent when it reads as an environment. */
  commit: (change: Partial<EnvironmentDraft>) => void;
  /** Why the draft's folders cannot be saved yet, worded; null when they can. */
  folderProblem: string | null;
  readOnly?: boolean;
}

/** More of the agent's environment in the builder's "Environment" section. Editions plug in via config.ts. */
export function AgentEnvironmentExtras(_props: AgentEnvironmentExtrasProps) {
  return null;
}
