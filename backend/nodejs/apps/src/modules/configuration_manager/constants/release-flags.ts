import { COLLAB_FLAG_KEYS } from './constants';

export interface ReleaseFlag {
  key: string;
  owner: string;
  /** ISO date (YYYY-MM-DD). */
  createdAt: string;
  /** ISO date (YYYY-MM-DD); release-flags.test.ts fails after this date. */
  removeBy: string;
}

// Owner and removeBy are placeholders until PH-12 rollout is scheduled.
export const RELEASE_FLAGS: readonly ReleaseFlag[] = [
  COLLAB_FLAG_KEYS.collaborativeChats,
  COLLAB_FLAG_KEYS.chatMentions,
  COLLAB_FLAG_KEYS.chatAgentBuilder,
].map((key) => ({
  key,
  owner: 'collab-chats-owner',
  createdAt: '2026-10-01',
  removeBy: '2027-06-30',
}));

export const MAX_LIVE_RELEASE_FLAGS = 3;
