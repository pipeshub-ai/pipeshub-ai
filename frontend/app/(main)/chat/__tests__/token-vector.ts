/**
 * QA-11: the shared token vector as test data, read from the contract file rather
 * than the generated module, so a redactor that stops reading the vector, or a
 * pattern added without an example, fails the tests that use it.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

interface RawEntry {
  kind: string;
  source: string;
  forbidden?: boolean;
  examples?: string[][];
}

export interface VectorExample {
  /** `github_token-1`: the entry and the example's index, for test names. */
  id: string;
  kind: string;
  source: string;
  /** Joined from parts here: the file holds no literal credential. */
  secret: string;
}

const raw = JSON.parse(
  readFileSync(join(__dirname, '..', '..', '..', '..', '..', 'contracts', 'harness', 'vectors', 'token-patterns.json'), 'utf8'),
) as {
  mask: string;
  patterns: RawEntry[];
  redactOnly: RawEntry[];
  secretKeyNames: { matches: string[]; ignores: string[] };
};

export const VECTOR_MASK = raw.mask;
/** Key names whose value is a secret whatever it looks like, and ordinary ones, from the vector. */
export const SECRET_KEY_NAMES = raw.secretKeyNames;
export const VECTOR_ENTRIES: readonly RawEntry[] = [...raw.patterns, ...raw.redactOnly];
export const VECTOR_EXAMPLES: readonly VectorExample[] = VECTOR_ENTRIES.flatMap((entry) =>
  (entry.examples ?? []).map((parts, index) => ({
    id: `${entry.kind}-${index}`,
    kind: entry.kind,
    source: entry.source,
    secret: parts.join(''),
  })),
);
