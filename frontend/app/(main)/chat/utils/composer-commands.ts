type Listener = () => void;

const mentionListeners = new Set<Listener>();

/** Asks the mounted composer to insert "@" at the caret, which opens its mention popover. */
export function requestComposerMention(): void {
  mentionListeners.forEach((fn) => fn());
}

export function onComposerMentionRequest(fn: Listener): () => void {
  mentionListeners.add(fn);
  return () => {
    mentionListeners.delete(fn);
  };
}
