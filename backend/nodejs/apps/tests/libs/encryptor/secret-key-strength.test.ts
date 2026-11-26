import { expect } from 'chai';
import crypto from 'node:crypto';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { EncryptionService } from '../../../src/libs/encryptor/encryptor';
import { SecretKeyRotation } from '../../../src/modules/agent-harness/application/secret-key-rotation';
import { ConfigStoreReencryptStep } from '../../../src/modules/agent-harness/application/secret-key-rotation-steps';
import {
  cipherRing,
  hashedSecret,
  platformCipherKey,
  SecretKeyRingError,
  secretKeyRing,
  weakSecretReason,
} from '../../../src/libs/encryptor/secret-key-ring';

/**
 * SEC-D-05: SECRET_KEY strength, and the platform cipher's key derivation (ADR-KEY-02).
 * The cases are shared with Python (`contracts/secret-key/strength-cases.json`), so the two rings refuse alike.
 */
interface StrengthCase {
  name: string;
  secret: string;
  reason: string | null;
}

const CASES = (
  JSON.parse(
    readFileSync(
      path.resolve(__dirname, '../../../../../../contracts/secret-key/strength-cases.json'),
      'utf8',
    ),
  ) as { cases: StrengthCase[] }
).cases;

const STRONG = crypto.createHash('sha256').update('sec-d-05 strong').digest('hex');
const OTHER = crypto.createHash('sha256').update('sec-d-05 other').digest('hex');

describe('SEC-D-05: SECRET_KEY strength', () => {
  for (const c of CASES) {
    it(`${c.name}: ${c.reason ?? 'accepted'}`, () => {
      expect(weakSecretReason(c.secret)).to.equal(c.reason);
    });
  }

  it('refuses a weak SECRET_KEY at start, saying what to do and never the value', () => {
    let message = '';
    try {
      secretKeyRing({ PIPESHUB_HARDENED: 'true', SECRET_KEY: 'your_random_encryption_secret_key' });
    } catch (error) {
      expect(error).to.be.instanceOf(SecretKeyRingError);
      message = (error as Error).message;
    }
    expect(message).to.match(/^SECRET_KEY is a published placeholder/);
    expect(message).to.include('openssl rand -base64 32');
    expect(message).to.include('SECRET_KEY_PREVIOUS');
    expect(message).not.to.include('your_random_encryption_secret_key');
  });

  it('refuses a weak SECRET_KEY_NEXT: it is about to be written under', () => {
    expect(() => secretKeyRing({ PIPESHUB_HARDENED: 'true', SECRET_KEY: STRONG, SECRET_KEY_NEXT: 'changeme' })).to.throw(
      SecretKeyRingError,
      /^SECRET_KEY_NEXT is a published placeholder/,
    );
  });

  it('accepts a weak SECRET_KEY_PREVIOUS: that is how an install moves off a weak key', () => {
    const ring = secretKeyRing({ PIPESHUB_HARDENED: 'true', SECRET_KEY: STRONG, SECRET_KEY_PREVIOUS: 'changeme' });
    expect(ring.other?.role).to.equal('previous');
  });

  it('a weak previous key never becomes the writer', () => {
    const keys = cipherRing(secretKeyRing({ PIPESHUB_HARDENED: 'true', SECRET_KEY: STRONG, SECRET_KEY_PREVIOUS: 'changeme' }), { PIPESHUB_HARDENED: 'true' });
    expect(keys[0]).to.equal(platformCipherKey(STRONG));
    expect(keys).to.deep.equal([
      platformCipherKey(STRONG),
      hashedSecret(STRONG),
      platformCipherKey('changeme'),
      hashedSecret('changeme'),
    ]);
  });
});

describe('SEC-D-05: the platform cipher key is HKDF of the secret, and the SHA-256 key is still read', () => {
  it('the HKDF key is not the SHA-256 key, and matches the Python twin', () => {
    expect(platformCipherKey(STRONG)).to.not.equal(hashedSecret(STRONG));
    // The same vector as test_secret_key_strength.py::test_platform_cipher_key_vector: both languages derive alike.
    expect(platformCipherKey(STRONG)).to.equal(
      '3f9d988e2f25dc0fedd5e25858e5a67fbd3f6b2a3f77501607406e48fa940cec',
    );
  });

  it('writes under HKDF; SECRET_KEY_KDF=sha256 keeps the writer on the old key for a rolling upgrade', () => {
    const ring = secretKeyRing({ PIPESHUB_HARDENED: 'true', SECRET_KEY: STRONG });
    expect(cipherRing(ring, { PIPESHUB_HARDENED: 'true' })[0]).to.equal(platformCipherKey(STRONG));
    expect(cipherRing(ring, { SECRET_KEY_KDF: 'sha256' })[0]).to.equal(hashedSecret(STRONG));
    expect(cipherRing(ring, { SECRET_KEY_KDF: ' SHA256 ' })[0]).to.equal(hashedSecret(STRONG));
  });

  it('a value the SHA-256 key sealed opens, and is reported as not current so rotate moves it', () => {
    const legacy = EncryptionService.withKey('aes-256-gcm', hashedSecret(STRONG)).encrypt('stored');
    const reader = EncryptionService.withRing('aes-256-gcm', cipherRing(secretKeyRing({ PIPESHUB_HARDENED: 'true', SECRET_KEY: STRONG }), { PIPESHUB_HARDENED: 'true' }) as [string, ...string[]]);
    expect(reader.decryptDetailed(legacy)).to.deep.equal({ plaintext: 'stored', current: false });
    expect(reader.decryptDetailed(reader.encrypt('new'))).to.deep.equal({ plaintext: 'new', current: true });
  });

  it('a value under another deployment key opens under neither', () => {
    const foreign = EncryptionService.withKey('aes-256-gcm', platformCipherKey(OTHER)).encrypt('x');
    const reader = EncryptionService.withRing('aes-256-gcm', cipherRing(secretKeyRing({ PIPESHUB_HARDENED: 'true', SECRET_KEY: STRONG }), { PIPESHUB_HARDENED: 'true' }) as [string, ...string[]]);
    expect(() => reader.decrypt(foreign)).to.throw();
  });
});

describe('SEC-D-05: rotate moves what the old SHA-256 cipher key sealed, with no previous key and no downtime', () => {
  class Kv {
    readonly values = new Map<string, string>();
    async getAllKeys(): Promise<string[]> {
      return [...this.values.keys()];
    }
    async get<T>(key: string): Promise<T | null> {
      return (this.values.get(key) ?? null) as T | null;
    }
    async compareAndSet<T>(key: string, expected: T | null, next: T): Promise<boolean> {
      if ((this.values.get(key) ?? null) !== expected) return false;
      this.values.set(key, next as unknown as string);
      return true;
    }
  }
  const lease = { tryAcquire: async () => true, release: async () => undefined };

  it('dry-run counts it, rotate moves it, verify then finds nothing under the old key, and retire is allowed', async () => {
    const kv = new Kv();
    const legacy = EncryptionService.withKey('aes-256-gcm', hashedSecret(STRONG));
    kv.values.set('/services/aiModels', legacy.encrypt('{"k":"v"}'));
    const ring = cipherRing(secretKeyRing({ PIPESHUB_HARDENED: 'true', SECRET_KEY: STRONG }), { PIPESHUB_HARDENED: 'true' }) as [string, ...string[]];
    const cipher = EncryptionService.withRing('aes-256-gcm', ring);
    kv.values.set('/services/new', cipher.encrypt('{"n":1}'));
    const rotation = (other: 'previous' | null, cipherMove: boolean) =>
      new SecretKeyRotation({
        steps: [new ConfigStoreReencryptStep(kv, cipher)],
        ring: { other, cipherMove },
        lease,
        holder: 'op',
      });
    expect((await rotation(null, false).run('rotate')).refused).to.match(/^SECRET_KEY_PREVIOUS is not set/);
    const dry = await rotation(null, true).run('dry-run');
    expect(dry.steps[0]).to.include({ previous: 1, current: 1 });
    const moved = await rotation(null, true).run('rotate');
    expect(moved.ok).to.equal(true);
    expect(cipher.decryptDetailed(kv.values.get('/services/aiModels')!)).to.deep.equal({ plaintext: '{"k":"v"}', current: true });
    expect((await rotation(null, true).run('verify')).ok).to.equal(true);
    expect((await rotation(null, true).run('retire')).ok).to.equal(true);
  });
});
