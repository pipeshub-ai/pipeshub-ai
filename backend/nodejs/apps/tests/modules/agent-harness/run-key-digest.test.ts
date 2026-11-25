import { expect } from 'chai';
import { createHash } from 'node:crypto';
import { HmacRunKeyDigest, isRunKeyDigest } from '../../../src/modules/agent-harness/domain/run-key-digest';
import { platformRunKeyDigest } from '../../../src/modules/agent-harness/adapters/crypto/platform-run-key-digest';

const secret = (byte: number) => () => Buffer.alloc(32, byte);

describe('QA15F-1 (ERA-26): the run key digest', () => {
  const digest = new HmacRunKeyDigest(secret(1));

  it('is the same for the same org and key, and differs with the org, the key or the secret', () => {
    const one = digest.digest('org-1', 'retry-1');
    expect(new HmacRunKeyDigest(secret(1)).digest('org-1', 'retry-1')).to.equal(one);
    expect(digest.digest('org-2', 'retry-1')).to.not.equal(one);
    expect(digest.digest('org-1', 'retry-2')).to.not.equal(one);
    expect(new HmacRunKeyDigest(secret(2)).digest('org-1', 'retry-1')).to.not.equal(one);
  });

  it('is keyed: not a plain hash of the key, and no part of the key shows through', () => {
    const key = 'SENTINEL-caller-text:4f1d';
    const stored = digest.digest('org-1', key);
    expect(stored).to.match(/^hk1:[0-9a-f]{64}$/);
    expect(stored).to.not.include(key);
    expect(stored).to.not.include(createHash('sha256').update(key).digest('hex'));
    expect(stored).to.not.include(createHash('sha256').update(JSON.stringify(['org-1', key])).digest('hex'));
  });

  it('keeps an org and key pair apart from one whose parts join to the same text', () => {
    expect(digest.digest('org-1', ':a')).to.not.equal(digest.digest('org-1:', 'a'));
  });

  it('tells a digest from a key an older build stored', () => {
    expect(isRunKeyDigest(digest.digest('org-1', 'k'))).to.equal(true);
    for (const raw of ['k', 'child:run-1:inv-1', 'next-run:abc', `hk1:${'a'.repeat(63)}`, `hk1:${'A'.repeat(64)}`]) {
      expect(isRunKeyDigest(raw), raw).to.equal(false);
    }
  });

  it("the platform digest is the deployment SECRET_KEY's, read on first use: every replica derives the same one", () => {
    const saved = process.env.SECRET_KEY;
    try {
      delete process.env.SECRET_KEY;
      expect(() => platformRunKeyDigest().digest('org-1', 'k')).to.throw('SECRET_KEY environment variable is required');
      process.env.SECRET_KEY = '4a548e7c602a2872ec983e28e3e80d5fc8aa81bf422510d120daaba403c7988d';
      const one = platformRunKeyDigest().digest('org-1', 'k');
      expect(platformRunKeyDigest().digest('org-1', 'k')).to.equal(one);
      expect(new HmacRunKeyDigest(() => createHash('sha256').update('4a548e7c602a2872ec983e28e3e80d5fc8aa81bf422510d120daaba403c7988d').digest()).digest('org-1', 'k')).to.equal(one);
      process.env.SECRET_KEY = 'ce1807e913c97047dafef68295a8968c894cc8d1d8cc2c38fa84e06e7d5c0f06';
      expect(platformRunKeyDigest().digest('org-1', 'k')).to.not.equal(one);
    } finally {
      if (saved === undefined) delete process.env.SECRET_KEY;
      else process.env.SECRET_KEY = saved;
    }
  });
});
