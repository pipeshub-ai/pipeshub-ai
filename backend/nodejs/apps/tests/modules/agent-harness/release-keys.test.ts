/**
 * R41SUPPLY-FU-6 (ADR-SEC-06 D1): release keys expire on their own, and a
 * signed list revokes them, in Node as in Python.
 *
 * - RK-1: the shared vectors (contracts/release-keys/revocation-vectors.json),
 *   the same file Python's test_release_key_revocation.py runs.
 * - RK-2: Node's key table and not-after are Python's.
 * - RK-3: past its not-after, the shipped key verifies no vendor rules, and
 *   the rules verify again only while a key is in force.
 * - RK-4: the stored-copy reader: a copy that does not verify, a store that is
 *   down, or nothing stored leaves the list in force.
 *
 * Every key here is generated; no release key signs anything.
 */
import { expect } from 'chai';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import * as sinon from 'sinon';
import {
  applyRevocations,
  NO_REVOCATIONS,
  parseInstant,
  releaseKeySet,
  ReleaseKey,
  RevocationRefused,
  revocationsInForce,
  resetRevocationsForTests,
  trustedReleaseKeys,
  usableKeys,
  verifyRevocations,
  formatInstant,
  Revocations,
} from '../../../src/modules/agent-harness/domain/release-keys';
import { releaseVendorRules } from '../../../src/modules/agent-harness/domain/model-access';
import {
  RELEASE_KEYS,
  RELEASE_KEY_NOT_AFTER,
} from '../../../src/modules/agent-harness/domain/vendor-rules-release';
import { ReleaseKeyRevocationReader } from '../../../src/modules/agent-harness/application/release-key-revocations';

const REPO = join(__dirname, '../../../../../..');

interface RawKey {
  public: string;
  notAfter: string;
}
interface RawCurrent {
  issued: string | null;
  revoked: string[];
}
interface Vectors {
  keys: Record<string, RawKey>;
  usable: Array<{ name: string; revoked: string[]; now: string; expect: string[] }>;
  revocations: Array<{
    name: string;
    document: string | null;
    signature: string | null;
    current: RawCurrent;
    now: string;
    expect: { refused: string } | { issued: string; revoked: string[] };
  }>;
}

const VECTORS = JSON.parse(
  readFileSync(join(REPO, 'contracts/release-keys/revocation-vectors.json'), 'utf8'),
) as Vectors;

const instant = (value: string): number => {
  const ms = parseInstant(value);
  if (ms === null) throw new Error(`not an instant: ${value}`);
  return ms;
};

const KEYS: Record<string, ReleaseKey> = Object.fromEntries(
  Object.entries(VECTORS.keys).map(([id, key]) => [
    id,
    { publicKey: key.public, notAfter: instant(key.notAfter) },
  ]),
);

const current = (raw: RawCurrent): Revocations => ({
  issued: raw.issued === null ? null : instant(raw.issued),
  revoked: new Set(raw.revoked),
});

describe('R41SUPPLY-FU-6 release keys: the shared vectors (RK-1)', () => {
  for (const c of VECTORS.usable) {
    it(`usable: ${c.name}`, () => {
      const got = usableKeys(
        KEYS,
        { issued: null, revoked: new Set(c.revoked) },
        instant(c.now),
      );
      expect(Object.keys(got).sort()).to.deep.equal(c.expect);
    });
  }
  for (const c of VECTORS.revocations) {
    it(`revocations: ${c.name}`, () => {
      let outcome: unknown;
      try {
        const got = verifyRevocations(
          c.document,
          c.signature,
          KEYS,
          current(c.current),
          instant(c.now),
        );
        outcome = {
          issued: got.issued === null ? null : formatInstant(got.issued),
          revoked: [...got.revoked].sort(),
        };
      } catch (error) {
        if (!(error instanceof RevocationRefused)) throw error;
        outcome = { refused: error.reason };
      }
      expect(outcome).to.deep.equal(c.expect);
    });
  }
});

describe('R41SUPPLY-FU-6 release keys in force', () => {
  let clock: sinon.SinonFakeTimers | null = null;
  afterEach(() => {
    clock?.restore();
    clock = null;
    resetRevocationsForTests();
  });

  it("RK-2: Node's keys and not-after are Python's", () => {
    const python = readFileSync(
      join(REPO, 'backend/python/harness_engines/vendor_rules.py'),
      'utf8',
    );
    const notAfter = Object.fromEntries(
      [...python.matchAll(/"([a-z0-9-]+)":\s*"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)"/g)].map(
        (m) => [m[1], m[2]],
      ),
    );
    expect(RELEASE_KEY_NOT_AFTER).to.deep.equal(notAfter);
    expect(Object.keys(releaseKeySet()).sort()).to.deep.equal(
      Object.keys(RELEASE_KEYS).sort(),
    );
  });

  it('RK-3: past its not-after the shipped key verifies no vendor rules', () => {
    const notAfter = instant(Object.values(RELEASE_KEY_NOT_AFTER)[0] ?? expect.fail('a shipped key'));
    clock = sinon.useFakeTimers({ now: notAfter - 1000, toFake: ['Date'] });
    expect(releaseVendorRules().rules.length).to.be.greaterThan(0);
    clock.setSystemTime(notAfter);
    expect(trustedReleaseKeys()).to.deep.equal({});
    expect(() => releaseVendorRules()).to.throw().with.property('detail', 'unknown_key');
    clock.setSystemTime(notAfter - 1000);
    expect(releaseVendorRules().rules.length).to.be.greaterThan(0);
  });

  it('RK-3: a list no release key signed is refused and changes nothing', () => {
    const c = VECTORS.revocations.find(
      (v) => v.name === 'a first list, signed by a usable key',
    );
    expect(c).to.not.equal(undefined);
    expect(() => applyRevocations(c!.document, c!.signature)).to.throw(
      RevocationRefused,
    );
    expect(revocationsInForce()).to.equal(NO_REVOCATIONS);
    expect(Object.keys(trustedReleaseKeys())).to.deep.equal(
      Object.keys(RELEASE_KEYS),
    );
  });

  it('RK-4: the reader keeps the list in force whatever the store holds', async () => {
    const warned: string[] = [];
    const logger = {
      debug: () => undefined,
      info: () => undefined,
      warn: (message: string) => void warned.push(message),
      error: () => undefined,
    };
    const forged = VECTORS.revocations[0] ?? expect.fail('a revocation vector');
    for (const store of [
      { get: () => Promise.resolve(null) },
      { get: () => Promise.reject(new Error('store down')) },
      { get: () => Promise.resolve({ document: 1, signature: null }) },
      {
        get: () =>
          Promise.resolve({ document: forged.document, signature: forged.signature }),
      },
    ]) {
      await new ReleaseKeyRevocationReader(
        store as never,
        logger,
      ).readOnce();
      expect(revocationsInForce()).to.equal(NO_REVOCATIONS);
    }
    expect(warned).to.deep.equal([
      'agent_harness.release_keys.store_unreadable',
      'agent_harness.release_keys.revocations_refused',
    ]);
  });
});
