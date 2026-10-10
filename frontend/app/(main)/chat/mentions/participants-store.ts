import { create } from 'zustand';
import { CollaborationApi } from '@/chat/collaboration-api';
import {
  isCollaboratorsView,
  type CollaboratorsResponse,
  type ConversationRef,
} from '@/chat/collaboration-types';
import type { MentionRef } from '../components/composer/composer-input.types';
import type { RespondMode } from './classify';
import { onAclVersion, onCollaboratorsChanged } from '../collaboration-events';
import { MentionsApi, type MentionableAgent, type MentionScope } from './api';

const keyOf = (ref: Pick<MentionRef, 'type' | 'id'>): string => `${ref.type}:${ref.id}`;

interface ParticipantsState {
  byConv: Record<string, { response: CollaboratorsResponse; at: number }>;
  /** Agents the caller can run, by conversation; filled on the same fetch as the collaborators. */
  agentsByConv: Record<string, MentionableAgent[]>;
  /** Conversations whose list is being fetched. */
  loading: Record<string, boolean>;
  /** Bumped by `invalidate`, so a cache keyed by chat (the picker's per-query results) can drop what it held. */
  epoch: Record<string, number>;
  /** Last access-list version the feed reported, by chat. */
  aclSeen: Record<string, string | number>;
  /** Who is in the chat may have changed: forget the list and the agents; the next reader refetches. */
  invalidate(convId: string): void;
  /** The feed's access-list version; a change after the first sight invalidates. Absent versions are ignored. */
  noteAclVersion(convId: string, version: string | number | undefined): void;
  /** Names seen for ids, so a chip in the history can show a name. A miss renders "Unknown ...". */
  labels: Record<string, string>;
  load(ref: ConversationRef): Promise<void>;
  /** Agents whose handle or name starts with `q`, merged into the cached list; the server caps a page, so a typed handle may not be in it. */
  loadAgentsMatching(ref: MentionScope, q: string): Promise<MentionableAgent[]>;
  /** The agents the caller can run, for a scope the collaborators list does not cover (a chat with no id yet). */
  loadAgents(scope: MentionScope): Promise<void>;
  mergeAgents(convId: string, agents: MentionableAgent[]): void;
  remember(items: ReadonlyArray<{ ref: Pick<MentionRef, 'type' | 'id'>; label: string }>): void;
  reset(): void;
}

const agentsKeyOf = (scope: MentionScope): string => (scope.kind === 'new' ? 'new' : scope.id);

const inFlight = new Map<string, Promise<void>>();

export const useParticipantsStore = create<ParticipantsState>((set, get) => ({
  byConv: {},
  agentsByConv: {},
  loading: {},
  labels: {},
  epoch: {},
  aclSeen: {},
  invalidate(convId) {
    inFlight.delete(convId);
    set((s) => {
      const { [convId]: _gone, ...byConv } = s.byConv;
      const { [convId]: _agents, ...agentsByConv } = s.agentsByConv;
      return { byConv, agentsByConv, epoch: { ...s.epoch, [convId]: (s.epoch[convId] ?? 0) + 1 } };
    });
  },
  noteAclVersion(convId, version) {
    if (version === undefined || version === null) return;
    const prev = get().aclSeen[convId];
    if (prev === version) return;
    set((s) => ({ aclSeen: { ...s.aclSeen, [convId]: version } }));
    if (prev !== undefined) get().invalidate(convId);
  },
  load(ref) {
    const pending = inFlight.get(ref.id);
    if (pending) return pending;
    set((s) => ({ loading: { ...s.loading, [ref.id]: true } }));
    const epoch0 = get().epoch[ref.id] ?? 0;
    const stale = () => (get().epoch[ref.id] ?? 0) !== epoch0;
    const run: Promise<void> = CollaborationApi.getCollaborators(ref)
      .then((response) => {
        if (stale()) return;
        const items = [
          { ref: { type: 'user' as const, id: response.owner.userId }, label: response.owner.displayName },
          ...(isCollaboratorsView(response)
            ? response.collaborators
                .filter((c) => c.state === 'active')
                .map((c) => ({
                  ref: { type: c.principalType === 'team' ? ('team' as const) : ('user' as const), id: c.principalId },
                  label: c.displayName,
                }))
            : []),
        ];
        set((s) => ({ byConv: { ...s.byConv, [ref.id]: { response, at: Date.now() } } }));
        get().remember(items);
      })
      .catch(() => {
        // Aliases still work without the list; a failed lookup only means no typed-name matches.
      })
      .finally(() => {
        if (inFlight.get(ref.id) === run) inFlight.delete(ref.id);
        set((s) => {
          const { [ref.id]: _done, ...rest } = s.loading;
          return { loading: rest };
        });
      });
    void MentionsApi.listAgents(ref)
      .then((agents) => {
        if (stale()) return;
        set((s) => ({ agentsByConv: { ...s.agentsByConv, [ref.id]: agents } }));
        get().remember(agents.map((a) => ({ ref: { type: 'agent' as const, id: a.id }, label: a.label })));
      })
      .catch(() => undefined);
    inFlight.set(ref.id, run);
    return run;
  },
  async loadAgentsMatching(ref, q) {
    try {
      const agents = await MentionsApi.listAgents(ref, q);
      get().mergeAgents(agentsKeyOf(ref), agents);
      return agents;
    } catch {
      return [];
    }
  },
  async loadAgents(scope) {
    try {
      const agents = await MentionsApi.listAgents(scope);
      set((s) => ({ agentsByConv: { ...s.agentsByConv, [agentsKeyOf(scope)]: agents } }));
      get().remember(agents.map((a) => ({ ref: { type: 'agent' as const, id: a.id }, label: a.label })));
    } catch {
      // The picker still offers the assistant and people without agents.
    }
  },
  mergeAgents(convId, agents) {
    if (agents.length === 0) return;
    set((s) => {
      const merged = new Map((s.agentsByConv[convId] ?? []).map((a) => [a.id, a]));
      for (const a of agents) merged.set(a.id, a);
      return { agentsByConv: { ...s.agentsByConv, [convId]: [...merged.values()] } };
    });
    get().remember(agents.map((a) => ({ ref: { type: 'agent' as const, id: a.id }, label: a.label })));
  },
  remember(items) {
    const named = items.filter((i) => i.label.trim());
    if (named.length === 0) return;
    set((s) => {
      const labels = { ...s.labels };
      for (const i of named) labels[keyOf(i.ref)] = i.label.trim();
      return { labels };
    });
  },
  reset() {
    set({ byConv: {}, agentsByConv: {}, loading: {}, labels: {}, epoch: {}, aclSeen: {} });
  },
}));

export const labelOfMention = (labels: Record<string, string>, ref: Pick<MentionRef, 'type' | 'id'>): string | undefined =>
  labels[keyOf(ref)];

export function respondModeOf(response: CollaboratorsResponse | undefined): RespondMode {
  if (!response) return 'smart';
  const mode = isCollaboratorsView(response) ? response.settings.respondMode : response.respondMode;
  return mode ?? 'smart';
}

onCollaboratorsChanged((ref) => useParticipantsStore.getState().invalidate(ref.id));
onAclVersion((ref, version) => useParticipantsStore.getState().noteAclVersion(ref.id, version));
