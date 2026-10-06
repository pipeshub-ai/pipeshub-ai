'use client';

import React from 'react';
import { isRedactedAgentDraft } from '../../types';
import { AgentDraftCard } from './agent-draft-card';
import { AgentDraftRedacted } from './agent-draft-redacted';
import type { AgentDraftPayload } from '../../types';

export interface AgentDraftPlaceholderProps {
  draft: AgentDraftPayload;
  /** Who drafted it, for the notice anyone but the requester sees. */
  authorName?: string;
  conversationId?: string | null;
  /** The stored draft row's id; unknown while the draft is still streaming. */
  messageId?: string | null;
  /** False when the agent builder flag is off: the card stays visible but cannot create. */
  builderEnabled?: boolean;
}

/** The seat for the draft card: the requester gets the card, everyone else a one-line notice. */
export function AgentDraftPlaceholder({
  draft,
  authorName,
  conversationId = null,
  messageId = null,
  builderEnabled = true,
}: AgentDraftPlaceholderProps) {
  if (isRedactedAgentDraft(draft)) return <AgentDraftRedacted authorName={authorName} />;
  return <AgentDraftCard draft={draft} conversationId={conversationId} messageId={messageId} builderEnabled={builderEnabled} />;
}
