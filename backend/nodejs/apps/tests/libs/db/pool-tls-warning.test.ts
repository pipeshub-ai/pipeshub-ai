import { expect } from 'chai';
import sinon from 'sinon';
import {
  createPgPool,
  plaintextRemoteWarning,
} from '../../../src/libs/db/pool';
import { Logger } from '../../../src/libs/services/logger.service';

const secret = 's3cretpw';
const url = (rest: string) => `postgresql://app:${secret}@${rest}`;

describe('plaintextRemoteWarning', () => {
  for (const q of [
    '',
    '?sslmode=disable',
    '?sslmode=prefer',
    '?sslmode=allow',
  ]) {
    it(`warns for a remote host with ${q || 'no sslmode'}, without the URL`, () => {
      const w = plaintextRemoteWarning(
        'node',
        url(`db.example.com:5432/x${q}`),
      );
      expect(w).to.include('node');
      expect(w).to.not.include(secret);
      expect(w).to.not.include('db.example.com');
    });
  }

  for (const u of [
    url('localhost/x'),
    url('127.0.0.1/x'),
    url('[::1]/x'),
    url('db.example.com/x?host=/var/run/postgresql'),
    url('db.example.com/x?sslmode=verify-full'),
    url('db.example.com/x?sslmode=require'),
    'not a url',
  ]) {
    it(`is silent for ${u.replace(secret, '***')}`, () => {
      expect(plaintextRemoteWarning('node', u)).to.equal(undefined);
    });
  }
});

describe('createPgPool TLS warning', () => {
  let warn: sinon.SinonStub;
  beforeEach(() => {
    warn = sinon.stub(Logger.prototype, 'warn');
  });
  afterEach(() => sinon.restore());

  // Service names are unique per test: the once-per-process memory is not reset between tests.
  const open = (service: string, rest: string) =>
    createPgPool({ connectionString: url(rest), service, version: 't' }).end();

  const tlsWarnings = () =>
    warn.getCalls().filter((c) => String(c.args[0]).includes('without TLS'));

  for (const host of ['pgbouncer', '10.0.0.5', 'db.example.com']) {
    it(`warns for implicit prefer on ${host}`, async () => {
      await open(`implicit-${host}`, `${host}:5432/x`);
      expect(tlsWarnings()).to.have.length(1);
    });
  }

  it('warns once per process for the same service and URL', async () => {
    for (let i = 0; i < 5; i++) await open('once-same', 'pgbouncer:6432/x');
    expect(tlsWarnings()).to.have.length(1);
  });

  it('still warns for another service, host or sslmode', async () => {
    await open('once-a', 'pgbouncer/x');
    await open('once-a', 'pgbouncer/x');
    await open('once-b', 'pgbouncer/x');
    await open('once-a', 'db.example.com/x');
    await open('once-a', 'pgbouncer/x?sslmode=disable');
    expect(tlsWarnings()).to.have.length(4);
  });

  it('is silent for localhost, a socket host or TLS', async () => {
    await open('silent', 'localhost/x');
    await open('silent', 'db.example.com/x?host=/var/run/postgresql');
    await open('silent', 'db.example.com/x?sslmode=require');
    await open('silent', 'db.example.com/x?sslmode=verify-full');
    expect(tlsWarnings()).to.have.length(0);
  });
});
