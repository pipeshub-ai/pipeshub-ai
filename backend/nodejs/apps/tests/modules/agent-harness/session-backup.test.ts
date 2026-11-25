import { expect } from 'chai';
import sinon from 'sinon';
import { createHash } from 'crypto';
import {
  archiveFileDigest,
  manifestDigest,
  SessionBackupRefused,
} from '../../../src/modules/agent-harness/domain/session-backup';
import { runSessionBackupCli } from '../../../src/modules/agent-harness/ops/session-backup-cli';

const sha256 = (text: string): string => createHash('sha256').update(text).digest('hex');

describe('agent-harness session backup (P9), without Mongo', () => {
  afterEach(() => sinon.restore());

  it('the manifest digest does not depend on the order collections were listed in', () => {
    const a = { sessionEvents: { documents: 3, sha256: 'aa' }, sessions: { documents: 1, sha256: 'bb' } };
    const b = { sessions: { documents: 1, sha256: 'bb' }, sessionEvents: { documents: 3, sha256: 'aa' } };
    expect(manifestDigest(a, sha256)).to.equal(manifestDigest(b, sha256));
    expect(manifestDigest(a, sha256)).to.equal(sha256('sessionEvents:aa\nsessions:bb'));
  });

  it('one collection file changing changes the digest; a document count alone does not', () => {
    const base = { sessions: { documents: 1, sha256: 'bb' } };
    expect(manifestDigest({ sessions: { documents: 1, sha256: 'bc' } }, sha256)).to.not.equal(
      manifestDigest(base, sha256),
    );
    expect(manifestDigest({ sessions: { documents: 2, sha256: 'bb' } }, sha256)).to.equal(
      manifestDigest(base, sha256),
    );
  });

  it("a file's digest is the sha256 of its UTF-8 text, the one every backend checks on restore", () => {
    const text = '{"title":"Café ☕"}\n';
    expect(archiveFileDigest(text)).to.equal(createHash('sha256').update(Buffer.from(text, 'utf8')).digest('hex'));
    expect(archiveFileDigest(text)).to.not.equal(archiveFileDigest(text.normalize('NFD')));
  });

  it('a refusal carries its reason code', () => {
    const refused = new SessionBackupRefused('session_exists', 'already here');
    expect([refused.name, refused.reasonCode, refused.message]).to.deep.equal([
      'SessionBackupRefused',
      'session_exists',
      'already here',
    ]);
  });

  for (const [label, argv] of [
    ['no command', []],
    ['an unknown command', ['copy', '--mongo-uri', 'mongodb://x', '--db', 'd']],
    ['export without its session', ['export', '--mongo-uri', 'mongodb://x', '--db', 'd', '--org', 'o', '--out', '/tmp/x']],
    ['restore without its archive', ['restore', '--mongo-uri', 'mongodb://x', '--db', 'd']],
    ['a flag without a value', ['restore', '--mongo-uri']],
  ] as Array<[string, string[]]>) {
    it(`the CLI answers 2 for a usage error before connecting: ${label}`, async () => {
      const stderr = sinon.stub(process.stderr, 'write').returns(true);
      const code = await runSessionBackupCli(argv);
      stderr.restore();
      expect(code).to.equal(2);
      expect(stderr.callCount).to.equal(1);
    });
  }
});
