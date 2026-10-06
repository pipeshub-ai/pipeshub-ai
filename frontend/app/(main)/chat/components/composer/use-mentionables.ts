import { useEffect, useMemo, useState } from 'react';
import { useChatStore } from '@/chat/store';
import { isCollaboratorsView, type CollaboratorsResponse } from '@/chat/collaboration-types';
import aliases from '@/chat/mentions/reserved-aliases.json';
import { useParticipantSources } from '@/chat/mentions/use-participant-sources';
import { labelOfMention, useParticipantsStore } from '@/chat/mentions/participants-store';
import type { MentionRef } from './composer-input.types';

export const MENTION_QUERY_DEBOUNCE_MS = 150;
export const MENTION_RESULT_LIMIT = 20;

export type MentionGroup = 'assistant' | 'agent' | 'people' | 'teams';

export interface Mentionable {
  ref: MentionRef;
  label: string;
  group: MentionGroup;
  /** An own agent's `@handle`, without the `@`. */
  handle?: string;
}

/** Typed names that reach the chat's own responder (the shared reserved-alias table). */
const ASSISTANT_KEYWORDS: readonly string[] = aliases.assistant;

export interface MentionSources {
  agent?: { id: string; name: string } | null;
  /** The caller's own agents the server offers in a chat that has no agent of its own. */
  ownAgents?: ReadonlyArray<{ id: string; label: string; handle?: string }>;
  collaborators?: CollaboratorsResponse | null;
  /** People seen as authors in the feed: the only other names a non-manager's summary view leaves. */
  authors?: Array<{ userId: string; displayName: string | null }>;
  meUserId?: string | null;
  assistantLabel: string;
}

function matches(label: string, keywords: readonly string[], q: string): boolean {
  if (!q) return true;
  const needle = q.toLowerCase();
  if (keywords.some((k) => k.startsWith(needle))) return true;
  return label
    .toLowerCase()
    .split(/\s+/)
    .some((word) => word.startsWith(needle));
}

/** Pure: who can be mentioned in this chat for `query`, grouped in popover order and capped. */
export function buildMentionables(
  sources: MentionSources,
  query: string,
  limit: number = MENTION_RESULT_LIMIT,
): Mentionable[] {
  const q = query.trim();
  const out: Mentionable[] = [];

  if (matches(sources.assistantLabel, ASSISTANT_KEYWORDS, q)) {
    out.push({ ref: { type: 'assistant', id: 'self' }, label: sources.assistantLabel, group: 'assistant' });
  }
  if (sources.agent && matches(sources.agent.name, [], q)) {
    out.push({ ref: { type: 'agent', id: sources.agent.id }, label: sources.agent.name, group: 'agent' });
  }

  for (const a of sources.ownAgents ?? []) {
    if (matches(a.label, [], q) || (a.handle && q && a.handle.startsWith(q.replace(/^@/, '').toLowerCase()))) {
      out.push({ ref: { type: 'agent', id: a.id }, label: a.label, group: 'agent', ...(a.handle ? { handle: a.handle } : {}) });
    }
  }

  const people = new Map<string, string>();
  const addPerson = (userId: string, name: string | null | undefined) => {
    if (!userId || userId === sources.meUserId || !name?.trim() || people.has(userId)) return;
    people.set(userId, name.trim());
  };
  const teams = new Map<string, string>();

  const response = sources.collaborators;
  if (response) {
    addPerson(response.owner.userId, response.owner.displayName);
    if (isCollaboratorsView(response)) {
      for (const c of response.collaborators) {
        if (c.state !== 'active') continue;
        if (c.principalType === 'user') addPerson(c.principalId, c.displayName);
        else teams.set(c.principalId, c.displayName);
      }
    }
  }
  for (const a of sources.authors ?? []) addPerson(a.userId, a.displayName);

  for (const [id, name] of people) {
    if (matches(name, [], q)) out.push({ ref: { type: 'user', id }, label: name, group: 'people' });
  }
  for (const [id, name] of teams) {
    if (matches(name, [], q)) out.push({ ref: { type: 'team', id }, label: name, group: 'teams' });
  }
  return out.slice(0, limit);
}

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return debounced;
}

export interface UseMentionablesOptions {
  query: string;
  /** The popover is open: the participants are fetched (once per open) and the list is computed. */
  enabled: boolean;
  assistantLabel: string;
}

export interface UseMentionablesResult {
  items: Mentionable[];
  loading: boolean;
}

/**
 * Mentionable targets of the active chat. People and teams come from the collaborators list the chat's
 * participants store already holds (fetched once per chat, refreshed when stale): an owner or an inviting editor
 * gets the full list, anyone else a summary (owner and a count), so for them the list is the owner plus the people
 * who authored turns in this chat. The query is debounced.
 */
export function useMentionables({ query, enabled, assistantLabel }: UseMentionablesOptions): UseMentionablesResult {
  const { agentId, response, ownAgents, authors, meUserId, loading } = useParticipantSources(enabled);
  const agentName = useChatStore((s) => s.agentContextDisplayName);
  const debounced = useDebounced(query, MENTION_QUERY_DEBOUNCE_MS);

  const items = useMemo(
    () =>
      enabled
        ? buildMentionables(
            {
              agent: agentId ? { id: agentId, name: agentName?.trim() || 'Agent' } : null,
              collaborators: response ?? null,
              ownAgents,
              authors,
              meUserId,
              assistantLabel,
            },
            debounced,
          )
        : [],
    [enabled, agentId, agentName, response, ownAgents, authors, meUserId, assistantLabel, debounced],
  );

  return { items, loading };
}

/** Names seen in any popover this session, so a prefill that carries only ids (edit, regenerate) and the sent message can show chips with names. */
export function rememberMentionLabels(items: Array<Pick<Mentionable, 'ref' | 'label'>>): void {
  useParticipantsStore.getState().remember(items);
}

export function cachedMentionLabel(ref: MentionRef): string | undefined {
  return labelOfMention(useParticipantsStore.getState().labels, ref);
}
