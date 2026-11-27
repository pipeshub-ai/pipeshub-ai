/**
 * QA-14 QA14-N1: the Node canary scan finds every vector example and planted canary, wherever it sits in a value,
 * and reports a clean value as clean.
 */
import { expect } from 'chai';
import { canary, findCanaries, SHAPES, VECTOR_EXAMPLES, VECTOR_MASK } from './support/canary-scan';
import {
  REDACT_ONLY_PATTERN_SOURCES,
  SECRET_MASK,
  VENDOR_PATTERN_SOURCES,
} from '../../../src/modules/agent-harness/contracts/vectors/token-patterns';
import { redactTokens } from '../../../src/modules/agent-harness/domain/token-redaction';

describe('QA14-N1 Node canary scan', () => {
  it('every vector entry has an example its own pattern finds', () => {
    const kinds = new Set(VECTOR_EXAMPLES.map((e) => e.kind));
    expect(SHAPES.map((s) => s.kind).filter((k) => !kinds.has(k))).to.deep.equal([]);
  });

  for (const example of VECTOR_EXAMPLES) {
    it(`finds ${example.id} nested in a value`, () => {
      const value = { a: [{ b: new Map([['k', `prefix ${example.secret} suffix`]]) }] };
      expect(findCanaries(value)).to.include(`shape ${example.kind}`);
      expect(findCanaries({ masked: VECTOR_MASK })).to.deep.equal([]);
    });
  }

  it('finds planted secrets and private text, in keys and buffers too, and names them in part only', () => {
    const secret = canary();
    const note = canary('private');
    expect(secret).to.match(/^pipeshub-canary-[0-9a-f-]{36}$/);
    const found = findCanaries({ [`key ${secret}`]: 1, body: Buffer.from(`text ${note}.`) }, { secrets: [secret], privates: [note], shapes: 'none' });
    expect(found).to.deep.equal([`secret ${secret.slice(0, 8)}...`, `private ${note.slice(0, 8)}...`]);
    expect(found.join(' ')).to.not.include(secret);
  });

  it('Node\'s generated module is the vector, and its redactor masks every example', () => {
    expect([...VENDOR_PATTERN_SOURCES, ...REDACT_ONLY_PATTERN_SOURCES].map((p) => [p.kind, p.source])).to.deep.equal(
      SHAPES.map((s) => [s.kind, s.pattern.source]),
    );
    expect(SECRET_MASK).to.equal(VECTOR_MASK);
    for (const example of VECTOR_EXAMPLES) {
      const masked = redactTokens(`a ${example.secret} b ${example.secret}`);
      expect(masked, example.id).to.equal(`a ${VECTOR_MASK} b ${VECTOR_MASK}`);
    }
  });

  it('a clean value and a cyclic value report nothing', () => {
    const cyclic: Record<string, unknown> = { text: 'run completed in 3 steps' };
    cyclic.self = cyclic;
    expect(findCanaries(cyclic, { secrets: [canary()] })).to.deep.equal([]);
  });

  it('vendor mode leaves out the redact-only bearer heuristic', () => {
    const bearer = VECTOR_EXAMPLES.find((e) => e.kind === 'bearer');
    expect(bearer).to.not.equal(undefined);
    expect(findCanaries(bearer?.secret, { shapes: 'vendor' })).to.deep.equal([]);
    expect(findCanaries(bearer?.secret)).to.deep.equal(['shape bearer']);
  });
});
