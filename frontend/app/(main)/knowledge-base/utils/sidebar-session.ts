import { useAuthStore } from '@/lib/store/auth-store';
import { onSessionEnd } from '@/lib/api/session-cache';
import { useKnowledgeBaseStore } from '../store';
import { forgetPendingNodeChildrenRequests } from '../api';
import { resetRootListLoads } from './root-app-list';
import { resetFolderChildrenLoads } from './folder-children';
import { resetAppChildFetches } from './fetch-app-direct-children';
import { endKbSession } from './kb-session';

/**
 * Clears everything the knowledge base keeps between screens: the store and
 * the loaders' in-flight bookkeeping. Signing out does not reload the page on
 * the desktop app, so without this the next person to sign in (possibly in
 * another org) would see the previous sidebar until each list reloaded.
 */
export function resetKnowledgeBaseSession(): void {
  endKbSession();
  resetRootListLoads();
  resetFolderChildrenLoads();
  resetAppChildFetches();
  forgetPendingNodeChildrenRequests();
  useKnowledgeBaseStore.getState().reset();
}

let watching = false;

/**
 * Resets at every session end (sign-out, or a switch to another org while the
 * app stays open: the loaders' requests of the ended session never settle, so
 * nothing may keep waiting on them) and when a different user signs in.
 */
export function watchKnowledgeBaseSession(): void {
  if (watching) return;
  watching = true;
  onSessionEnd(resetKnowledgeBaseSession);
  useAuthStore.subscribe((state, previous) => {
    const userChanged = !!previous.user && !!state.user && previous.user.id !== state.user.id;
    if (userChanged) resetKnowledgeBaseSession();
  });
}

watchKnowledgeBaseSession();
