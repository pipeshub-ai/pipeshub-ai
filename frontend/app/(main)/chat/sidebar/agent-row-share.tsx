'use client';

import type { ReactNode } from 'react';

export interface AgentRowShareInput {
  agentKey: string | undefined;
  /** The agent's display name, shown in the share panel. */
  name: string;
  /** The agent's own answer: the caller may share it. */
  canShare: boolean;
}

export interface AgentRowShare {
  /** The ⋯ menu's Share item. */
  menuItem: ReactNode;
  /** The share panel. Render it outside any clickable row, as the delete dialog is. */
  panel: ReactNode;
}

/** Sharing an agent from its row's ⋯ menu. Editions plug in via config.ts; this one has no agent sharing. */
export function useAgentRowShare(_input: AgentRowShareInput): AgentRowShare | null {
  return null;
}
