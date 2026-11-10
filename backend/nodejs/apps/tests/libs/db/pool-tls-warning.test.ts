import { expect } from 'chai';
import { plaintextRemoteWarning } from '../../../src/libs/db/pool';

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
    url('/x?host=/var/run/postgresql'),
    url('db.example.com/x?sslmode=verify-full'),
    url('db.example.com/x?sslmode=require'),
    'not a url',
  ]) {
    it(`is silent for ${u.replace(secret, '***')}`, () => {
      expect(plaintextRemoteWarning('node', u)).to.equal(undefined);
    });
  }
});
