'use client';

import React from 'react';
import { MentionChip } from '../composer/mention-chip';
import { unescapeLiteralTokens } from '../composer/mention-serializer';
import type { MentionRef } from '../composer/composer-input.types';
import { labelOfMention, useParticipantsStore } from '../../mentions/participants-store';
import { selectChatMentionsEnabled, useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { useChatStore } from '../../store';
import { useUserStore } from '@/lib/store/user-store';
import { findAssistantAliases } from '../composer/typed-mention-resolver';

const TOKEN = /<@(assistant|agent|user|team):([A-Za-z0-9_.-]{1,128})>/g;

/** True when the text carries at least one unescaped `<@type:id>` token. */
export const hasMentionTokens = (text: string): boolean => new RegExp(TOKEN.source).test(text);

/** Cuts at `limit` characters without leaving half a token behind. */
export function truncateKeepingTokens(text: string, limit: number): string {
  if (text.length <= limit) return text;
  const re = new RegExp(TOKEN.source, 'g');
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index < limit && m.index + m[0].length > limit) return text.slice(0, m.index + m[0].length);
  }
  return text.slice(0, limit);
}

/** The text with tokens replaced by `@Name` (the unknown-name wording is the caller's), for copying. */
export function mentionsToPlainText(text: string, labels: Record<string, string>, unknown = 'Unknown'): string {
  return unescapeLiteralTokens(
    text.replace(TOKEN, (_m, type: MentionRef['type'], id: string) => `@${labelOfMention(labels, { type, id }) ?? unknown}`),
  );
}

/** Plain text with typed reserved assistant aliases drawn as assistant chips. */
function textWithAliasChips(text: string, keyBase: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  let last = 0;
  for (const { start, end } of findAssistantAliases(text)) {
    if (start > last) out.push(unescapeLiteralTokens(text.slice(last, start)));
    out.push(<MentionChip key={`${keyBase}:alias:${start}`} type="assistant" label={null} />);
    last = end;
  }
  if (last < text.length) out.push(unescapeLiteralTokens(text.slice(last)));
  return out;
}

/** Message text with `<@type:id>` tokens drawn as chips; the label comes from the chat's people, else the chip says "Unknown ...". */
export function MentionText({ text }: { text: string }) {
  const labels = useParticipantsStore((s) => s.labels);
  const enabled = useFeatureFlagsStore(selectChatMentionsEnabled);
  // The chat's own agent is the only agent a token can name (v1); its name comes from the agent context.
  const agentId = useChatStore((s) => (s.activeSlotId ? s.slots[s.activeSlotId]?.threadAgentId?.trim() || null : null));
  const agentName = useChatStore((s) => s.agentContextDisplayName?.trim() || null);
  const meUserId = useUserStore((s) => s.profile?.userId ?? null);
  const labelOf = (ref: MentionRef): string | null | undefined =>
    labelOfMention(labels, ref) ?? (ref.type === 'agent' && ref.id === agentId ? agentName : undefined);
  if (!enabled) return <>{text}</>;
  if (!hasMentionTokens(text)) return <>{textWithAliasChips(text, 'p')}</>;
  const parts: React.ReactNode[] = [];
  const re = new RegExp(TOKEN.source, 'g');
  let last = 0;
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index > last) parts.push(...textWithAliasChips(text.slice(last, m.index), String(last)));
    const ref = { type: m[1] as MentionRef['type'], id: m[2] };
    parts.push(<MentionChip key={`${m.index}:${m[0]}`} type={ref.type} label={ref.type === 'assistant' ? null : labelOf(ref)} self={ref.type === 'user' && !!meUserId && ref.id === meUserId} />);
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push(...textWithAliasChips(text.slice(last), String(last)));
  return <>{parts}</>;
}
