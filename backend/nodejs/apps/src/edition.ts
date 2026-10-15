/**
 * Edition constants that must stay import-free, so any module can read them
 * without an import cycle through `config.ts`.
 */

/** Sign-in methods this edition does not offer; saved policies that name them are rewritten. */
export const REMOVED_SIGN_IN_METHODS: readonly string[] = ['samlSso', 'oauth'];

/** Sign-in methods this edition offers under another name. */
export const RENAMED_SIGN_IN_METHODS: Readonly<Record<string, string>> = {
  azureAd: 'microsoft',
};
