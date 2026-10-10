import { useFeatureFlagsStore, selectCollaborativeChatsEnabled } from '@/lib/store/feature-flags-store';

/** `ENABLE_COLLABORATIVE_CHATS` outside React. With it off no collaboration code path runs. */
export function collabSendEnabled(): boolean {
  return selectCollaborativeChatsEnabled(useFeatureFlagsStore.getState());
}
