import { describe, expect, it } from 'vitest';
import {
  collectImportCandidates,
  inviteOutcome,
  isSupportedInviteImportFile,
  validateEmail,
} from '../invite-email';

describe('validateEmail', () => {
  it.each([
    'alice@acme.com',
    'first.last+tag@sub.example.co.uk',
    "o'brien@example.org",
    'jose@bücher.de',
  ])('accepts %s', (email) => {
    expect(validateEmail(email)).toBeNull();
  });

  it.each([
    // What a =HYPERLINK("http://evil","user@example.com") cell becomes once split at its comma.
    '"user@example.com")',
    'a,b@example.com',
    'a;b@example.com',
    '<a@example.com>',
    'a b@example.com',
    '(a)@example.com',
    'a@example',
    'a@example.c',
    'a@@example.com',
  ])('refuses %s', (email) => {
    expect(validateEmail(email)).not.toBeNull();
  });

  it('refuses an address longer than 254 characters', () => {
    expect(validateEmail(`${'a'.repeat(64)}@${'b'.repeat(186)}.com`)).not.toBeNull();
    expect(validateEmail(`${'a'.repeat(64)}@${'b'.repeat(185)}.com`)).toBeNull();
  });
});

describe('isSupportedInviteImportFile', () => {
  it.each(['users.csv', 'Users.XLSX', 'old.xls'])('accepts %s', (name) => {
    expect(isSupportedInviteImportFile(name)).toBe(true);
  });

  it.each(['users.txt', 'users.csv.txt', 'users', 'xlsx'])('refuses %s', (name) => {
    expect(isSupportedInviteImportFile(name)).toBe(false);
  });
});

describe('collectImportCandidates', () => {
  it('counts cells too long to be an address instead of dropping them silently', () => {
    const long = `${'a'.repeat(250)}@example.com`;
    const { found, tooLong } = collectImportCandidates(
      [['email'], ['a@x.com'], [long, null, 'name'], [' b@x.com ']],
      10,
    );
    expect(found).toEqual(['a@x.com', 'b@x.com']);
    expect(tooLong).toBe(1);
  });

  it('stops collecting one past the limit so the caller can report the overflow', () => {
    const rows = Array.from({ length: 20 }, (_, i) => [`u${i}@x.com`]);
    expect(collectImportCandidates(rows, 5).found).toHaveLength(6);
  });
});

describe('inviteOutcome', () => {
  it('adds new, re-invited and restored accounts, and reports the members skipped', () => {
    expect(
      inviteOutcome(5, { invited: 1, reinvited: 1, restored: 1, alreadyActive: 2 }),
    ).toEqual({ sent: 3, alreadyMembers: 2 });
  });

  it('assumes every address was invited when the server sends no counts', () => {
    expect(inviteOutcome(4, {})).toEqual({ sent: 4, alreadyMembers: 0 });
  });
});
