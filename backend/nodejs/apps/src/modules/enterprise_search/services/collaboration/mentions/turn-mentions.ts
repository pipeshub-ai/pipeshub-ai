import { MentionRef } from './mention.types';

const admitted = new WeakMap<object, readonly MentionRef[]>();

/** Set by the mention gate once a send's mentions are validated; unset with the flags off, so nothing is stored or forwarded. */
export function setTurnMentions(
  req: object,
  mentions: readonly MentionRef[],
): void {
  admitted.set(req, mentions);
}

export function turnMentionsOf(req: object): readonly MentionRef[] {
  return admitted.get(req) ?? [];
}
