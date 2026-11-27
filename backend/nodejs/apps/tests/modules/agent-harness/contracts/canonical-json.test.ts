import { expect } from 'chai';
import { createHash } from 'crypto';
import { readFileSync } from 'fs';
import { join } from 'path';
import { inspect } from 'util';
import fc from 'fast-check';
import {
  assertNoReservedKeys,
  CanonicalJsonError,
  canonicalDigest,
  canonicalJson,
  canonicalJsonBytes,
  canonicalSha256,
} from '../../../../src/modules/agent-harness/contracts/canonical-json';
import { CONTRACTS_DIR } from '../../../support/harness/contracts';

interface Vectors {
  vectors: Array<{ name: string; input: string; expected: string; sha256: string }>;
  errors: Array<{ name: string; input: string; languages: string[] }>;
}

const vectors = JSON.parse(
  readFileSync(join(CONTRACTS_DIR, 'vectors', 'canonical-json.json'), 'utf8'),
) as Vectors;

describe('agent-harness contracts: RFC 8785 canonical JSON', () => {
  describe('C3 golden vectors (shared with harness_core)', () => {
    for (const vector of vectors.vectors) {
      it(vector.name, () => {
        const bytes = canonicalJsonBytes(JSON.parse(vector.input));
        expect(bytes.toString('utf8')).to.equal(vector.expected);
        expect(createHash('sha256').update(bytes).digest('hex')).to.equal(vector.sha256);
      });
    }
    for (const error of vectors.errors.filter((e) => e.languages.includes('typescript'))) {
      it(`rejects: ${error.name}`, () => {
        expect(() => canonicalJson(JSON.parse(error.input))).to.throw(CanonicalJsonError);
      });
    }
  });

  it('reserves __proto__ at any depth, including inside arrays', () => {
    expect(() => assertNoReservedKeys(JSON.parse('[1,{"a":[{"__proto__":null}]}]'))).to.throw(
      CanonicalJsonError,
    );
    expect(() => canonicalJson(JSON.parse('{"__proto__":{}}'))).to.throw(CanonicalJsonError);
    expect(() => assertNoReservedKeys({ constructor: 1, prototype: { a: null } })).not.to.throw();
  });

  it('sorts keys by UTF-16 code units, not locale (the old localeCompare order differed)', () => {
    expect(canonicalJson({ b: 1, a: 2, B: 3 })).to.equal('{"B":3,"a":2,"b":1}');
  });

  it('rejects values JSON cannot carry', () => {
    for (const bad of [NaN, Infinity, -Infinity, BigInt(1), () => 1, Symbol('s'), new Map(), [undefined]]) {
      expect(() => canonicalJson(bad), inspect(bad)).to.throw(CanonicalJsonError);
    }
  });

  it('omits undefined members and applies toJSON like JSON.stringify', () => {
    const at = new Date('2026-10-01T12:00:00.000Z');
    expect(canonicalJson({ z: undefined, at, n: null })).to.equal(
      '{"at":"2026-10-01T12:00:00.000Z","n":null}',
    );
  });

  it('digest helpers hash the canonical bytes', () => {
    const hex = createHash('sha256').update('{"a":1,"b":[true]}').digest('hex');
    expect(canonicalSha256({ b: [true], a: 1 })).to.equal(hex);
    expect(canonicalDigest({ b: [true], a: 1 })).to.equal(`sha256:${hex}`);
  });

  it('property: canonical form is a fixed point and ignores key order', () => {
    fc.assert(
      fc.property(fc.jsonValue(), (value) => {
        const once = canonicalJson(value);
        expect(canonicalJson(JSON.parse(once))).to.equal(once);
        const shuffled = JSON.parse(JSON.stringify(value), (_key, v: unknown) =>
          v !== null && typeof v === 'object' && !Array.isArray(v)
            ? Object.fromEntries(Object.entries(v as Record<string, unknown>).reverse())
            : v,
        ) as unknown;
        expect(canonicalJson(shuffled)).to.equal(once);
      }),
      { numRuns: 300, seed: 8785 },
    );
  });

  it('property: numbers match JSON.stringify for every finite double', () => {
    fc.assert(
      fc.property(fc.double({ noNaN: true, noDefaultInfinity: true }), (n) => {
        expect(canonicalJson(n)).to.equal(Object.is(n, -0) ? '0' : JSON.stringify(n));
      }),
      { numRuns: 1000, seed: 8785 },
    );
  });
});
