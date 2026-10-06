import aliases from './reserved-aliases.json';

/** `@word` forms that address the chat's own responder. */
export const ASSISTANT_ALIASES: readonly string[] = aliases.assistant;
/** Reserved but not yet acting: `@everyone`, `@here`, `@all` do nothing in v1. */
export const INERT_ALIASES: readonly string[] = aliases.inert;
export const RESERVED_ALIASES: readonly string[] = [
  ...ASSISTANT_ALIASES,
  ...INERT_ALIASES,
];
