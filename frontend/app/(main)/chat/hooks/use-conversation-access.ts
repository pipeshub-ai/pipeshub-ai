import { useMemo } from 'react';
import { useChatStore } from '../store';
import { useFeatureFlagsStore, selectCollaborativeChatsEnabled } from '@/lib/store/feature-flags-store';
import type { ActiveRunDto } from '../collaboration-types';
import { toConversationAccess, type ConversationAccess } from '../utils/conversation-access';

export interface UseConversationAccessResult extends ConversationAccess {
  /** `ENABLE_COLLABORATIVE_CHATS` is on. With it off every capability follows today's owner-only rule. */
  collabEnabled: boolean;
  /** A poll or load answered 404/403; history stays, the composer goes. */
  accessLost: boolean;
  /** The owner is no longer active: read-only for everyone but the (inactive) owner until the chat is transferred. */
  ownerInactive: boolean;
  activeRun: ActiveRunDto | null;
  /** Composer is shown: the server allows sending and access was not lost. */
  showComposer: boolean;
  /** Read-only banner is shown: the flag is on, the user may only read, and access was not lost. */
  showReadOnlyBanner: boolean;
}

/** UI capabilities for one slot, derived only from the server's `access` view and the flag. */
export function useConversationAccess(slotId: string | null | undefined): UseConversationAccessResult {
  const collabEnabled = useFeatureFlagsStore(selectCollaborativeChatsEnabled);
  const api = useChatStore((s) => (slotId ? s.slots[slotId]?.access ?? null : null));
  const accessLost = useChatStore((s) => (slotId ? s.slots[slotId]?.accessLost ?? false : false));
  const ownerInactive = useChatStore((s) => (slotId ? s.slots[slotId]?.ownerInactive ?? false : false));
  const activeRun = useChatStore((s) => (slotId ? s.slots[slotId]?.activeRun ?? null : null));

  return useMemo(() => {
    const access = toConversationAccess(api, api?.isOwner ?? null, collabEnabled);
    const lost = collabEnabled && accessLost;
    const inactive = collabEnabled && ownerInactive && !lost && access.isOwner !== true;
    return {
      ...access,
      collabEnabled,
      accessLost: lost,
      ownerInactive: inactive,
      activeRun,
      showComposer: access.canSend && !lost && !inactive,
      showReadOnlyBanner: collabEnabled && (access.isReadOnly || inactive) && !lost,
    };
  }, [api, collabEnabled, accessLost, ownerInactive, activeRun]);
}
