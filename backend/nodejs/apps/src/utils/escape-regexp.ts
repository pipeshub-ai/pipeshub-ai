/** Escapes every regex metacharacter so `text` matches only itself inside a RegExp. */
export const escapeRegExp = (text: string): string =>
  text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
