import { useMemo } from 'react';
import { useChatStore } from '../store';
import { useFeatureFlagsStore, selectCollaborativeChatsEnabled } from '@/lib/store/feature-flags-store';
import type { Conversation } from '../types';
import { hideAccessLost } from '../utils/hide-access-lost';

const SEPARATOR = '\u0000';

/**
 * `hideAccessLost` for lists a component fetched itself (the "more chats" panels). It subscribes
 * to the ids of the slots that lost access, not to `slots`, so streaming updates do not re-render the list.
 */
export function useHideAccessLost<T extends Pick<Conversation, 'id'>>(list: T[]): T[] {
  const enabled = useFeatureFlagsStore(selectCollaborativeChatsEnabled);
  const lostKey = useChatStore((s) =>
    enabled
      ? Object.values(s.slots)
          .filter((slot) => slot.accessLost && slot.convId)
          .map((slot) => slot.convId)
          .sort()
          .join(SEPARATOR)
      : '',
  );
  return useMemo(() => {
    if (!lostKey) return list;
    const slots = Object.fromEntries(
      lostKey.split(SEPARATOR).map((convId) => [convId, { convId, accessLost: true }]),
    );
    return hideAccessLost(list, slots, true);
  }, [list, lostKey]);
}
