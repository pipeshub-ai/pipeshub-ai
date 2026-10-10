import type { ConversationRef } from './collaboration-types';

type Listener = (ref: ConversationRef) => void;
const listeners = new Set<Listener>();

/** Who is in a chat changed (add, remove, level, settings, owner, leave). Lets caches follow without importing the API client. */
export function onCollaboratorsChanged(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function emitCollaboratorsChanged(ref: ConversationRef): void {
  for (const listener of listeners) listener(ref);
}

type AclListener = (ref: ConversationRef, version: string | number) => void;
const aclListeners = new Set<AclListener>();

/** The server's sharing version for a chat, from a feed answer (the header comes with a 304 too). */
export function onAclVersion(listener: AclListener): () => void {
  aclListeners.add(listener);
  return () => aclListeners.delete(listener);
}

export function emitAclVersion(ref: ConversationRef, version: string | number): void {
  for (const listener of aclListeners) listener(ref, version);
}
