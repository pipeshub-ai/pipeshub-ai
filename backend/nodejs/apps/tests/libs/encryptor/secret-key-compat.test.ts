import { expect } from 'chai';
import crypto from 'node:crypto';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import {
  cipherRing,
  hashedSecret,
  platformCipherKey,
  SecretKeyRingError,
  secretKeyRing,
  writerKdf,
} from '../../../src/libs/encryptor/secret-key-ring';
import {
  hardenedProfile,
  resetCompatWarningsForTests,
} from '../../../src/libs/utils/security-profile';

/**
 * R51-COMPAT item 2 (ADR-OPS-07): with PIPESHUB_HARDENED unset an upgraded install keeps starting with the key it
 * had, and writes under the SHA-256 key a release without SEC-D-05 reads, so a rollback still opens what was written.
 */
const STRONG = crypto.createHash('sha256').update('r51 strong').digest('hex');
const WEAK = 'your_random_encryption_secret_key';

describe('R51-COMPAT: the security profile switch', () => {
  beforeEach(() => resetCompatWarningsForTests());

  it('reads compatibility when unset, blank, false or not a boolean', () => {
    expect(hardenedProfile({})).to.equal(false);
    expect(hardenedProfile({ PIPESHUB_HARDENED: '' })).to.equal(false);
    expect(hardenedProfile({ PIPESHUB_HARDENED: 'no' })).to.equal(false);
    expect(hardenedProfile({ PIPESHUB_HARDENED: 'maybe' })).to.equal(false);
  });

  it('reads hardened for 1/true/yes/on in any case', () => {
    for (const value of ['1', 'true', 'TRUE', 'Yes', 'on']) {
      expect(hardenedProfile({ PIPESHUB_HARDENED: value }), value).to.equal(true);
    }
  });
});

describe('R51-COMPAT item 2: SECRET_KEY under the compatibility profile', () => {
  beforeEach(() => resetCompatWarningsForTests());

  it('accepts a weak SECRET_KEY with a warning that never carries the value', () => {
    const warnings: string[] = [];
    const listener = (w: Error) => warnings.push(w.message);
    process.on('warning', listener);
    expect(secretKeyRing({ SECRET_KEY: WEAK }).current).to.equal(WEAK);
    return new Promise<void>((resolve) =>
      setImmediate(() => {
        process.removeListener('warning', listener);
        expect(warnings.some((m) => m.includes('SECRET_KEY'))).to.equal(true);
        expect(warnings.join(' ')).not.to.include(WEAK);
        resolve();
      }),
    );
  });

  it('still refuses a weak SECRET_KEY on the hardened profile', () => {
    expect(() => secretKeyRing({ SECRET_KEY: WEAK, PIPESHUB_HARDENED: 'true' })).to.throw(
      SecretKeyRingError,
    );
  });

  it('writes under the SHA-256 key a previous release reads, and still reads the HKDF key', () => {
    const env = { SECRET_KEY: STRONG };
    expect(writerKdf(env)).to.equal('sha256');
    const keys = cipherRing(secretKeyRing(env), env);
    expect(keys[0]).to.equal(hashedSecret(STRONG));
    expect(keys).to.include(platformCipherKey(STRONG));
  });

  it('writes under HKDF on the hardened profile', () => {
    const env = { SECRET_KEY: STRONG, PIPESHUB_HARDENED: 'true' };
    expect(writerKdf(env)).to.equal('hkdf');
    expect(cipherRing(secretKeyRing(env), env)[0]).to.equal(platformCipherKey(STRONG));
  });

  it('lets SECRET_KEY_KDF decide over the profile', () => {
    expect(writerKdf({ SECRET_KEY_KDF: 'hkdf' })).to.equal('hkdf');
    expect(writerKdf({ SECRET_KEY_KDF: 'sha256', PIPESHUB_HARDENED: 'true' })).to.equal('sha256');
  });
});

interface ProfileCase {
  name: string;
  env: Record<string, string>;
  secret: 'weak' | 'strong';
  refused: boolean;
  writer: 'sha256' | 'hkdf' | null;
}

const PROFILE_CASES = (
  JSON.parse(
    readFileSync(path.resolve(__dirname, '../../../../../../contracts/secret-key/profile-cases.json'), 'utf8'),
  ) as { cases: ProfileCase[] }
).cases;

// The same cases Python's ring is held to, so both rings agree on what an upgraded install starts with and writes.
describe('R51-COMPAT: contracts/secret-key/profile-cases.json (Node)', () => {
  for (const c of PROFILE_CASES) {
    it(c.name, () => {
      const env = { ...c.env, SECRET_KEY: c.secret === 'weak' ? WEAK : STRONG };
      if (c.refused) {
        expect(() => secretKeyRing(env)).to.throw(SecretKeyRingError);
        return;
      }
      const writer = cipherRing(secretKeyRing(env), env)[0];
      const secret = env.SECRET_KEY;
      expect(writer).to.equal(c.writer === 'hkdf' ? platformCipherKey(secret) : hashedSecret(secret));
    });
  }
});

