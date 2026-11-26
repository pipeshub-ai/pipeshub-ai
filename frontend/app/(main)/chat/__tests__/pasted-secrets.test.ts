/**
 * SEC-CR-07 (G-37): the composer finds what the server removes, from the same
 * vector rules, so the person is warned before send. Read from the contract
 * file, as the Node and Python twins of this test are.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { SECRET_MASK } from '@/lib/contracts/harness/vectors/token-patterns';
import { findPastedSecrets, redactPastedSecrets } from '../pasted-secrets';
import { VECTOR_EXAMPLES } from './token-vector';

interface Entry {
  kind: string;
  examples: string[][];
}

const raw = JSON.parse(
  readFileSync(join(__dirname, '..', '..', '..', '..', '..', 'contracts', 'harness', 'vectors', 'token-patterns.json'), 'utf8'),
) as { pastedInput: { detectors: Entry[]; assigned: Entry; contextual: Entry; negatives: string[] } };
const pasted = raw.pastedInput;
const EXAMPLES = [...pasted.detectors, pasted.assigned, pasted.contextual].flatMap((entry) =>
  entry.examples.map((parts, index) => ({ id: `${entry.kind}-${index}`, kind: entry.kind, text: parts.join('') })),
);

describe('SEC-CR-07 composer secret check', () => {
  it.each(EXAMPLES)('finds and masks $id', ({ kind, text }) => {
    const found = findPastedSecrets(text);
    expect(found.map((f) => f.kind)).toEqual([kind]);
    const secret = text.slice(found[0].start, found[0].end);
    const redacted = redactPastedSecrets(text);
    expect(redacted.text).toContain(SECRET_MASK);
    expect(redacted.text).not.toContain(secret);
  });

  it.each(VECTOR_EXAMPLES)('counts the token vector\'s $id as pasted', ({ secret }) => {
    const redacted = redactPastedSecrets(`use this one: ${secret} thanks`);
    expect(redacted.kinds).toHaveLength(1);
    expect(redacted.text).not.toContain(secret);
  });

  it.each(pasted.negatives)('leaves ordinary text alone: %s', (text) => {
    expect(findPastedSecrets(text)).toEqual([]);
  });
});
