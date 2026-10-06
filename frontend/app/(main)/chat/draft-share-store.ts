import { create } from 'zustand';
import type { CollaboratorAccessLevel } from './collaboration-types';

export interface DraftPrincipal {
  type: 'user' | 'team';
  id: string;
  name: string;
  email?: string;
  memberCount?: number;
  level: CollaboratorAccessLevel;
}

/** What the first send carries: the body of `PUT .../collaborators`, applied by the server as it creates the chat. */
export interface DraftShareBody {
  collaborators: Array<{ principalType: 'user' | 'team'; principalId: string; accessLevel: CollaboratorAccessLevel }>;
  note?: string;
  confirmOrgWide?: true;
}

interface DraftShareState {
  principals: DraftPrincipal[];
  message: string;
  confirmOrgWide: boolean;
  add(list: DraftPrincipal[], options?: { message?: string; confirmOrgWide?: boolean }): void;
  remove(type: DraftPrincipal['type'], id: string): void;
  setLevel(type: DraftPrincipal['type'], id: string, level: CollaboratorAccessLevel): void;
  clear(): void;
}

const same = (a: Pick<DraftPrincipal, 'type' | 'id'>, b: Pick<DraftPrincipal, 'type' | 'id'>) => a.type === b.type && a.id === b.id;

/** People and teams picked on a chat that does not exist yet. Nothing is sent until the first message. */
export const useDraftShareStore = create<DraftShareState>((set) => ({
  principals: [],
  message: '',
  confirmOrgWide: false,
  add(list, options) {
    set((s) => ({
      principals: [...s.principals.filter((p) => !list.some((n) => same(n, p))), ...list],
      message: options?.message?.trim() ? options.message.trim() : s.message,
      confirmOrgWide: s.confirmOrgWide || options?.confirmOrgWide === true,
    }));
  },
  remove(type, id) {
    set((s) => {
      const principals = s.principals.filter((p) => !same(p, { type, id }));
      return principals.length === 0 ? { principals, message: '', confirmOrgWide: false } : { principals };
    });
  },
  setLevel(type, id, level) {
    set((s) => ({ principals: s.principals.map((p) => (same(p, { type, id }) ? { ...p, level } : p)) }));
  },
  clear() {
    set({ principals: [], message: '', confirmOrgWide: false });
  },
}));

/** The `share` field of the first send, or undefined when nobody was picked. */
export function draftShareBody(): DraftShareBody | undefined {
  const { principals, message, confirmOrgWide } = useDraftShareStore.getState();
  if (principals.length === 0) return undefined;
  return {
    collaborators: principals.map((p) => ({ principalType: p.type, principalId: p.id, accessLevel: p.level })),
    ...(message ? { note: message } : {}),
    ...(confirmOrgWide ? { confirmOrgWide: true as const } : {}),
  };
}

/** Ids of the people picked so far: the new-chat mention list counts them as in the chat. */
export const draftUserIds = (principals: readonly DraftPrincipal[]): string[] =>
  principals.filter((p) => p.type === 'user').map((p) => p.id);
