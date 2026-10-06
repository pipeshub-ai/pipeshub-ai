import { useChatStore } from '../store';
import { useUserStore } from '@/lib/store/user-store';
import { useConversationAccess, type UseConversationAccessResult } from './use-conversation-access';

export interface CollabMessageContext {
  /** Flag on and the active chat has other people in it: attribution and bindings apply. */
  collabActive: boolean;
  access: UseConversationAccessResult;
  meUserId: string | null;
}

/** What message rows need to decide on attribution, the ask card and Regenerate. */
export function useCollabMessageContext(): CollabMessageContext {
  const activeSlotId = useChatStore((s) => s.activeSlotId);
  const access = useConversationAccess(activeSlotId);
  const meUserId = useUserStore((s) => s.profile?.userId ?? null);
  return { collabActive: access.collabEnabled && access.isCollaborative, access, meUserId };
}
