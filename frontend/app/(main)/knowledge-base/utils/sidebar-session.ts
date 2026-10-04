import { useAuthStore } from '@/lib/store/auth-store';
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
 * Resets on sign-out and when a different user signs in. The app has no
 * in-place org switcher: changing org means signing in again, which this
 * covers.
 */
export function watchKnowledgeBaseSession(): void {
  if (watching) return;
  watching = true;
  useAuthStore.subscribe((state, previous) => {
    const signedOut = previous.isAuthenticated && !state.isAuthenticated;
    const userChanged = !!previous.user && !!state.user && previous.user.id !== state.user.id;
    if (signedOut || userChanged) resetKnowledgeBaseSession();
  });
}

watchKnowledgeBaseSession();
