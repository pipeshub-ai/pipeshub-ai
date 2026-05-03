'use client';

import { useSearchParams } from 'next/navigation';
import { useChatStore } from '@/chat/store';

/**
 * Resolve the "effective" agent id for the current chat view.
 *
 * Precedence:
 *   1. The active slot's `threadAgentId` (the agent the current thread is
 *      pinned to — survives conversation navigation even if the URL is late
 *      or missing the `agentId` param).
 *   2. The URL `agentId` search param.
 *   3. `null` — we're in the Assistant (non-agent) context.
 *
 * This is the ONE place agent-context is resolved. All consumers (page.tsx,
 * chat input wrapper, model panel, agent header, runtime / streaming submit
 * paths) should import this hook (or its store-level twin `ctxKeyFromAgent`)
 * rather than re-reading the URL and the slot independently.
 */
export function useEffectiveAgentId(): string | null {
  const rawUrl = useSearchParams().get('agentId');
  const slotAgentId = useChatStore((s) => {
    const sid = s.activeSlotId;
    return sid ? s.slots[sid]?.threadAgentId?.trim() ?? '' : '';
  });
  return slotAgentId || (rawUrl?.trim() ? rawUrl : null);
}

export { ASSISTANT_CTX, ctxKeyFromAgent, getEffectiveModel } from '@/chat/store';
