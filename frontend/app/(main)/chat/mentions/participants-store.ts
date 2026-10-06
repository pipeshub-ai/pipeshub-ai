import { create } from 'zustand';
import { CollaborationApi } from '@/chat/collaboration-api';
import {
  isCollaboratorsView,
  type CollaboratorsResponse,
  type ConversationRef,
} from '@/chat/collaboration-types';
import type { MentionRef } from '../components/composer/composer-input.types';
import type { RespondMode } from './classify';
import { MentionsApi, type MentionableAgent } from './api';

const keyOf = (ref: Pick<MentionRef, 'type' | 'id'>): string => `${ref.type}:${ref.id}`;

interface ParticipantsState {
  byConv: Record<string, { response: CollaboratorsResponse; at: number }>;
  /** The caller's own agents, by conversation; filled on the same fetch as the collaborators. */
  agentsByConv: Record<string, MentionableAgent[]>;
  /** Conversations whose list is being fetched. */
  loading: Record<string, boolean>;
  /** Names seen for ids, so a chip in the history can show a name. A miss renders "Unknown ...". */
  labels: Record<string, string>;
  load(ref: ConversationRef): Promise<void>;
  remember(items: ReadonlyArray<{ ref: Pick<MentionRef, 'type' | 'id'>; label: string }>): void;
  reset(): void;
}

const inFlight = new Map<string, Promise<void>>();

export const useParticipantsStore = create<ParticipantsState>((set, get) => ({
  byConv: {},
  agentsByConv: {},
  loading: {},
  labels: {},
  load(ref) {
    const pending = inFlight.get(ref.id);
    if (pending) return pending;
    set((s) => ({ loading: { ...s.loading, [ref.id]: true } }));
    const run = CollaborationApi.getCollaborators(ref)
      .then((response) => {
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
        inFlight.delete(ref.id);
        set((s) => {
          const { [ref.id]: _done, ...rest } = s.loading;
          return { loading: rest };
        });
      });
    if (ref.kind === 'chat') {
      void MentionsApi.listAgents(ref)
        .then((agents) => {
          set((s) => ({ agentsByConv: { ...s.agentsByConv, [ref.id]: agents } }));
          get().remember(agents.map((a) => ({ ref: { type: 'agent' as const, id: a.id }, label: a.label })));
        })
        .catch(() => undefined);
    }
    inFlight.set(ref.id, run);
    return run;
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
    set({ byConv: {}, agentsByConv: {}, loading: {}, labels: {} });
  },
}));

export const labelOfMention = (labels: Record<string, string>, ref: Pick<MentionRef, 'type' | 'id'>): string | undefined =>
  labels[keyOf(ref)];

export function respondModeOf(response: CollaboratorsResponse | undefined): RespondMode {
  if (!response) return 'smart';
  const mode = isCollaboratorsView(response) ? response.settings.respondMode : response.respondMode;
  return mode ?? 'smart';
}
