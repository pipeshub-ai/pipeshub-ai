'use client';

import { createContext, useContext } from 'react';
import { useChatStore } from '../../store';

/**
 * The slot a reply is rendered for. It is the active slot unless a Compare
 * column renders a hidden branch's reply, whose cards must act on that branch.
 */
export const RenderSlotContext = createContext<string | null>(null);

export function useRenderSlotId(): string | null {
  const override = useContext(RenderSlotContext);
  const active = useChatStore((s) => s.activeSlotId);
  return override ?? active;
}
