import test from 'node:test';
import * as assert from 'node:assert/strict';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { SessionVault, type SealingKeychain } from '../session-vault';

const keychain = (available = true): SealingKeychain => ({
  isEncryptionAvailable: () => available,
  encryptString: (plain) => Buffer.from(`sealed:${Buffer.from(plain).toString('base64')}`),
  decryptString: (sealed) => Buffer.from(sealed.toString().replace(/^sealed:/, ''), 'base64').toString(),
});

const tempFile = () => path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'vault-')), 'nested', 'session-refresh.bin');

test('the refresh token round-trips sealed, and the file never holds it in clear', () => {
  const file = tempFile();
  const vault = new SessionVault(file, keychain());
  assert.equal(vault.read(), null);
  assert.equal(vault.write('refresh-abc'), true);
  assert.equal(vault.read(), 'refresh-abc');
  assert.ok(!fs.readFileSync(file).toString().includes('refresh-abc'));
  assert.equal(new SessionVault(file, keychain()).read(), 'refresh-abc', 'survives a restart');
  if (process.platform !== 'win32') assert.equal(fs.statSync(file).mode & 0o777, 0o600);
});

test('sign-out clears it, and clearing twice is harmless', () => {
  const file = tempFile();
  const vault = new SessionVault(file, keychain());
  vault.write('refresh-abc');
  vault.clear();
  vault.clear();
  assert.equal(vault.read(), null);
  assert.equal(fs.existsSync(file), false);
});

test('without a keychain nothing is written to disk; the token lasts this process only', () => {
  const file = tempFile();
  const vault = new SessionVault(file, keychain(false));
  assert.equal(vault.write('refresh-abc'), true);
  assert.equal(vault.read(), 'refresh-abc');
  assert.equal(fs.existsSync(file), false);
  assert.equal(new SessionVault(file, keychain(false)).read(), null);
});

test('an empty or non-string token is refused, and an unreadable file reads as signed out', () => {
  const file = tempFile();
  const vault = new SessionVault(file, keychain());
  assert.equal(vault.write(''), false);
  assert.equal(vault.write(42), false);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, 'garbage');
  const broken = new SessionVault(file, { ...keychain(), decryptString: () => { throw new Error('locked'); } });
  assert.equal(broken.read(), null);
});

test('a keyed session (an edition\'s, by organisation) is sealed beside the one session and never mixed with it', () => {
  const file = tempFile();
  const vault = new SessionVault(file, keychain());
  assert.equal(vault.write('one-session'), true);
  assert.equal(vault.write('org-a-refresh', 'org_A-1'), true);
  assert.equal(vault.write('org-b-refresh', 'orgB'), true);
  assert.equal(vault.read(), 'one-session');
  assert.equal(vault.read('org_A-1'), 'org-a-refresh');
  assert.equal(new SessionVault(file, keychain()).read('orgB'), 'org-b-refresh', 'survives a restart');
  assert.ok(!fs.readFileSync(`${file}.orgB`).toString().includes('org-b-refresh'));
  assert.deepEqual(vault.keys(), ['orgB', 'org_A-1']);
  vault.clear('orgB');
  assert.equal(vault.read('orgB'), null);
  assert.equal(vault.read(), 'one-session', 'clearing a key leaves the one session');
});

test('a key that is not an id names no session: it reads nothing, writes nothing, clears nothing', () => {
  const file = tempFile();
  const vault = new SessionVault(file, keychain());
  vault.write('one-session');
  for (const key of ['../escape', 'a.b', '', 'x'.repeat(65), 42, { org: 'a' }]) {
    assert.equal(vault.write('refresh', key), false, String(key));
    assert.equal(vault.read(key), null, String(key));
    vault.clear(key);
  }
  assert.equal(vault.read(), 'one-session');
  assert.deepEqual(fs.readdirSync(path.dirname(file)), ['session-refresh.bin']);
});

test('clearAll ends every session, keyed or not; without a keychain keyed sessions last this process only', () => {
  const file = tempFile();
  const vault = new SessionVault(file, keychain());
  vault.write('one-session');
  vault.write('org-a', 'a');
  vault.clearAll();
  assert.equal(vault.read(), null);
  assert.deepEqual(vault.keys(), []);
  const memory = new SessionVault(tempFile(), keychain(false));
  memory.write('org-a', 'a');
  assert.equal(memory.read('a'), 'org-a');
  assert.deepEqual(memory.keys(), ['a']);
  memory.clearAll();
  assert.equal(memory.read('a'), null);
});
