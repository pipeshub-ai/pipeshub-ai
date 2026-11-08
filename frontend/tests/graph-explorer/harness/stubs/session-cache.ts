// The product resets per-user stores when the session ends; the harness has no session.
export function resetStoreOnSessionEnd(_store?: unknown): () => void {
  return () => undefined;
}
