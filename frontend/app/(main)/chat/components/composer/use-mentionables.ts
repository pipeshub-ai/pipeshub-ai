import { useEffect, useMemo, useState } from 'react';
import { useChatStore } from '@/chat/store';
import { isCollaboratorsView, type CollaboratorsResponse } from '@/chat/collaboration-types';
import aliases from '@/chat/mentions/reserved-aliases.json';
import { useParticipantSources } from '@/chat/mentions/use-participant-sources';
import { labelOfMention, useParticipantsStore } from '@/chat/mentions/participants-store';
import { MentionsApi, type MentionScope, type MentionSearchResult } from '@/chat/mentions/api';
import type { MentionRef } from './composer-input.types';

export const MENTION_QUERY_DEBOUNCE_MS = 150;
export const MENTION_RESULT_LIMIT = 20;

/** Stands in for a ref on the "add people" row; never sent. */
export const ADD_PEOPLE_ITEM: Mentionable = {
  ref: { type: 'user', id: '__add-people__' },
  label: '',
  group: 'action',
  action: 'addPeople',
};

export type MentionGroup = 'assistant' | 'agent' | 'people' | 'others' | 'teams' | 'action';

export interface Mentionable {
  ref: MentionRef;
  label: string;
  group: MentionGroup;
  /** An agent's `@handle`, without the `@`. */
  handle?: string;
  /** A colleague's email, shown under the name so two people with one name can be told apart. */
  email?: string;
  /** False for an organization member who is not in this chat yet (the sender may add them after sending). */
  inChat?: boolean;
  /** Not a mention: the closing row that opens the share drawer to add people to the chat. */
  action?: 'addPeople';
  /** Shown but not pickable: a message may carry one agent, and another is already in the composer. */
  disabled?: boolean;
}

/** Typed names that reach the chat's own responder (the shared reserved-alias table). */
const ASSISTANT_KEYWORDS: readonly string[] = aliases.assistant;

export interface RemotePerson {
  id: string;
  label: string;
  email?: string;
  inChat: boolean;
}

export interface MentionSources {
  agent?: { id: string; name: string } | null;
  /** Agents the caller can run that the server offers in this chat (a guest agent answers the turn). */
  ownAgents?: ReadonlyArray<{ id: string; label: string; handle?: string }>;
  collaborators?: CollaboratorsResponse | null;
  /** People seen as authors in the feed: the only other names a non-manager's summary view leaves. */
  authors?: Array<{ userId: string; displayName: string | null }>;
  /** Organization members the server matched on the query, in or out of this chat. */
  remotePeople?: ReadonlyArray<RemotePerson>;
  meUserId?: string | null;
  assistantLabel: string;
  /** Agents already mentioned in the composer; once one is there, every other agent is offered disabled. */
  selectedAgentIds?: readonly string[];
}

/**
 * A multi-word query is a name typed in order: each word starts a word of the label, left to right
 * ("jo sm" finds "John Michael Smith"). A trailing space needs one more label word after the last match.
 */
export function phraseMatches(label: string, query: string): boolean {
  const tokens = query.trimStart().toLowerCase().split(/\s+/);
  const words = label.toLowerCase().split(/\s+/).filter(Boolean);
  const wantsMore = tokens[tokens.length - 1] === '';
  if (wantsMore) tokens.pop();
  let at = 0;
  for (const token of tokens) {
    while (at < words.length && !words[at].startsWith(token)) at++;
    if (at >= words.length) return false;
    at++;
  }
  return !wantsMore || at < words.length;
}

function matches(label: string, keywords: readonly string[], q: string): boolean {
  const needle = q.trim().toLowerCase();
  if (!needle) return true;
  if (/\s/.test(q.trimStart())) return phraseMatches(label, q);
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
  const q = query.trimStart();
  const out: Mentionable[] = [];

  if (matches(sources.assistantLabel, ASSISTANT_KEYWORDS, q)) {
    out.push({ ref: { type: 'assistant', id: 'self' }, label: sources.assistantLabel, group: 'assistant' });
  }
  const agents = new Map<string, { label: string; handle?: string }>();
  const offerAgent = (id: string, label: string, handle?: string) => {
    const known = agents.get(id);
    if (known) {
      if (!known.handle && handle) known.handle = handle;
      return;
    }
    agents.set(id, { label, ...(handle ? { handle } : {}) });
  };
  const handleOfOwn = sources.ownAgents?.find((a) => a.id === sources.agent?.id)?.handle;
  if (sources.agent) offerAgent(sources.agent.id, sources.agent.name, handleOfOwn);
  for (const a of sources.ownAgents ?? []) offerAgent(a.id, a.label, a.handle);

  const handleQuery = q.trim().replace(/^@/, '').toLowerCase();
  const taken = sources.selectedAgentIds ?? [];
  for (const [id, a] of agents) {
    if (!matches(a.label, [], q) && !(a.handle && handleQuery && a.handle.toLowerCase().startsWith(handleQuery))) continue;
    out.push({
      ref: { type: 'agent', id },
      label: a.label,
      group: 'agent',
      ...(a.handle ? { handle: a.handle } : {}),
      ...(taken.length > 0 && !taken.includes(id) ? { disabled: true } : {}),
    });
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
  const remoteById = new Map((sources.remotePeople ?? []).map((p) => [p.id, p]));
  for (const p of remoteById.values()) if (p.inChat) addPerson(p.id, p.label);

  for (const [id, name] of people) {
    if (!remoteById.has(id) && !matches(name, [], q)) continue;
    const email = remoteById.get(id)?.email;
    out.push({ ref: { type: 'user', id }, label: name, group: 'people', ...(email ? { email } : {}) });
  }
  for (const p of remoteById.values()) {
    if (p.inChat || p.id === sources.meUserId || people.has(p.id) || !p.label.trim()) continue;
    out.push({
      ref: { type: 'user', id: p.id },
      label: p.label.trim(),
      group: 'others',
      inChat: false,
      ...(p.email ? { email: p.email } : {}),
    });
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
  /** Agents already in the composer (see `MentionSources.selectedAgentIds`). */
  selectedAgentIds?: readonly string[];
}

export interface UseMentionablesResult {
  items: Mentionable[];
  loading: boolean;
  /** The list reflects the current query: the debounce elapsed and the server answered. */
  settled: boolean;
}

/**
 * Mentionable targets of the active chat. People and teams come from the collaborators list the chat's
 * participants store already holds (fetched once per chat, refreshed when stale): an owner or an inviting editor
 * gets the full list, anyone else a summary (owner and a count), so for them the list is the owner plus the people
 * who authored turns in this chat. The query is debounced.
 */
export function useMentionables({ query, enabled, assistantLabel, selectedAgentIds }: UseMentionablesOptions): UseMentionablesResult {
  const { epoch, agentId, searchRef: ref, response, ownAgents, authors, meUserId, loading } = useParticipantSources(enabled);
  const agentName = useChatStore((s) => s.agentContextDisplayName);
  const debounced = useDebounced(query, MENTION_QUERY_DEBOUNCE_MS);

  const needle = debounced.trim();
  const [remote, setRemote] = useState<{ ref: MentionScope; q: string; epoch: number; result: MentionSearchResult } | null>(null);
  const [searching, setSearching] = useState(false);
  useEffect(() => {
    if (!enabled || !ref || !needle) {
      setRemote(null);
      setSearching(false);
      return;
    }
    let live = true;
    setSearching(true);
    MentionsApi.search(ref, needle)
      .then((result) => live && setRemote({ ref, q: needle, epoch, result }))
      .catch(() => live && setRemote(null))
      .finally(() => live && setSearching(false));
    return () => {
      live = false;
    };
  }, [enabled, ref, needle, epoch]);
  const remoteNow = remote && remote.ref === ref && remote.q === needle && remote.epoch === epoch ? remote.result : null;

  // The full collaborators list goes only to the owner and to an editor who may invite: the same people who may add someone.
  const canInvite = response !== undefined && isCollaboratorsView(response);

  const items = useMemo(() => {
    if (!enabled) return [];
    const found = buildMentionables(
      {
        agent: agentId ? { id: agentId, name: agentName?.trim() || 'Agent' } : null,
        collaborators: response ?? null,
        ownAgents: remoteNow ? [...ownAgents, ...remoteNow.agents] : ownAgents,
        remotePeople: remoteNow?.people,
        authors,
        meUserId,
        assistantLabel,
        selectedAgentIds,
      },
      debounced,
    );
    return canInvite ? [...found, ADD_PEOPLE_ITEM] : found;
  }, [enabled, canInvite, agentId, agentName, response, ownAgents, remoteNow, authors, meUserId, assistantLabel, selectedAgentIds, debounced]);

  return { items, loading: loading || searching, settled: debounced === query && !searching && !loading };
}

/** Names seen in any popover this session, so a prefill that carries only ids (edit, regenerate) and the sent message can show chips with names. */
export function rememberMentionLabels(items: Array<Pick<Mentionable, 'ref' | 'label'>>): void {
  useParticipantsStore.getState().remember(items);
}

export function cachedMentionLabel(ref: MentionRef): string | undefined {
  return labelOfMention(useParticipantsStore.getState().labels, ref);
}
