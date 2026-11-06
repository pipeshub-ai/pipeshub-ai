'use client';

import { useEffect } from 'react';
import { useChatStore } from '../store';

/**
 * For a composer outside the chat page, whose send starts a new chat there (the project workspace, a collection's
 * widget). The chat page's last open chat stays active after it unmounts, and the composer would read that chat's
 * people, room and run; a project left open would scope it too. A client-side navigation kept both, a reload did not.
 */
export function useNewChatComposerScope(projectId: string | null): void {
  useEffect(() => {
    const store = useChatStore.getState();
    store.clearActiveSlot();
    store.setActiveProjectId(projectId);
    return () => {
      if (projectId !== null && useChatStore.getState().activeProjectId === projectId) {
        useChatStore.getState().setActiveProjectId(null);
      }
    };
  }, [projectId]);
}
