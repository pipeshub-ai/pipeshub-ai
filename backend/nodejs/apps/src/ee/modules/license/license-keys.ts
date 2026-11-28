// Copyright (c) 2026 PipesHub Inc.
// Licensed under the PipesHub Enterprise License. See the LICENSE file in this
// ee folder. Free for development, testing and evaluation. Production use needs
// a PipesHub licence key.

import { createHash, createPublicKey, type KeyObject } from 'crypto';
import {
  BUILT_IN_LICENSE_PUBLIC_KEYS,
  LICENSE_KEY_FROM_ENV,
} from './license-trust';

export const LICENSE_PUBLIC_KEY_ENV = 'PIPESHUB_LICENSE_PUBLIC_KEY';

export interface LicenseKeyConfig {
  source: 'built-in' | 'env' | 'none';
  keys: readonly KeyObject[];
  problems: string[];
}

type Env = Readonly<Record<string, string | undefined>>;

const PEM_BLOCK =
  /-----BEGIN PUBLIC KEY-----([\s\S]*?)-----END PUBLIC KEY-----/g;
const BASE64 = /^[A-Za-z0-9+/]+={0,2}$/;
const NOT_KEY_TEXT = /\\[nr]|\s/g;

const pemOf = (base64: string): string =>
  `-----BEGIN PUBLIC KEY-----\n${base64}\n-----END PUBLIC KEY-----\n`;

/** Every key in `text`: PEM blocks, or the one-line body of a single key. */
export function parseLicensePublicKeys(text: string): {
  keys: KeyObject[];
  problems: string[];
} {
  const blocks = [...text.matchAll(PEM_BLOCK)].map((match) => match[1] ?? '');
  if (blocks.length === 0 && text.trim() !== '') blocks.push(text);
  const keys: KeyObject[] = [];
  const problems: string[] = [];
  blocks.forEach((block, index) => {
    const key = ed25519KeyOf(block.replace(NOT_KEY_TEXT, ''));
    if (key) {
      keys.push(key);
    } else {
      const which =
        blocks.length > 1 ? `key ${String(index + 1)}` : 'the value';
      problems.push(`${which} is not an Ed25519 public key`);
    }
  });
  return { keys, problems };
}

function ed25519KeyOf(body: string): KeyObject | null {
  if (!BASE64.test(body)) return null;
  try {
    const key = createPublicKey(pemOf(body));
    return key.asymmetricKeyType === 'ed25519' ? key : null;
  } catch {
    return null;
  }
}

/** The built-in keys, when the build has any; only a build without them reads the env key. */
export function resolveLicenseKeys(
  env: Env = process.env,
  builtInPems: readonly string[] = BUILT_IN_LICENSE_PUBLIC_KEYS,
  keyFromEnv: boolean = LICENSE_KEY_FROM_ENV,
): LicenseKeyConfig {
  const inline = env[LICENSE_PUBLIC_KEY_ENV]?.trim() ?? '';
  // Decided by the list, not by its valid keys: a garbled built-in key must never let env choose.
  if (builtInPems.length > 0) {
    const builtIn = parseLicensePublicKeys(builtInPems.join('\n'));
    const problems = builtIn.problems.map(
      (problem) => `built-in license public keys: ${problem}`,
    );
    if (inline !== '') {
      problems.push(
        `${LICENSE_PUBLIC_KEY_ENV} is ignored: this build trusts only its built-in keys`,
      );
    }
    return { source: 'built-in', keys: builtIn.keys, problems };
  }
  if (!keyFromEnv) {
    return {
      source: 'none',
      keys: [],
      problems:
        inline === ''
          ? []
          : [
              `${LICENSE_PUBLIC_KEY_ENV} is ignored: this build trusts only its built-in keys`,
            ],
    };
  }
  if (inline === '') return { source: 'none', keys: [], problems: [] };
  const parsed = parseLicensePublicKeys(inline);
  return {
    source: 'env',
    keys: parsed.keys,
    problems: parsed.problems.map(
      (problem) => `${LICENSE_PUBLIC_KEY_ENV}: ${problem}`,
    ),
  };
}

let resolved: { at: string; config: LicenseKeyConfig } | null = null;
let override: readonly KeyObject[] | null = null;

/** The deployment's key configuration, read again only when the variable changes. */
export function licenseKeyConfig(): LicenseKeyConfig {
  const at = process.env[LICENSE_PUBLIC_KEY_ENV] ?? '';
  if (resolved?.at !== at) {
    resolved = { at, config: resolveLicenseKeys() };
  }
  return resolved.config;
}

export function trustedLicenseKeys(): readonly KeyObject[] {
  return override ?? licenseKeyConfig().keys;
}

/** A short SHA-256 of the key, so an operator can tell which key is loaded. */
export const licenseKeyFingerprint = (key: KeyObject): string =>
  createHash('sha256')
    .update(key.export({ format: 'der', type: 'spki' }))
    .digest('hex')
    .slice(0, 16);

/** Test seam: trust only these PEM keys, ignoring env, until the returned restore runs. */
export function useLicenseKeysForTesting(pems: readonly string[]): () => void {
  const previous = override;
  override = Object.freeze(pems.map((pem) => createPublicKey(pem)));
  return () => {
    override = previous;
  };
}
