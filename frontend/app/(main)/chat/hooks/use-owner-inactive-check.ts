import { useEffect } from 'react';
import { useChatStore } from '../store';
import { useFeatureFlagsStore, selectCollaborativeChatsEnabled } from '@/lib/store/feature-flags-store';
import { CollaborationApi } from '../collaboration-api';
import { refOf } from '../utils/collab-send';

/**
 * An editor who is not the owner finds out that the owner is inactive (D11) when the chat opens, not by failing
 * a send: the detail response carries no owner status, the readiness route does. A failed check leaves the
 * composer as it was; a send still gets the server's `OWNER_INACTIVE` answer.
 */
export function useOwnerInactiveCheck(slotId: string | null | undefined): void {
  const flagOn = useFeatureFlagsStore(selectCollaborativeChatsEnabled);
  const convId = useChatStore((s) => (slotId ? s.slots[slotId]?.convId ?? null : null));
  const agentKey = useChatStore((s) => (slotId ? s.slots[slotId]?.threadAgentId ?? null : null));
  const isTemp = useChatStore((s) => (slotId ? s.slots[slotId]?.isTemp ?? false : false));
  const isEditor = useChatStore((s) => (slotId ? s.slots[slotId]?.access?.role === 'write' : false));
  const known = useChatStore((s) => (slotId ? s.slots[slotId]?.ownerInactive ?? false : false));

  useEffect(() => {
    if (!flagOn || !slotId || !convId || isTemp || !isEditor || known) return;
    const controller = new AbortController();
    const ref = refOf({ convId, threadAgentId: agentKey });
    if (!ref) return;
    CollaborationApi.getReadiness(ref, controller.signal)
      .then((readiness) => {
        if (readiness.reasons.includes('OWNER_INACTIVE')) useChatStore.getState().updateSlot(slotId, { ownerInactive: true });
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [flagOn, slotId, convId, agentKey, isTemp, isEditor, known]);
}
