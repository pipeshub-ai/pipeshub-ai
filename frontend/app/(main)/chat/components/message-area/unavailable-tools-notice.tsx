'use client';

import React from 'react';
import type { UnavailableTool } from '../../unavailable-tools';
import { ConnectToolsCard } from './connect-tools-card';

interface UnavailableToolsNoticeProps {
  /** The answer's tools; only those marked `needed` (its run called their stub) are offered. */
  items: UnavailableTool[];
  question: string;
  agentId?: string;
  /**
   * Set when the viewer did not ask this question. The tools tell of the asker's own connections, so the viewer is
   * told nothing of them.
   */
  readOnlyFor?: { agentName: string | null };
}

/** Under an answer that needed tools that aren't connected: the Connect card, for the person who asked. */
export function UnavailableToolsNotice({ items, question, agentId, readOnlyFor }: UnavailableToolsNoticeProps) {
  const needed = items.filter((item) => item.needed);
  if (readOnlyFor || needed.length === 0) return null;
  return (
    <ConnectToolsCard items={needed} agentId={agentId} serviceAccount={false} question={question} blocked={false} />
  );
}
