'use client';

import { create } from 'zustand';

/**
 * Whether this deployment keeps toolset instances apart (`INSTANCE_TOOL_IDS`,
 * P2.2), as the toolset lists report it (`instanceToolIds`). Off, or never
 * reported, an agent holds one instance per toolset type, as before.
 */
interface ToolIdentityState {
  instanceToolIds: boolean;
}

export const useToolIdentityStore = create<ToolIdentityState>()(() => ({ instanceToolIds: false }));

/** Records what a toolset list answered; an absent field reads as off. */
export function observeInstanceToolIds(body: unknown): boolean {
  const on =
    body !== null && typeof body === 'object' && (body as { instanceToolIds?: unknown }).instanceToolIds === true;
  if (useToolIdentityStore.getState().instanceToolIds !== on) useToolIdentityStore.setState({ instanceToolIds: on });
  return on;
}

export const selectInstanceToolIds = (s: ToolIdentityState) => s.instanceToolIds;
