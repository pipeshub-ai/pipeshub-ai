'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { NodeViewWrapper, type NodeViewProps } from '@tiptap/react';
import type { MentionRef } from './composer-input.types';

export interface MentionChipProps {
  type: MentionRef['type'];
  /** Display text only. A directory miss (deleted user, team that left the chat) leaves it empty. */
  label?: string | null;
  /** The chip names the viewer: drawn as "@you" and emphasised. */
  self?: boolean;
}

/** Text node only: a label is never interpreted as markup. */
export function MentionChip({ type, label, self }: MentionChipProps) {
  const { t } = useTranslation();
  const fallback: Record<MentionRef['type'], string> = {
    assistant: t('chat.mentions.assistant', { defaultValue: 'Assistant' }),
    agent: t('chat.mentions.unknownAgent', { defaultValue: 'Unknown agent' }),
    user: t('chat.mentions.unknownUser', { defaultValue: 'Unknown user' }),
    team: t('chat.mentions.unknownTeam', { defaultValue: 'Unknown team' }),
  };
  const text = label?.trim() || fallback[type];
  if (self) {
    return (
      <span
        className="ph-mention-chip ph-mention-chip--self"
        data-testid="mention-chip"
        data-mention-type={type}
        data-mention-self="true"
        aria-label={t('chat.mentions.mentionsYou', { defaultValue: 'mentions you' })}
      >
        @{t('chat.mentions.you', { defaultValue: 'you' })}
      </span>
    );
  }
  return (
    <span className="ph-mention-chip" data-testid="mention-chip" data-mention-type={type}>
      @{text}
    </span>
  );
}

export function MentionChipView({ node }: NodeViewProps) {
  return (
    <NodeViewWrapper as="span" className="ph-mention-chip-wrap" contentEditable={false}>
      <MentionChip type={node.attrs.mentionType as MentionRef['type']} label={node.attrs.label as string | null} />
    </NodeViewWrapper>
  );
}
