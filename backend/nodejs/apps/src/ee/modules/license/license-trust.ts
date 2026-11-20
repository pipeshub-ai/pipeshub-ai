/**
 * PipesHub's license signing public keys, as PEM blocks. A build with a key
 * here trusts only these, so a license can only come from PipesHub. The image
 * build writes its key here, matching this line's exact text. A run from
 * source, with no key here, reads PIPESHUB_LICENSE_PUBLIC_KEY.
 */
export const BUILT_IN_LICENSE_PUBLIC_KEYS: readonly string[] = [];

/** The image build sets this to false, so an image never reads a key from env. */
export const LICENSE_KEY_FROM_ENV: boolean = true;
