// Copyright (c) 2026 PipesHub Inc.
// Licensed under the PipesHub Enterprise License. See the LICENSE file in this
// ee folder. Free for development, testing and evaluation. Production use needs
// a PipesHub licence key.

/**
 * PipesHub's license signing public keys, as PEM blocks. A build with a key
 * here trusts only these, so a license can only come from PipesHub. The image
 * build writes its key here, matching this line's exact text. A run from
 * source, with no key here, reads PIPESHUB_LICENSE_PUBLIC_KEY.
 */
export const BUILT_IN_LICENSE_PUBLIC_KEYS: readonly string[] = [];

/** The image build sets this to false, so an image never reads a key from env. */
export const LICENSE_KEY_FROM_ENV: boolean = true;
