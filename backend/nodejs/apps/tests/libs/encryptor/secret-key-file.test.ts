import { expect } from 'chai';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
  ringSecret,
  secretKeyConfigured,
  SecretKeyRingError,
  secretKeyRing,
} from '../../../src/libs/encryptor/secret-key-ring';

/**
 * R38IDENT-FU-8 (ADR-KEY-02 D8): each SECRET_KEY ring member may come from a
 * file (a Docker or Kubernetes secret). The file wins over the variable and
 * loses one trailing line break; a file that cannot be read or is empty stops
 * the start with the variable and the path, never the content. Python's twin
 * is tests/unit/utils/encryption/test_secret_key_file.py.
 */
const STRONG = crypto.createHash('sha256').update('fu-8 strong').digest('hex');
const OTHER = crypto.createHash('sha256').update('fu-8 other').digest('hex');

describe('SECRET_KEY from a file (R38IDENT-FU-8)', () => {
  let dir: string;
  const keyFile = (content: string, name = 'secret-key', mode = 0o400): string => {
    const file = path.join(dir, name);
    fs.writeFileSync(file, content, { encoding: 'utf8' });
    fs.chmodSync(file, mode);
    return file;
  };

  beforeEach(() => {
    dir = fs.mkdtempSync(path.join(os.tmpdir(), 'pxh-secret-key-file-'));
  });
  afterEach(() => {
    fs.rmSync(dir, { recursive: true, force: true });
  });

  it('reads the file, which wins over the variable', () => {
    const ring = secretKeyRing({ PIPESHUB_HARDENED: 'true', SECRET_KEY_FILE: keyFile(`${STRONG}\n`), SECRET_KEY: OTHER });
    expect(ring.current).to.equal(STRONG);
    expect(ring.other).to.equal(null);
  });

  it('still reads the variable without a file', () => {
    expect(secretKeyRing({ PIPESHUB_HARDENED: 'true', SECRET_KEY: STRONG }).current).to.equal(STRONG);
  });

  for (const ending of ['', '\n', '\r\n']) {
    it(`drops one trailing line break (${JSON.stringify(ending)})`, () => {
      expect(ringSecret('SECRET_KEY', { SECRET_KEY_FILE: keyFile(STRONG + ending) })).to.equal(STRONG);
    });
  }

  for (const content of [` ${STRONG}\n`, `${STRONG}\n\n`, `${STRONG} \n`, `\t${STRONG}`]) {
    it(`refuses other whitespace as the key's own (${JSON.stringify(content.replace(STRONG, 'K'))})`, () => {
      expect(() => secretKeyRing({ PIPESHUB_HARDENED: 'true', SECRET_KEY_FILE: keyFile(content) })).to.throw(
        SecretKeyRingError,
        'starts or ends with a space or line break',
      );
    });
  }

  it('names the variable and the path of a missing file', () => {
    const missing = path.join(dir, 'nope');
    expect(() => secretKeyRing({ PIPESHUB_HARDENED: 'true', SECRET_KEY_FILE: missing, SECRET_KEY: STRONG })).to.throw(
      SecretKeyRingError,
      `SECRET_KEY_FILE names ${missing}, which cannot be read (ENOENT)`,
    );
  });

  it('refuses an empty file rather than reading it as unset', () => {
    const file = keyFile('\n');
    expect(() => secretKeyRing({ PIPESHUB_HARDENED: 'true', SECRET_KEY_FILE: file, SECRET_KEY: STRONG })).to.throw(
      SecretKeyRingError,
      `SECRET_KEY_FILE names ${file}, which is empty`,
    );
  });

  it('refuses an unreadable file and never shows the key', function () {
    if (process.getuid?.() === 0) this.skip();
    const file = keyFile(STRONG, 'locked', 0o000);
    try {
      secretKeyRing({ PIPESHUB_HARDENED: 'true', SECRET_KEY_FILE: file });
      expect.fail('read a mode-000 file');
    } catch (error) {
      expect(error).to.be.instanceOf(SecretKeyRingError);
      expect((error as Error).message).to.contain('EACCES').and.not.contain(STRONG);
    }
  });

  it('refuses a weak key in a file as it does in the variable', () => {
    expect(() =>
      secretKeyRing({ PIPESHUB_HARDENED: 'true', SECRET_KEY_FILE: keyFile('your_random_encryption_secret_key\n') }),
    ).to.throw(SecretKeyRingError, 'SECRET_KEY is a published placeholder');
  });

  it('reads every ring member from a file', () => {
    const current = keyFile(STRONG, 'current');
    const previous = secretKeyRing({
      SECRET_KEY_FILE: current,
      SECRET_KEY_PREVIOUS_FILE: keyFile('weak-old-key\n', 'previous'),
    });
    expect(previous.other).to.deep.equal({ role: 'previous', secret: 'weak-old-key' });
    const next = keyFile(OTHER, 'next');
    expect(secretKeyRing({ PIPESHUB_HARDENED: 'true', SECRET_KEY_FILE: current, SECRET_KEY_NEXT_FILE: next }).other).to.deep.equal({
      role: 'next',
      secret: OTHER,
    });
    expect(() =>
      secretKeyRing({ PIPESHUB_HARDENED: 'true', SECRET_KEY_FILE: current, SECRET_KEY_NEXT_FILE: next, SECRET_KEY_PREVIOUS: 'old' }),
    ).to.throw(SecretKeyRingError, 'never both');
  });

  it('warns about a file every account can read, naming the path and not the key', () => {
    const file = keyFile(STRONG, 'world', 0o644);
    const warnings: string[] = [];
    const original = process.emitWarning;
    process.emitWarning = ((warning: string | Error) => {
      warnings.push(String(warning));
    }) as typeof process.emitWarning;
    try {
      expect(secretKeyRing({ PIPESHUB_HARDENED: 'true', SECRET_KEY_FILE: file }).current).to.equal(STRONG);
    } finally {
      process.emitWarning = original;
    }
    expect(warnings).to.have.length(1);
    expect(warnings[0]).to.contain(file).and.contain('every account can read').and.not.contain(STRONG);
  });

  it('counts a configured file as a configured key', () => {
    expect(secretKeyConfigured({})).to.equal(false);
    expect(secretKeyConfigured({ SECRET_KEY: '' })).to.equal(false);
    expect(secretKeyConfigured({ SECRET_KEY: '', SECRET_KEY_FILE: '/run/secrets/k' })).to.equal(true);
    expect(secretKeyConfigured({ SECRET_KEY: STRONG })).to.equal(true);
  });
});
